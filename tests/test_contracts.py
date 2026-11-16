"""Unit tests for the shared Pydantic data contracts (`shared/contracts`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts import (
    SKIPPED_ANSWER,
    IntakeResult,
    Keyword,
    Rubric,
    RubricArea,
    Sentiment,
    TranscriptTurn,
)


def test_intake_result_completeness() -> None:
    assert not IntakeResult(user_name="Ana", topic=None).is_complete
    assert not IntakeResult(user_name=None, topic="AI in the workplace").is_complete
    assert IntakeResult(user_name="Ana", topic="AI in the workplace").is_complete


def test_rubric_requires_3_to_5_unique_areas() -> None:
    areas = [
        RubricArea(name="Task Automation", description="Desc 1", angles=["a1"]),
        RubricArea(name="Trust & Safety", description="Desc 2", angles=["a2"]),
        RubricArea(name="Skill Evolution", description="Desc 3", angles=["a3"]),
    ]
    rubric = Rubric(topic="AI in the workplace", areas=areas)
    assert rubric.area_names() == ["Task Automation", "Trust & Safety", "Skill Evolution"]

    # Duplicate area names (case-insensitive) must be rejected.
    with pytest.raises(ValidationError):
        Rubric(
            topic="AI in the workplace",
            areas=[
                RubricArea(name="Task Automation", description="Desc 1", angles=["a1"]),
                RubricArea(name="task automation", description="Desc 2", angles=["a2"]),
                RubricArea(name="Skill Evolution", description="Desc 3", angles=["a3"]),
            ],
        )


def test_score_rounds_to_three_decimals_and_keyword_lowercases() -> None:
    s = Sentiment(label="positive", score=0.87654321)
    assert s.score == 0.877

    kw = Keyword(phrase="  Drafting Emails  ", score=0.61234)
    assert kw.phrase == "drafting emails"
    assert kw.score == 0.612


def test_transcript_turn_skip_detection() -> None:
    turn = TranscriptTurn(
        index=1,
        answer_id=TranscriptTurn.make_answer_id("int-1", 1),
        area="Task Automation",
        question="How do you use AI?",
        answer=SKIPPED_ANSWER,
        asked_at="2026-11-16T10:00:00Z",
    )
    assert turn.is_skipped
    assert turn.answer_id == "int-1:1"
