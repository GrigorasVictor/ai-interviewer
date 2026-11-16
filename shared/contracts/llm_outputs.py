"""Structured LLM output contracts (`IntakeResult`, `InterviewSummary`, `EmotionToneAnalysis`)."""

from pydantic import Field, field_validator

from .common import Contract

_PLACEHOLDER_VALUES = {
    "",
    "none",
    "(none)",
    "null",
    "(null)",
    "nil",
    "n/a",
    "na",
    "unknown",
    "(unknown)",
    "unspecified",
    "not provided",
    "not specified",
    "not mentioned",
    "missing",
    "user",
    "participant",
    "topic",
    "the topic",
    "hi",
    "hello",
    "hey",
    "salut",
    "buna",
}


class IntakeResult(Contract):
    """Validated participant name and topic passed to `start_interview`."""

    user_name: str | None = Field(
        default=None,
        description="The participant's name or ID, if explicitly stated by the user; otherwise null.",
    )
    topic: str | None = Field(
        default=None,
        max_length=120,
        description="The interview topic, if explicitly stated by the user; otherwise null.",
    )

    @field_validator("user_name", "topic", mode="before")
    @classmethod
    def _clean_placeholder(cls, value: object) -> str | None:
        text = str(value or "").strip().strip("\"'")
        return None if text.casefold() in _PLACEHOLDER_VALUES else text

    @property
    def is_complete(self) -> bool:
        return bool(self.user_name and self.topic)


class InterviewSummary(Contract):
    """Full-transcript qualitative summary produced by the Summary LLM."""

    overview: str = Field(description="2-4 sentence summary of the participant's views.")
    themes: list[str] = Field(min_length=2, max_length=5, description="2-5 key themes.")
    key_points: list[str] = Field(min_length=2, max_length=6, description="2-6 key points.")
    quotes: list[str] = Field(default_factory=list, max_length=3, description="0-3 verbatim quotes copied from the answers.")
    sentiment_explanation: str = Field(description="Qualitative description of the overall sentiment.")


class EmotionToneAnalysis(Contract):
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
