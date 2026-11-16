"""AI analyzers for full-transcript Summary and Emotion / Tone & Actionable Insights.

Uses prompts from `shared/contracts/prompts.py` and LiteLLM (`ollama_chat/...` or `openrouter/...`).
"""

from __future__ import annotations

import json
import re
from typing import Any

import litellm
from contracts import (
    INSIGHTS_SYSTEM_PROMPT,
    INSIGHTS_USER_PROMPT,
    SKIPPED_ANSWER,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
    InterviewSummary,
    Sentiment,
)
from pydantic import BaseModel, Field

from .config import AnalysisSettings


class EmotionToneAnalysis(BaseModel):
    """Output of the Emotion / Tone & Actionable Insights AI analyzer."""

    dominant_emotion: str = Field(
        description="Primary emotion expressed (e.g. 'optimism', 'pragmatism', 'frustration', 'curiosity')."
    )
    communication_tone: str = Field(
        description="Overall tone of the participant (e.g. 'analytical and candid', 'concise and goal-oriented')."
    )
    confidence_level: str = Field(
        description="Participant's confidence/engagement level: 'high', 'medium', or 'low'."
    )
    pain_points: list[str] = Field(
        default_factory=list,
        description="1-4 concrete challenges, trade-offs, or frustrations mentioned by the participant.",
    )
    actionable_recommendations: list[str] = Field(
        default_factory=list,
        description="2-4 concrete recommendations or next steps derived from the interview.",
    )


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in: {raw_text!r}")
    return json.loads(match.group(0))


def _format_transcript_for_llm(record: dict[str, Any]) -> str:
    lines: list[str] = []
    for idx, turn in enumerate(record.get("transcript") or [], start=1):
        q = turn.get("question") or ""
        a = turn.get("answer") or ""
        src = turn.get("source") or "main_queue"
        lines.append(f"Q{idx} ({src}): {q}\nA{idx}: {a}")
    return "\n\n".join(lines)


def _call_llm_json(model: str, api_base: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    if model.startswith(("ollama/", "ollama_chat/")):
        kwargs["api_base"] = api_base
        kwargs["format"] = "json"
    resp = litellm.completion(**kwargs)
    content = resp.choices[0].message.content or ""
    return _extract_json_object(content)


def generate_ai_summary(
    record: dict[str, Any],
    session_sentiment: Sentiment,
    settings: AnalysisSettings,
) -> InterviewSummary:
    """Generate a full-transcript `InterviewSummary` using `SUMMARY_SYSTEM_PROMPT`."""
    user_prompt = SUMMARY_USER_PROMPT.format(
        user_name=record.get("user_name") or record.get("user_id") or "Participant",
        topic=record.get("topic") or "General topic",
        sentiment_label=session_sentiment.label,
        sentiment_score=session_sentiment.score,
        transcript_text=_format_transcript_for_llm(record),
    )
    try:
        data = _call_llm_json(
            settings.summary_model,
            settings.ollama_api_base,
            SUMMARY_SYSTEM_PROMPT,
            user_prompt,
        )
        return InterviewSummary.model_validate(data)
    except Exception:
        return _fallback_summary(record, session_sentiment)


def generate_emotion_and_insights(
    record: dict[str, Any],
    session_sentiment: Sentiment,
    settings: AnalysisSettings,
) -> EmotionToneAnalysis:
    """Generate Emotion / Tone & Actionable Insights using `INSIGHTS_SYSTEM_PROMPT`."""
    user_prompt = INSIGHTS_USER_PROMPT.format(
        user_name=record.get("user_name") or record.get("user_id") or "Participant",
        topic=record.get("topic") or "General topic",
        sentiment_label=session_sentiment.label,
        sentiment_score=session_sentiment.score,
        transcript_text=_format_transcript_for_llm(record),
    )
    try:
        data = _call_llm_json(
            settings.insights_model,
            settings.ollama_api_base,
            INSIGHTS_SYSTEM_PROMPT,
            user_prompt,
        )
        return EmotionToneAnalysis.model_validate(data)
    except Exception:
        return _fallback_insights(record, session_sentiment)


def _fallback_summary(record: dict[str, Any], session_sentiment: Sentiment) -> InterviewSummary:
    user_name = record.get("user_name") or record.get("user_id") or "The participant"
    topic = record.get("topic") or "the topic"
    turns = [
        t for t in (record.get("transcript") or []) if (t.get("answer") or "").strip() != SKIPPED_ANSWER
    ]
    answers = [str(t.get("answer")).strip() for t in turns if t.get("answer")]

    overview = (
        f"{user_name} discussed {topic} across {len(turns)} answered question(s), expressing a "
        f"predominantly {session_sentiment.label} perspective ({session_sentiment.score}). "
        f"Their responses focused on practical outcomes and day-to-day workflow impact."
    )
    themes = [topic, "Workflow & Productivity", "Practical Trade-offs"][:3]
    key_points = [ans[:140] for ans in answers[:4]]
    while len(key_points) < 2:
        key_points.append(f"Shared perspective on {topic}.")
    quotes = [ans for ans in answers[:2] if len(ans) <= 160]
    sentiment_explanation = (
        f"Overall sentiment is {session_sentiment.label} (score {session_sentiment.score}) based on "
        f"{len(turns)} non-skipped response(s)."
    )
    return InterviewSummary(
        overview=overview,
        themes=themes,
        key_points=key_points,
        quotes=quotes,
        sentiment_explanation=sentiment_explanation,
    )


def _fallback_insights(record: dict[str, Any], session_sentiment: Sentiment) -> EmotionToneAnalysis:
    topic = record.get("topic") or "the topic"
    turns = [
        t for t in (record.get("transcript") or []) if (t.get("answer") or "").strip() != SKIPPED_ANSWER
    ]
    emotion_map = {
        "positive": "optimism & pragmatism",
        "negative": "frustration & caution",
        "neutral": "pragmatism & neutrality",
    }
    pain_points: list[str] = []
    for t in turns:
        ans = str(t.get("answer") or "")
        if any(w in ans.lower() for w in ("but", "debug", "hard", "issue", "problem", "short", "error")):
            pain_points.append(ans)
    if not pain_points:
        pain_points = [f"Ensuring consistent quality and measurable value in {topic}."]

    return EmotionToneAnalysis(
        dominant_emotion=emotion_map.get(session_sentiment.label, "pragmatism"),
        communication_tone="concise and practical",
        confidence_level="high" if len(turns) >= 3 else "medium",
        pain_points=pain_points[:3],
        actionable_recommendations=[
            f"Focus {topic} adoption on high-leverage repetitive tasks first.",
            "Pair automated outputs with lightweight human verification and debugging checks.",
        ],
    )
