"""AI analyzers for full-transcript Summary and Emotion / Tone & Actionable Insights.

Uses JSON Schema grammar-constrained structured outputs (`response_format={"type": "json_schema", ...}`)
so Ollama / OpenRouter enforces the schema at token generation time, and returns the raw JSON dict
emitted by the model without post-validation crashes or hardcoded overrides.
"""

from __future__ import annotations

import json
import re
from typing import Any

import litellm
from contracts import (
    INSIGHTS_SYSTEM_PROMPT,
    INSIGHTS_USER_PROMPT,
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


def _format_transcript_for_llm(record: dict[str, Any]) -> str:
    return "\n\n".join(
        f"Q{idx} ({turn.get('source')}): {turn.get('question')}\nA{idx}: {turn.get('answer')}"
        for idx, turn in enumerate(record.get("transcript") or [], start=1)
    )


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
) -> dict[str, Any]:
    """Call LiteLLM with grammar-constrained `json_schema` and return the raw JSON dict from the model."""
    user_prompt = user_prompt_template.format(
        user_name=record.get("user_name") or record.get("user_id") or "",
        topic=record.get("topic") or "",
        sentiment_label=session_sentiment.label,
        sentiment_score=session_sentiment.score,
        transcript_text=_format_transcript_for_llm(record),
    )
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
) -> dict[str, Any]:
    """Generate emotion, tone & actionable insights (grammar-constrained by `EmotionToneAnalysis` JSON schema)."""
    return run_structured_llm(
        EmotionToneAnalysis,
        model=settings.insights_model,
        api_base=settings.ollama_api_base,
        system_prompt=INSIGHTS_SYSTEM_PROMPT,
        user_prompt_template=INSIGHTS_USER_PROMPT,
        record=record,
        session_sentiment=session_sentiment,
    )
