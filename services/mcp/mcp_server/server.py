"""Minimal FastMCP server stub for Milestone M0–M3.

Exposes:
- `ping` — connectivity check between the `agent` and `mcp` containers.
- `analyze_answers` — stub returning valid `AnalyzeAnswersResult` contracts (replaced by
  real Hugging Face RoBERTa + KeyBERT models in Milestone M2).
- `save_interview` — stub returning valid `SaveInterviewResult` contracts (replaced by
  atomic JSON + Neo4j persistence in Milestone M4).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from contracts import (
        AnalyzeAnswersResult,
        AnswerAnalysis,
        AnswerInput,
        InterviewRecord,
        JsonSaveStatus,
        Keyword,
        Neo4jSaveStatus,
        NlpModels,
        SaveInterviewResult,
        Sentiment,
    )
except ModuleNotFoundError:
    _SHARED_DIR = Path(__file__).resolve().parents[3] / "shared"
    if str(_SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(_SHARED_DIR))
    from contracts import (
        AnalyzeAnswersResult,
        AnswerAnalysis,
        AnswerInput,
        InterviewRecord,
        JsonSaveStatus,
        Keyword,
        Neo4jSaveStatus,
        NlpModels,
        SaveInterviewResult,
        Sentiment,
    )

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("interview-tools", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8001")))


@mcp.tool()
def ping() -> dict[str, str]:
    """Health/connectivity check used by the agent service."""
    return {"status": "ok", "service": "mcp"}


@mcp.tool()
def analyze_answers(answers: list[AnswerInput]) -> AnalyzeAnswersResult:
    """Stub NLP enrichment tool (real Hugging Face models land in Milestone M2)."""
    per_answer = [
        AnswerAnalysis(
            answer_id=item.answer_id,
            sentiment=Sentiment(label="neutral", score=0.5),
            keywords=[Keyword(phrase="stub keyword", score=0.5)],
        )
        for item in answers
    ]
    return AnalyzeAnswersResult(
        answers=per_answer,
        session_sentiment=Sentiment(label="neutral", score=0.5),
        models=NlpModels(sentiment="stub/sentiment", keywords="stub/keywords"),
    )


@mcp.tool()
def save_interview(record: InterviewRecord) -> SaveInterviewResult:
    """Stub persistence tool (real JSON + Neo4j persistence lands in Milestone M4)."""
    return SaveInterviewResult(
        interview_id=record.interview_id,
        json_file=JsonSaveStatus(status="ok", path="data/interviews_output.json", message="stub"),
        neo4j=Neo4jSaveStatus(status="skipped", message="stub — Neo4j ingestion lands in M4"),
        cypher_query=(
            f'MATCH p = (u:User)-[:PARTICIPATED_IN]->(i:Interview {{id: "{record.interview_id}"}})-[*1..2]->() '
            "RETURN p"
        ),
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
