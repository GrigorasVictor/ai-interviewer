"""Re-exports the shared Pydantic contracts inside `interviewer_app`.

Having `schemas.py` right inside the agent package makes imports short
(`from .schemas import Rubric, InterviewerTurn`) while keeping a single source
of truth in `shared/contracts/`.

If `interview-contracts` is not yet installed in the active virtualenv, we add
`shared/` to `sys.path` automatically so `adk web` works out of the box.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from contracts import (
        SCHEMA_VERSION,
        SKIPPED_ANSWER,
        AnalyzeAnswersResult,
        AnswerAnalysis,
        AnswerInput,
        IntakeResult,
        InterviewListItem,
        InterviewRecord,
        InterviewSummary,
        InterviewerTurn,
        JsonSaveStatus,
        Keyword,
        ModelsUsed,
        Neo4jSaveStatus,
        NlpModels,
        ResyncResult,
        Rubric,
        RubricArea,
        SaveInterviewResult,
        Score,
        Sentiment,
        SentimentLabel,
        TranscriptTurn,
        UserRef,
        utc_now_iso,
    )
except ModuleNotFoundError:
    _SHARED_DIR = Path(__file__).resolve().parents[3] / "shared"
    if str(_SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(_SHARED_DIR))
    from contracts import (
        SCHEMA_VERSION,
        SKIPPED_ANSWER,
        AnalyzeAnswersResult,
        AnswerAnalysis,
        AnswerInput,
        IntakeResult,
        InterviewListItem,
        InterviewRecord,
        InterviewSummary,
        InterviewerTurn,
        JsonSaveStatus,
        Keyword,
        ModelsUsed,
        Neo4jSaveStatus,
        NlpModels,
        ResyncResult,
        Rubric,
        RubricArea,
        SaveInterviewResult,
        Score,
        Sentiment,
        SentimentLabel,
        TranscriptTurn,
        UserRef,
        utc_now_iso,
    )

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
