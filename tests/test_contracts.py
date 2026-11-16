"""Unit tests for the shared Pydantic data contracts (`shared/contracts`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts import (
    SKIPPED_ANSWER,
    AnswerAnalysis,
    EmotionToneAnalysis,
    IntakeResult,
    InterviewSummary,
    Keyword,
    Sentiment,
)


def test_intake_result_completeness_and_placeholder_stripping() -> None:
    assert not IntakeResult(user_name="Ana", topic=None).is_complete
    assert not IntakeResult(user_name="unknown", topic="AI in the workplace").is_complete
    assert not IntakeResult(user_name=None, topic="AI in the workplace").is_complete
    assert IntakeResult(user_name="Ana", topic="AI in the workplace").is_complete


def test_summary_and_emotion_contracts_validation() -> None:
    summary = InterviewSummary(
        overview="Ana shared a pragmatic perspective on AI in daily workflows.",
        themes=["Workflow Automation", "Human Verification"],
        key_points=["Automates boilerplate tests", "Requires code review"],
        quotes=["I use AI every morning"],
        sentiment_explanation="Predominantly positive sentiment.",
    )
    assert len(summary.themes) == 2

    with pytest.raises(ValidationError):
        InterviewSummary(
            overview="Too few themes",
            themes=["OnlyOne"],
            key_points=["p1", "p2"],
            sentiment_explanation="exp",
        )

    insights = EmotionToneAnalysis(
        dominant_emotion="optimism",
        communication_tone="analytical and concise",
        confidence_level="high",
        pain_points=["Edge case debugging"],
        actionable_recommendations=["Adopt automated test review"],
    )
    assert insights.dominant_emotion == "optimism"


def test_score_rounds_to_three_decimals_and_keyword_lowercases() -> None:
    s = Sentiment(label="positive", score=0.87654321)
    assert s.score == 0.877

    kw = Keyword(phrase="  Drafting Emails  ", score=0.61234)
    assert kw.phrase == "drafting emails"
    assert kw.score == 0.612

    analysis = AnswerAnalysis(answer_id="int-1:1", sentiment=s, keywords=[kw])
    assert analysis.answer_id == "int-1:1"
    assert SKIPPED_ANSWER == "[skipped]"
