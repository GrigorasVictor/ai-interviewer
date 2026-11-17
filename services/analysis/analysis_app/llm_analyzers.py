"""AI analyzers for full-transcript Summary and Emotion / Tone & Actionable Insights.

Uses JSON Schema grammar-constrained structured outputs (`response_format={"type": "json_schema", ...}`)
so Ollama / OpenRouter enforces the schema at token generation time, and combines upstream NLP
(`SentimentEngine` + `KeywordEngine`) and `Summary LLM` signals into a rich multi-model context
for the `Insights LLM`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import litellm
from contracts import (
    INSIGHTS_SYSTEM_PROMPT,
    INSIGHTS_USER_PROMPT,
    SKIPPED_ANSWER,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
    EmotionToneAnalysis,
    InterviewSummary,
    Sentiment,
)
from pydantic import BaseModel

from .config import AnalysisSettings


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in: {raw_text!r}")
    return json.loads(match.group(0))


def _format_turn_telemetry(turn: dict[str, Any]) -> str:
    analysis = turn.get("analysis")
    answer_text = str(turn.get("answer") or "").strip()
    if not analysis or not answer_text or answer_text == SKIPPED_ANSWER:
        return "NLP Telemetry: [skipped by user — excluded from sentiment]"

    sent = analysis.get("sentiment") or {}
    label = sent.get("label", "neutral")
    score = float(sent.get("score", 0.0))
    kws = analysis.get("keywords") or []
    kw_str = (
        ", ".join(f"'{k.get('phrase')}' ({float(k.get('score', 0.0)):.3f})" for k in kws if k.get("phrase"))
        or "none"
    )
    return f"NLP Telemetry: sentiment={label} (score={score:.3f}) | keywords=[{kw_str}]"


def _format_transcript_for_llm(record: dict[str, Any]) -> str:
    blocks: list[str] = []
    for idx, turn in enumerate(record.get("transcript") or [], start=1):
        source = turn.get("source") or "main_queue"
        context = turn.get("context")
        ctx_line = f"\nContext (referenced answer): {context}" if context else ""
        telemetry_line = _format_turn_telemetry(turn)
        blocks.append(
            f"Q{idx} ({source}): {turn.get('question')}{ctx_line}\n"
            f"A{idx}: {turn.get('answer')}\n"
            f"{telemetry_line}"
        )
    return "\n\n".join(blocks)


def _build_multi_model_context(
    record: dict[str, Any],
    session_sentiment: Sentiment,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine predictions from `SentimentEngine`, `KeywordEngine`, and `Summary LLM` into prompt context variables."""
    transcript = list(record.get("transcript") or [])
    counts: Counter[str] = Counter()
    trajectory_steps: list[str] = []
    positive_turns: list[str] = []
    friction_turns: list[str] = []
    skipped_turns: list[str] = []
    keyword_entries: list[tuple[float, str, int, str]] = []

    for idx, turn in enumerate(transcript, start=1):
        source = str(turn.get("source") or "main_queue")
        analysis = turn.get("analysis")
        answer_text = str(turn.get("answer") or "").strip()
        if not analysis or not answer_text or answer_text == SKIPPED_ANSWER:
            counts["skipped"] += 1
            trajectory_steps.append(f"Q{idx}[{source}]: skipped")
            skipped_turns.append(f"Q{idx}")
            continue

        sent = analysis.get("sentiment") or {}
        label = str(sent.get("label") or "neutral")
        score = float(sent.get("score") or 0.0)
        counts[label] += 1
        trajectory_steps.append(f"Q{idx}[{source}]: {label} ({score:.3f})")

        kws = [k for k in (analysis.get("keywords") or []) if isinstance(k, dict) and k.get("phrase")]
        top_turn_kws = ", ".join(f"'{k['phrase']}'" for k in kws[:2]) or f"'{answer_text[:40]}'"
        if label == "positive":
            positive_turns.append(f"Q{idx} ({label} {score:.3f} on {top_turn_kws})")
        else:
            friction_turns.append(f"Q{idx} ({label} {score:.3f} on {top_turn_kws})")

        for kw in kws:
            keyword_entries.append((float(kw.get("score") or 0.0), str(kw["phrase"]), idx, label))

    total = len(transcript)
    sentiment_distribution = (
        f"{counts['positive']} positive, {counts['neutral']} neutral, "
        f"{counts['negative']} negative, {counts['skipped']} skipped (out of {total} turns)"
    )
    sentiment_trajectory = " -> ".join(trajectory_steps) if trajectory_steps else "no turns recorded"

    contrast_parts: list[str] = []
    if positive_turns:
        contrast_parts.append(f"Positive peaks: {'; '.join(positive_turns)}")
    if friction_turns:
        contrast_parts.append(f"Neutral/Negative dips (potential friction/hesitation): {'; '.join(friction_turns)}")
    if skipped_turns:
        contrast_parts.append(f"Skipped questions (disengagement/avoidance): {', '.join(skipped_turns)}")
    sentiment_contrasts = " | ".join(contrast_parts) if contrast_parts else "Uniform polarity across all answers"

    seen_phrases: set[str] = set()
    top_kw_formatted: list[str] = []
    for kw_score, phrase, turn_idx, turn_label in sorted(keyword_entries, key=lambda item: item[0], reverse=True):
        if phrase in seen_phrases:
            continue
        seen_phrases.add(phrase)
        top_kw_formatted.append(f"'{phrase}' (score={kw_score:.3f}, Q{turn_idx}:{turn_label})")
        if len(top_kw_formatted) >= 8:
            break
    top_keywords = ", ".join(top_kw_formatted) if top_kw_formatted else "none extracted"

    resolved_summary = summary or (record.get("analysis_report") or {}).get("summary") or {}
    summary_sentiment_explanation = (
        str(resolved_summary.get("sentiment_explanation") or "").strip()
        or "Not yet generated (Summary stage)."
    )
    themes_list = [str(t) for t in (resolved_summary.get("themes") or []) if t]
    key_points_list = [str(kp) for kp in (resolved_summary.get("key_points") or []) if kp]

    return {
        "user_name": record.get("user_name") or record.get("user_id") or "",
        "topic": record.get("topic") or "",
        "sentiment_label": session_sentiment.label,
        "sentiment_score": session_sentiment.score,
        "sentiment_distribution": sentiment_distribution,
        "sentiment_trajectory": sentiment_trajectory,
        "sentiment_contrasts": sentiment_contrasts,
        "top_keywords": top_keywords,
        "summary_sentiment_explanation": summary_sentiment_explanation,
        "summary_themes": "; ".join(themes_list) if themes_list else "N/A",
        "summary_key_points": "; ".join(key_points_list) if key_points_list else "N/A",
        "transcript_text": _format_transcript_for_llm(record),
    }


