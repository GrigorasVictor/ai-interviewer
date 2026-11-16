"""NLP analyzers for per-answer sentiment, session sentiment, and keyphrase extraction.

Uses Hugging Face `transformers` / `KeyBERT` when installed, and automatically falls back
to NLTK `VADER` (`vader_lexicon`) + TF-scored n-gram keyphrases + Ollama embeddings so the
pipeline works out-of-the-box offline or in lightweight environments.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from contracts import SKIPPED_ANSWER, Keyword, Sentiment

_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "everything",
    "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "itself", "just", "me", "more", "most", "Mostly", "mostly", "my", "myself", "no", "nor",
    "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves",
    "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "twice", "u", "under", "until", "up", "very", "was", "we", "well",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with",
    "would", "you", "your", "yours", "yourself", "yourselves",
}


class SentimentEngine:
    """Computes `Sentiment(label, score)` per answer and aggregates session sentiment."""

    def __init__(self, preferred_model: str = "cardiffnlp/twitter-roberta-base-sentiment-latest") -> None:
        self.preferred_model = preferred_model
        self.model_name = "nltk/vader_lexicon"
        self._hf_pipe: Any = None
        self._vader: Any = None
        self._init_models()

    def _init_models(self) -> None:
        try:
            from transformers import pipeline  # type: ignore[import-not-found]

            self._hf_pipe = pipeline(
                "text-classification",
                model=self.preferred_model,
                truncation=True,
                max_length=512,
                local_files_only=True,
            )
            self.model_name = self.preferred_model
            return
        except Exception:
            self._hf_pipe = None

        try:
            import nltk
            from nltk.sentiment import SentimentIntensityAnalyzer

            try:
                self._vader = SentimentIntensityAnalyzer()
            except LookupError:
                nltk.download("vader_lexicon", quiet=True)
                self._vader = SentimentIntensityAnalyzer()
            self.model_name = "nltk/vader_lexicon"
        except Exception:
            self._vader = None
            self.model_name = "lexicon-fallback"

    def analyze_text(self, text: str) -> Sentiment:
        cleaned = (text or "").strip()
        if not cleaned or cleaned == SKIPPED_ANSWER:
            return Sentiment(label="neutral", score=1.0)

        if self._hf_pipe is not None:
            raw = self._hf_pipe(cleaned[:5000])[0]
            lbl = str(raw["label"]).lower()
            label_map = {
                "label_0": "negative",
                "label_1": "neutral",
                "label_2": "positive",
                "negative": "negative",
                "neutral": "neutral",
                "positive": "positive",
            }
            norm_label = label_map.get(lbl, "neutral")
            return Sentiment(label=norm_label, score=round(float(raw["score"]), 3))  # type: ignore[arg-type]

        if self._vader is not None:
            scores = self._vader.polarity_scores(cleaned)
            compound = float(scores["compound"])
            if compound >= 0.15:
                return Sentiment(label="positive", score=round(min(1.0, 0.5 + abs(compound) / 2), 3))
            if compound <= -0.15:
                return Sentiment(label="negative", score=round(min(1.0, 0.5 + abs(compound) / 2), 3))
            return Sentiment(label="neutral", score=round(max(float(scores["neu"]), 1.0 - abs(compound)), 3))

        return Sentiment(label="neutral", score=0.5)

    @staticmethod
    def aggregate_session_sentiment(sentiments: list[Sentiment]) -> Sentiment:
        """Deterministic session sentiment aggregation (per `MCP_SERVICE.md` §3.1)."""
        if not sentiments:
            return Sentiment(label="neutral", score=1.0)

        signed_values: list[float] = []
        for s in sentiments:
            sign = 1.0 if s.label == "positive" else (-1.0 if s.label == "negative" else 0.0)
            signed_values.append(float(s.score) * sign)

        mean_signed = sum(signed_values) / len(signed_values)
        if mean_signed > 0.25:
            return Sentiment(label="positive", score=round(abs(mean_signed), 3))
        if mean_signed < -0.25:
            return Sentiment(label="negative", score=round(abs(mean_signed), 3))
        return Sentiment(label="neutral", score=round(1.0 - abs(mean_signed), 3))


class KeywordEngine:
    """Extracts 3-5 lowercase keyphrases per answer (`Keyword(phrase, score)`)."""

    def __init__(self, preferred_model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.preferred_model = preferred_model
        self.model_name = "ngram-tf-keyphrase"
        self._kw_model: Any = None
        self._init_model()

    def _init_model(self) -> None:
        try:
            from keybert import KeyBERT  # type: ignore[import-not-found]

            self._kw_model = KeyBERT(model=self.preferred_model)
            self.model_name = self.preferred_model
        except Exception:
            self._kw_model = None

    def extract(self, text: str, top_n: int = 5) -> list[Keyword]:
        cleaned = (text or "").strip()
        if not cleaned or cleaned == SKIPPED_ANSWER:
            return []

        words = [
            w
            for w in re.findall(r"[a-zA-Z0-9]+", cleaned.lower())
            if len(w) >= 3 and w not in _STOPWORDS
        ]
        if not words:
            return []

        if len(cleaned.split()) < 4:
            seen_short: list[str] = []
            for w in words:
                if w not in seen_short:
                    seen_short.append(w)
            return [Keyword(phrase=w, score=0.0) for w in seen_short[:top_n]]

        if self._kw_model is not None:
            try:
                raw_kws = self._kw_model.extract_keywords(
                    cleaned,
                    keyphrase_ngram_range=(1, 2),
                    stop_words="english",
                    use_mmr=True,
                    diversity=0.5,
                    top_n=top_n,
                )
                return [
                    Keyword(phrase=phrase.lower().strip(), score=round(float(score), 3))
                    for phrase, score in raw_kws
                    if len(phrase.strip()) >= 3
                ][:top_n]
            except Exception:
                pass

        # Deterministic n-gram keyphrase scoring (bigrams + unigrams)
        candidates: list[str] = []
        tokens = re.findall(r"[a-zA-Z0-9]+", cleaned.lower())
        for i in range(len(tokens) - 1):
            w1, w2 = tokens[i], tokens[i + 1]
            if w1 not in _STOPWORDS and w2 not in _STOPWORDS and len(w1) >= 3 and len(w2) >= 3:
                candidates.append(f"{w1} {w2}")
        for w in words:
            candidates.append(w)

        counts = Counter(candidates)
        total = max(1, sum(counts.values()))
        scored: list[tuple[str, float]] = []
        used_words: set[str] = set()
        for phrase, freq in counts.most_common():
            phrase_tokens = set(phrase.split())
            if phrase_tokens & used_words and len(scored) >= 3:
                continue
            used_words |= phrase_tokens
            bonus = 1.25 if " " in phrase else 1.0
            raw_score = min(0.95, round(math.sqrt(freq / total) * bonus, 3))
            scored.append((phrase, raw_score))
            if len(scored) >= top_n:
                break

        return [Keyword(phrase=p, score=s) for p, s in scored]
