"""Payloads of the NLP tools exposed by the MCP server (sentiment + keyphrases)."""

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


class AnswerInput(Contract):
    """One item of the `analyze_answers` tool input."""

    answer_id: str
    text: str = Field(max_length=5000)


class AnswerAnalysis(Contract):
    """NLP result for a single answer."""

    answer_id: str
    sentiment: Sentiment
    keywords: list[Keyword] = Field(default_factory=list, max_length=5)


class NlpModels(Contract):
    """Which Hugging Face models produced the results."""

    sentiment: str
    keywords: str


class AnalyzeAnswersResult(Contract):
    """Output of the `analyze_answers` tool."""

    answers: list[AnswerAnalysis]
    session_sentiment: Sentiment
    models: NlpModels

    def for_answer(self, answer_id: str) -> AnswerAnalysis | None:
        return next((a for a in self.answers if a.answer_id == answer_id), None)
