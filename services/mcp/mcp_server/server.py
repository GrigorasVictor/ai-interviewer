"""MCP tools server (`services/mcp/mcp_server/server.py`).

Exposes the Two-Queue interview tools over Streamable HTTP (`/mcp`) and saves
each user's interview in `data/<user_id>.json`.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from contracts import (
    Phase,
    apply_push_followup_to_state,
    apply_start_interview_to_state,
    load_user_interview,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

mcp = MCPServer("interview-tools")


@mcp.tool()
def start_interview(
    user_name: str,
    topic: str,
    questions: list[str],
) -> dict[str, Any]:
    """Start the interview once the user gives their name and topic.

    Args:
        user_name: Participant's name (e.g. 'Victor').
        topic: Subject to discuss (e.g. 'AI in the workplace').
        questions: 3 to 5 open-ended questions about the topic (each ending with '?').
    """
    state: dict[str, Any] = {"phase": str(Phase.INTAKE)}
    return apply_start_interview_to_state(
        state,
        user_name=user_name,
        topic=topic,
        questions=questions,
    )


@mcp.tool()
def push_followup(
    follow_up_question: str,
    referenced_answer: str,
    user_id: str = "",
) -> dict[str, Any]:
    """Queue a follow-up question for the second stage (`followup_queue`).

    Args:
        follow_up_question: One open-ended follow-up question ending with '?'.
        referenced_answer: Short summary of the user's answer that triggered it.
        user_id: Optional participant slug (e.g. 'victor').
    """
    state = (load_user_interview(user_id) if user_id else None) or {"phase": str(Phase.MAIN_QUEUE)}
    return apply_push_followup_to_state(
        state,
        follow_up_question=follow_up_question,
        referenced_answer=referenced_answer,
    )


if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8001"))
    # Allow requests from both localhost and the Docker `mcp` hostname (`http://mcp:8001/mcp`).
    app = mcp.streamable_http_app(
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    uvicorn.run(app, host="0.0.0.0", port=port)
