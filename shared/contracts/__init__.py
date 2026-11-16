"""Shared data contracts for the Mini AI Interviewer.

Used by both services (agent and mcp) so that every payload crossing a
service boundary has exactly one definition. Spec: .agents/DATA_MODEL.md

    llm_outputs  what the LLM returns (Rubric, InterviewerTurn, InterviewSummary, ...)
    nlp          sentiment / keyphrase tool payloads
    record       the persisted InterviewRecord
    storage      save / list / resync tool payloads
"""

from .common import SCHEMA_VERSION, Score, SentimentLabel, utc_now_iso
from .llm_outputs import IntakeResult, InterviewerTurn, InterviewSummary, Rubric, RubricArea
from .nlp import AnalyzeAnswersResult, AnswerAnalysis, AnswerInput, Keyword, NlpModels, Sentiment
from .record import SKIPPED_ANSWER, InterviewRecord, ModelsUsed, TranscriptTurn, UserRef
from .storage import InterviewListItem, JsonSaveStatus, Neo4jSaveStatus, ResyncResult, SaveInterviewResult

__all__ = [
    "SCHEMA_VERSION",
    "SKIPPED_ANSWER",
    "AnalyzeAnswersResult",
    "AnswerAnalysis",
    "AnswerInput",
    "IntakeResult",
    "InterviewListItem",
    "InterviewRecord",
    "InterviewSummary",
    "InterviewerTurn",
    "JsonSaveStatus",
    "Keyword",
    "ModelsUsed",
    "Neo4jSaveStatus",
    "NlpModels",
    "ResyncResult",
    "Rubric",
    "RubricArea",
    "SaveInterviewResult",
    "Score",
    "Sentiment",
    "SentimentLabel",
    "TranscriptTurn",
    "UserRef",
    "utc_now_iso",
]
