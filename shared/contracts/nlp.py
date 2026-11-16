"""NLP contracts for per-answer sentiment, session sentiment, and keyphrase extraction."""

from pydantic import Field, field_validator

from .common import Contract, Score, SentimentLabel


class Sentiment(Contract):
    label: SentimentLabel
    score: Score


class Keyword(Contract):
    phrase: str = Field(min_length=1)
    score: Score

    @field_validator("phrase")
    @classmethod
    def _lowercase(cls, phrase: str) -> str:
        return phrase.lower()


class AnswerAnalysis(Contract):
    """NLP result for a single answer."""

    answer_id: str
    sentiment: Sentiment
    keywords: list[Keyword] = Field(default_factory=list, max_length=5)