def _build_json_schema_response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """Build an OpenAI/Ollama-compatible `json_schema` response_format from a Pydantic model.

    LiteLLM maps this to Ollama's native `format: <json_schema>` (GBNF grammar enforcement)
    and OpenRouter's structured outputs.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


def run_structured_llm(
    schema: type[BaseModel],
    *,
    model: str,
    api_base: str,
    system_prompt: str,
    user_prompt_template: str,
    record: dict[str, Any],
    session_sentiment: Sentiment,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call LiteLLM with grammar-constrained `json_schema` and multi-model context variables."""
    prompt_context = _build_multi_model_context(record, session_sentiment, summary=summary)
    user_prompt = user_prompt_template.format(**prompt_context)
    extra_kwargs: dict[str, Any] = {
        "response_format": _build_json_schema_response_format(schema),
    }
    if model.startswith(("ollama/", "ollama_chat/")):
        extra_kwargs["api_base"] = api_base

    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        **extra_kwargs,
    )
    content = resp.choices[0].message.content or ""
    return _extract_json_object(content)


def generate_ai_summary(
    record: dict[str, Any],
    session_sentiment: Sentiment,
    settings: AnalysisSettings,
) -> dict[str, Any]:
    """Generate a full-transcript summary (grammar-constrained by `InterviewSummary` JSON schema)."""
    return run_structured_llm(
        InterviewSummary,
        model=settings.summary_model,
        api_base=settings.ollama_api_base,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt_template=SUMMARY_USER_PROMPT,
        record=record,
        session_sentiment=session_sentiment,
    )


def generate_emotion_and_insights(
    record: dict[str, Any],
    session_sentiment: Sentiment,
    settings: AnalysisSettings,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate emotion, tone & actionable insights enriched with NLP + Summary LLM context."""
    return run_structured_llm(
        EmotionToneAnalysis,
        model=settings.insights_model,
        api_base=settings.ollama_api_base,
        system_prompt=INSIGHTS_SYSTEM_PROMPT,
        user_prompt_template=INSIGHTS_USER_PROMPT,
        record=record,
        session_sentiment=session_sentiment,
        summary=summary,
    )
