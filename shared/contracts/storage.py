"""Payloads of the storage tools exposed by the MCP server."""

from typing import Literal

from pydantic import Field

from .common import Contract
from .nlp import Sentiment


class JsonSaveStatus(Contract):
    status: Literal["ok", "error"]
    path: str
    message: str | None = None


class Neo4jSaveStatus(Contract):
    status: Literal["ok", "skipped", "error"] = Field(description="'skipped' = Neo4j unreachable or disabled.")
    message: str | None = None


class SaveInterviewResult(Contract):
    """Output of the `save_interview` tool."""

    interview_id: str
    json_file: JsonSaveStatus
    neo4j: Neo4jSaveStatus
    cypher_query: str = Field(description="Query that visualizes this interview in Neo4j Browser.")


class InterviewListItem(Contract):
    """One item of the `list_interviews` tool output."""

    interview_id: str
    user_id: str
    topic: str
    timestamp: str
    overall_sentiment: Sentiment


class ResyncResult(Contract):
    """Output of the `resync_neo4j` tool."""

    synced: int = 0
    failed: int = 0
