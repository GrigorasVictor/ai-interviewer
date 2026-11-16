"""What the LLM returns.

These models are passed as `output_schema` to the ADK sub-agents, so field
descriptions double as instructions for the model. Keep them short and precise.
"""

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
    """Output of IntakeAgent: who is being interviewed, about what, and how to ask for any missing piece."""

    user_name: str | None = Field(
        default=None,
        description="The participant's name or ID, if explicitly stated by the user; otherwise null.",
    )
    topic: str | None = Field(
        default=None,
        max_length=120,
        description="The interview topic, if explicitly stated by the user; otherwise null.",
    )
    follow_up_question: str | None = Field(
        default=None,
        description=(
            "Required when `user_name` or `topic` is null: a friendly 1-2 sentence question "
            "asking the user for whichever piece is still missing. Null only when both are known."
        ),
    )

    @field_validator("user_name", "topic", mode="before")
    @classmethod
    def _clean_placeholder(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip().strip("\"'")
        if text.casefold() in _PLACEHOLDER_VALUES:
            return None
        return text

    @property
    def is_complete(self) -> bool:
        return bool(self.user_name and self.topic)


class RubricArea(Contract):
    """One thematic area (sub-topic) to explore during the interview."""

    name: str = Field(min_length=2, max_length=60, description="Short Title Case name, e.g. 'Task Automation'.")
    description: str = Field(description="One sentence describing what this area covers.")
    angles: list[str] = Field(min_length=1, max_length=3, description="1-3 example angles worth exploring.")


class Rubric(Contract):
    """Output of PlannerAgent: the interview plan."""

    topic: str = Field(description="The interview topic.")
    areas: list[RubricArea] = Field(min_length=3, max_length=5, description="3-5 distinct, non-overlapping thematic areas.")

    @field_validator("areas")
    @classmethod
    def _unique_area_names(cls, areas: list[RubricArea]) -> list[RubricArea]:
        names = [area.name.casefold() for area in areas]
        if len(names) != len(set(names)):
            raise ValueError("rubric area names must be unique")
        return areas

    def area_names(self) -> list[str]:
        return [area.name for area in self.areas]


class InterviewerTurn(Contract):
    """Output of InterviewerAgent: the next question to ask."""

    area: str = Field(description="Exactly one of the rubric area names.")
    question: str = Field(min_length=5, description="A single open-ended question addressed directly to the user ('you').")
    is_follow_up: bool = Field(default=False, description="True if the question builds on the previous answer.")
    coverage_complete: bool = Field(default=False, description="True only if every rubric area has been covered.")


class InterviewSummary(Contract):
    """Output of SummarizerAgent: the final qualitative summary."""

    overview: str = Field(description="2-4 sentence summary of the participant's views.")
    themes: list[str] = Field(min_length=2, max_length=5, description="2-5 key themes.")
    key_points: list[str] = Field(min_length=2, max_length=6, description="2-6 key points.")
    quotes: list[str] = Field(default_factory=list, max_length=3, description="0-3 verbatim quotes copied from the answers.")
    sentiment_explanation: str = Field(description="Qualitative description of the overall sentiment.")
