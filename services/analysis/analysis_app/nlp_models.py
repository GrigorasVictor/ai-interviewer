"""NLP analyzers for per-answer sentiment, session sentiment, and keyphrase extraction."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import nltk
from contracts import SKIPPED_ANSWER, Keyword, Sentiment, SentimentLabel

SIGN_BY_LABEL: dict[str, float] = {
    "positive": 1.0,
    "neutral": 0.0,
    "negative": -1.0,
}

HF_LABEL_MAP: dict[str, SentimentLabel] = {
    "label_0": "negative",
    "label_1": "neutral",
    "label_2": "positive",
    "negative": "negative",
    "neutral": "neutral",
    "positive": "positive",
}


def _load_english_stopwords() -> set[str]:
    try:
        from nltk.corpus import stopwords

        return set(stopwords.words("english"))
    except LookupError:
        nltk.download("stopwords", quiet=True)
        from nltk.corpus import stopwords

        return set(stopwords.words("english"))


def _classify_signed_score(value: float, threshold: float, neutral_score: float) -> Sentiment:
    """Table-driven classification of a signed polarity value in [-1, 1]."""
    rules: tuple[tuple[bool, SentimentLabel, float], ...] = (
        (value > threshold, "positive", abs(value)),
        (value < -threshold, "negative", abs(value)),
    )
    label, score = next(
        ((lbl, sc) for matched, lbl, sc in rules if matched),
        ("neutral", neutral_score),
    )
    return Sentiment(label=label, score=round(min(1.0, max(0.0, score)), 3))


class SentimentEngine:
    """Computes `Sentiment(label, score)` per answer and aggregates session sentiment."""

    def __init__(self, preferred_model: str = "cardiffnlp/twitter-roberta-base-sentiment-latest") -> None:
        self.preferred_model = preferred_model
        self.model_name = "nltk/vader_lexicon"
        self._analyzer = self._build_analyzer()

    def _build_analyzer(self):
        try:
            from transformers import pipeline  # type: ignore[import-not-found]

            hf_pipe = pipeline(
                "text-classification",
                model=self.preferred_model,
                truncation=True,
                max_length=512,
                local_files_only=True,
            )
            self.model_name = self.preferred_model
            return lambda text: self._score_hf(hf_pipe, text)
        except Exception:
            pass

        from nltk.sentiment import SentimentIntensityAnalyzer

        try:
            vader = SentimentIntensityAnalyzer()
        except LookupError:
            nltk.download("vader_lexicon", quiet=True)
            vader = SentimentIntensityAnalyzer()
        self.model_name = "nltk/vader_lexicon"
        return lambda text: self._score_vader(vader, text)

    @staticmethod
    def _score_hf(hf_pipe: Any, text: str) -> Sentiment:
        raw = hf_pipe(text[:5000])[0]
        label = HF_LABEL_MAP.get(str(raw["label"]).lower(), "neutral")
        return Sentiment(label=label, score=round(float(raw["score"]), 3))

    @staticmethod
    def _score_vader(vader: Any, text: str) -> Sentiment:
        scores = vader.polarity_scores(text)
        compound = float(scores["compound"])
        polarized_conf = min(1.0, 0.5 + abs(compound) / 2)
        neutral_conf = max(float(scores["neu"]), 1.0 - abs(compound))
        sentiment = _classify_signed_score(compound, threshold=0.149, neutral_score=neutral_conf)
        return (
            sentiment
            if sentiment.label == "neutral"
            else Sentiment(label=sentiment.label, score=round(polarized_conf, 3))
        )

    def analyze_text(self, text: str) -> Sentiment:
        cleaned = (text or "").strip()
        if not cleaned or cleaned == SKIPPED_ANSWER:
            return Sentiment(label="neutral", score=1.0)
        return self._analyzer(cleaned)

    @staticmethod
    def aggregate_session_sentiment(sentiments: list[Sentiment]) -> Sentiment:
        """Deterministic session sentiment aggregation."""
        if not sentiments:
            return Sentiment(label="neutral", score=1.0)
        signed_values = [float(s.score) * SIGN_BY_LABEL[s.label] for s in sentiments]
        mean_signed = sum(signed_values) / len(signed_values)
        return _classify_signed_score(mean_signed, threshold=0.25, neutral_score=1.0 - abs(mean_signed))


class KeywordEngine:
    """Extracts up to `top_n` lowercase keyphrases per answer (`Keyword(phrase, score)`)."""

    def __init__(self, preferred_model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.preferred_model = preferred_model
        self.model_name = "ngram-tf-keyphrase"
        self._stopwords = _load_english_stopwords()
        self._extractor = self._build_extractor()

    def _build_extractor(self):
        try:
            from keybert import KeyBERT  # type: ignore[import-not-found]

            kw_model = KeyBERT(model=self.preferred_model)
            self.model_name = self.preferred_model
            return lambda text, top_n: self._extract_keybert(kw_model, text, top_n)
        except Exception:
            return self._extract_ngrams

    def _extract_keybert(self, kw_model: Any, text: str, top_n: int) -> list[Keyword]:
        raw_kws = kw_model.extract_keywords(
            text,
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

    def _extract_ngrams(self, text: str, top_n: int) -> list[Keyword]:
        tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
        words = [w for w in tokens if len(w) >= 3 and w not in self._stopwords]
        if not words:
            return []
        if len(text.split()) < 4:
            return [Keyword(phrase=w, score=0.0) for w in list(dict.fromkeys(words))[:top_n]]

        bigrams = [
            f"{w1} {w2}"
            for w1, w2 in zip(tokens, tokens[1:])
            if w1 not in self._stopwords and w2 not in self._stopwords and len(w1) >= 3 and len(w2) >= 3
        ]
        counts = Counter([*bigrams, *words])
        total = max(1, sum(counts.values()))

        scored: list[Keyword] = []
        used_words: set[str] = set()
        for phrase, freq in counts.most_common():
            phrase_tokens = set(phrase.split())
            if phrase_tokens & used_words and len(scored) >= 3:
                continue
            used_words |= phrase_tokens
            bonus = 1.25 if " " in phrase else 1.0
            score = min(0.95, round(math.sqrt(freq / total) * bonus, 3))
            scored.append(Keyword(phrase=phrase, score=score))
            if len(scored) >= top_n:
                break
        return scored

    def extract(self, text: str, top_n: int = 5) -> list[Keyword]:
        cleaned = (text or "").strip()
        if not cleaned or cleaned == SKIPPED_ANSWER:
            return []
        return self._extractor(cleaned, top_n)
