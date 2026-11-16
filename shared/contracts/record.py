"""The persisted interview: one InterviewRecord = one element of interviews_output.json."""

from typing import Literal

from pydantic import Field

from .common import SCHEMA_VERSION, Contract, utc_now_iso
from .llm_outputs import InterviewSummary, Rubric
from .nlp import AnswerAnalysis, Sentiment

SKIPPED_ANSWER = "[skipped]"
"""Stored as the answer text when the user types `skip`."""


class UserRef(Contract):
    id: str = Field(description="Stable slug, e.g. 'ana-popescu', 'John Doe'.")
    name: str


class TranscriptTurn(Contract):
    """One question/answer pair of the interview."""

    index: int = Field(ge=1, description="1-based position in the interview.")
    answer_id: str
    area: str = Field(description="Rubric area this question belongs to.")
    question: str
    answer: str
    is_follow_up: bool = False
    asked_at: str
    answered_at: str | None = None
    analysis: AnswerAnalysis | None = Field(default=None, description="Filled at persist time; null for skipped answers.")

    @property
    def is_skipped(self) -> bool:
        return self.answer == SKIPPED_ANSWER

    @staticmethod
    def make_answer_id(interview_id: str, index: int) -> str:
        return f"{interview_id}:{index}"


class ModelsUsed(Contract):
    llm: str
    sentiment: str
    keywords: str


class InterviewRecord(Contract):
    """A completed interview: transcript + summary + NLP scores."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    interview_id: str
    timestamp: str = Field(default_factory=utc_now_iso, description="Completion time (ISO 8601 UTC).")
    user: UserRef
    topic: str
    rubric: Rubric
    transcript: list[TranscriptTurn] = Field(min_length=1)
    summary: InterviewSummary
    session_sentiment: Sentiment
    models: ModelsUsed
    neo4j_synced: bool = Field(default=False, description="Set by the MCP server after the graph write.")
