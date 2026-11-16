"""MCP tools server (`services/mcp/mcp_server/server.py`).

Runs as a standalone HTTP microservice (Streamable HTTP on `MCP_PORT`, path `/mcp`).
The `agent` microservice discovers and calls these tools over HTTP via Google ADK's
`McpToolset(connection_params=StreamableHTTPConnectionParams(url=MCP_SERVER_URL))`.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import uvicorn
from contracts import (
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    IntakeResult,
    Phase,
    parse_num_questions_env,
    slugify,
)
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("interview-tools")


@mcp.tool()
def start_interview(
    user_name: str,
    topic: str,
    questions: list[str],
) -> dict[str, Any]:
    """Validate the participant's name, topic, and 3-5 initial questions, and pop Question 1.

    Args:
        user_name: The participant's real name or ID as explicitly stated by the user.
        topic: The subject the user wants to be interviewed about.
        questions: 3 to 5 open-ended interview questions (each ending with '?').
    """
    intake = IntakeResult(user_name=user_name, topic=topic)
    distinct_fields = (intake.user_name or "").casefold() != (intake.topic or "").casefold()

    if not (intake.is_complete and distinct_fields):
        return {
            "status": "wait_for_user",
            "state_updates": {"phase": str(Phase.INTAKE)},
            "next_step": (
                "STOP calling tools on this turn. The user has NOT yet clearly told you both their "
                "name and topic. Reply directly to the user in plain text and ask for what is missing."
            ),
        }

    override = parse_num_questions_env()
    target_count = int(override) if override is not None else MIN_QUESTIONS

    cleaned_questions: list[str] = []
    for raw_q in questions or []:
        q = str(raw_q).strip()
        if not q:
            continue
        if not q.endswith("?"):
            q = f"{q}?"
        if q not in cleaned_questions:
            cleaned_questions.append(q)

    required_min = min(target_count, MIN_QUESTIONS)
    if len(cleaned_questions) < required_min:
        return {
            "status": "invalid_questions",
            "state_updates": {"phase": str(Phase.INTAKE)},
            "next_step": (
                f"Call `start_interview` again with at least {required_min} distinct open-ended "
                "questions in `questions`."
            ),
        }

    max_allowed = int(override) if override is not None else MAX_QUESTIONS
    queue = cleaned_questions[:max_allowed]
    first_question = queue.pop(0)

    state_updates = {
        "phase": str(Phase.MAIN_QUEUE),
        "user_name": intake.user_name,
        "user_id": slugify(intake.user_name),
        "topic": intake.topic,
        "interview_id": str(uuid.uuid4()),
        "question_queue": queue,
        "followup_queue": [],
        "current_question": first_question,
        "current_followup_context": None,
        "last_answered_question": None,
        "last_user_answer": None,
        "transcript": [],
        "pushed_this_turn": False,
        "saved_to_disk": False,
    }

    return {
        "status": "ready",
        "phase": str(Phase.MAIN_QUEUE),
        "user_name": intake.user_name,
        "topic": intake.topic,
        "popped_question": first_question,
        "remaining_in_question_queue": len(queue),
        "state_updates": state_updates,
        "next_step": (
            f"Do NOT call any more tools on this turn. Greet {intake.user_name}, include a "
            "1-sentence privacy note (avoid sharing confidential/PII details; they can type `skip` "
            f"or `stop` anytime), and ask the popped question: '{first_question}'"
        ),
    }


@mcp.tool()
def push_followup(
    follow_up_question: str,
    referenced_answer: str,
) -> dict[str, Any]:
    """Validate and format a follow-up question to be pushed onto `followup_queue`.

    Args:
        follow_up_question: A single open-ended follow-up question ending with '?'.
        referenced_answer: Short summary or quote of what the user just said that triggered this follow-up.
    """
    q = follow_up_question.strip()
    if not q.endswith("?"):
        q = f"{q}?"
    ctx_summary = referenced_answer.strip() or "what you shared earlier"

    return {
        "status": "pushed_to_followup_queue",
        "followup_item": {"question": q, "context": ctx_summary},
        "next_step": (
            "Follow-up saved in `followup_queue` for the second stage! Do NOT ask the follow-up right now; "
            "instead, acknowledge their answer and ask the current popped main question from `question_queue`."
        ),
    }


if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8001"))
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)
