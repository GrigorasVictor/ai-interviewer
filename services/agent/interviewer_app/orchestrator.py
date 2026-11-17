"""Two-Queue LLM Interview Orchestrator (`services/agent/interviewer_app/orchestrator.py`).

- Discovers & calls `start_interview` and `push_followup` over HTTP via `McpToolset`.
- Advances `question_queue` -> `followup_queue` -> `done` automatically on every user answer.
- Saves each participant's interview in `data/<user_id>.json`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from contracts import (
    ALLOWED_TOOLS_BY_PHASE,
    PHASE_PROMPTS,
    RESTART_COMMANDS,
    STATE_MACHINE_TRANSITIONS,
    Phase,
    Signal,
    advance_queues_on_user_answer,
    apply_push_followup_to_state,
    apply_start_interview_to_state,
    collect_user_messages_text,
    persist_interview_to_json,
    reset_interview_state,
)
from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from google.adk.workflow import RetryConfig

from .config import build_llm, load_settings


def _current_user_text(ctx: Any) -> str:
    content = getattr(ctx, "user_content", None)
    parts = getattr(content, "parts", None) or []
    return " ".join(p.text for p in parts if getattr(p, "text", None)).strip()


PHASE_USER_TURN_HANDLERS: dict[Phase, Callable[[Any, str], Any]] = {
    Phase.MAIN_QUEUE: advance_queues_on_user_answer,
    Phase.FOLLOWUP_QUEUE: advance_queues_on_user_answer,
    Phase.DONE: lambda state, text: reset_interview_state(state) if text.lower() in RESTART_COMMANDS else None,
}

TOOL_RESULT_HANDLERS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "start_interview": lambda args, tool_ctx: apply_start_interview_to_state(
        tool_ctx.state,
        user_name=str(args.get("user_name", "")),
        topic=str(args.get("topic", "")),
        questions=list(args.get("questions") or []),
        user_history=collect_user_messages_text(tool_ctx),
    ),
    "push_followup": lambda args, tool_ctx: apply_push_followup_to_state(
        tool_ctx.state,
        follow_up_question=str(args.get("follow_up_question", "")),
        referenced_answer=str(args.get("referenced_answer", "")),
    ),
}


def build_harness_instruction(ctx: ReadonlyContext) -> str:
    """Format `PHASE_PROMPTS[phase]` with the current state variables."""
    s = ctx.state
    phase = str(Phase(s.get("phase", Phase.INTAKE)))
    settings = load_settings()
    override = s.get("num_questions_override") or settings.num_questions_override
    return PHASE_PROMPTS[phase].format(
        user_name=s.get("user_name") or "(unknown)",
        user_id=s.get("user_id") or "(unknown)",
        topic=s.get("topic") or "(unknown)",
        target_questions=int(override) if override is not None else settings.min_questions,
        min_questions=settings.min_questions,
        max_questions=settings.max_questions,
        current_question=s.get("current_question") or "(None)",
        current_followup_context=s.get("current_followup_context") or "your earlier point",
        last_answered_question=s.get("last_answered_question") or "(None)",
        last_user_answer=s.get("last_user_answer") or "(None)",
        remaining_main_count=len(s.get("question_queue") or []),
        remaining_followup_count=len(s.get("followup_queue") or []),
        answered_count=len(s.get("transcript") or []),
    )


def build_mcp_toolset(mcp_url: str | None = None) -> McpToolset:
    """Connect to the `mcp` microservice over Streamable HTTP."""
    settings = load_settings()
    headers = {"Authorization": f"Bearer {settings.mcp_auth_token}"} if settings.mcp_auth_token else None
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=(mcp_url or settings.mcp_server_url).strip(),
            headers=headers,
        ),
        tool_filter=list(TOOL_RESULT_HANDLERS.keys()),
    )


def build_interview_harness(
    *,
    name: str = "interview_orchestrator",
    model_name: str | None = None,
    num_questions: int | None = None,
    mcp_url: str | None = None,
) -> LlmAgent:
    """Build the Two-Queue `LlmAgent` connected to MCP over HTTP."""

    async def _on_user_turn(ctx: Any) -> None:
        state = ctx.state
        if num_questions is not None:
            state["num_questions_override"] = num_questions
        phase = Phase(state.get("phase", Phase.INTAKE))
        state["phase"] = str(phase)

        user_text = _current_user_text(ctx)
        turn_handler = PHASE_USER_TURN_HANDLERS.get(phase)
        if turn_handler and user_text:
            turn_handler(state, user_text)

    async def _filter_tools(ctx: Any, llm_request: Any) -> None:
        # Strip prior internal `thought` parts so LiteLLM doesn't resend them as normal assistant text.
        for content in getattr(llm_request, "contents", None) or []:
            if getattr(content, "parts", None):
                non_thought = [p for p in content.parts if not getattr(p, "thought", False)]
                if non_thought:
                    content.parts = non_thought

        phase = Phase(ctx.state.get("phase", Phase.INTAKE))
        allowed = set(ALLOWED_TOOLS_BY_PHASE[phase])
        # Disable `push_followup` if already pushed this turn OR if no question has been answered yet (Question 1 turn).
        if ctx.state.get("pushed_this_turn") or not ctx.state.get("last_user_answer"):
            allowed.discard("push_followup")

        # Filter `config.tools` and drop any `Tool` object whose `function_declarations` becomes empty.
        # (Leaving `Tool(function_declarations=[])` causes ADK's `lite_llm.py` to serialize `{"functionDeclarations": []}`
        # without a `"type": "function"` field, which OpenRouter/Nvidia rejects with HTTP 400!)
        # Meanwhile `llm_request.tools_dict` stays intact so ADK never crashes with "Tool not found".
        config = getattr(llm_request, "config", None)
        if config and getattr(config, "tools", None):
            kept_tools = []
            for tool_obj in config.tools:
                decls = [
                    d
                    for d in (getattr(tool_obj, "function_declarations", None) or [])
                    if getattr(d, "name", None) in allowed
                ]
                if decls:
                    tool_obj.function_declarations = decls
                    kept_tools.append(tool_obj)
            config.tools = kept_tools

    async def _on_tool_result(tool: Any, args: dict[str, Any], tool_ctx: Any, response: Any) -> dict[str, Any] | None:
        handler = TOOL_RESULT_HANDLERS.get(getattr(tool, "name", ""))
        return handler(args, tool_ctx) if handler else None

    return LlmAgent(
        name=name,
        description="Two-Queue AI Interviewer (`question_queue` + `followup_queue`).",
        model=build_llm(model_name),
        instruction=build_harness_instruction,
        tools=[build_mcp_toolset(mcp_url)],
        before_agent_callback=_on_user_turn,
        before_model_callback=_filter_tools,
        after_tool_callback=_on_tool_result,
        retry_config=RetryConfig(max_attempts=3, initial_delay=0.2, backoff_factor=1.5, jitter=0.0),
    )


InterviewOrchestrator = build_interview_harness

__all__ = [
    "ALLOWED_TOOLS_BY_PHASE",
    "InterviewOrchestrator",
    "PHASE_USER_TURN_HANDLERS",
    "Phase",
    "STATE_MACHINE_TRANSITIONS",
    "Signal",
    "advance_queues_on_user_answer",
    "build_harness_instruction",
    "build_interview_harness",
    "build_mcp_toolset",
    "persist_interview_to_json",
]
