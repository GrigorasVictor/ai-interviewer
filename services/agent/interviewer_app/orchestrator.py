"""Two-Queue LLM AI Harness (`InterviewOrchestrator` / `build_interview_harness`).

Communicates with the `mcp` microservice over HTTP using Google ADK's `McpToolset`
(`StreamableHTTPConnectionParams`), and imports all shared state-machine definitions
and queue operations from `shared/contracts` (zero direct imports from `services/mcp`).
"""

from __future__ import annotations

from typing import Any

from contracts import (
    ALLOWED_TOOLS_BY_PHASE,
    END_ACTIONS,
    MIN_QUESTIONS,
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

from .agents.prompts import PHASE_PROMPTS
from .config import build_llm, load_settings


def _extract_current_user_text(ctx: Any) -> str:
    user_content = getattr(ctx, "user_content", None)
    if user_content and getattr(user_content, "parts", None):
        return " ".join(p.text for p in user_content.parts if getattr(p, "text", None)).strip()
    return ""


def _build_prompt_variables(state: Any) -> dict[str, Any]:
    """Extract all template variables from `state` so `PHASE_PROMPTS[phase].format(...)` works without `if/else`."""
    transcript: list[dict[str, Any]] = state.get("transcript") or []
    main_queue: list[str] = state.get("question_queue") or []
    followup_queue: list[dict[str, str]] = state.get("followup_queue") or []
    override = state.get("num_questions_override") or load_settings().num_questions_override

    return {
        "user_name": state.get("user_name") or "(unknown)",
        "user_id": state.get("user_id") or "(unknown)",
        "topic": state.get("topic") or "(unknown)",
        "target_questions": int(override) if override is not None else MIN_QUESTIONS,
        "current_question": state.get("current_question") or "(None — queue finished)",
        "current_followup_context": state.get("current_followup_context") or "what you shared earlier",
        "last_answered_question": state.get("last_answered_question") or "(None)",
        "last_user_answer": state.get("last_user_answer") or "(None)",
        "remaining_main_count": len(main_queue),
        "remaining_followup_count": len(followup_queue),
        "answered_count": len(transcript),
    }


def build_harness_instruction(ctx: ReadonlyContext) -> str:
    """Look up `PHASE_PROMPTS[phase]` and format it with the current session state."""
    phase = str(Phase(ctx.state.get("phase", Phase.INTAKE)))
    template = PHASE_PROMPTS[phase]
    return template.format(**_build_prompt_variables(ctx.state))


def build_mcp_toolset(mcp_url: str | None = None) -> McpToolset:
    """Build the ADK `McpToolset` that discovers and calls tools on the `mcp` microservice via HTTP."""
    settings = load_settings()
    url = (mcp_url or settings.mcp_server_url).strip()
    headers = {"Authorization": f"Bearer {settings.mcp_auth_token}"} if settings.mcp_auth_token else None
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers=headers,
        ),
        tool_filter=["start_interview", "push_followup"],
    )


def build_interview_harness(
    *,
    name: str = "interview_orchestrator",
    model_name: str | None = None,
    num_questions: int | None = None,
    mcp_url: str | None = None,
) -> LlmAgent:
    """Create the root `LlmAgent` harness connected to the MCP server over Streamable HTTP."""

    async def _before_agent_queue_step(callback_context: Any) -> None:
        state = callback_context.state
        if num_questions is not None:
            state["num_questions_override"] = num_questions
        if "phase" not in state:
            state["phase"] = str(Phase.INTAKE)

        phase = Phase(state.get("phase", Phase.INTAKE))
        user_text = _extract_current_user_text(callback_context)
        if phase in {Phase.MAIN_QUEUE, Phase.FOLLOWUP_QUEUE} and user_text:
            advance_queues_on_user_answer(state, user_text)
        elif phase == Phase.DONE and user_text.strip().lower() in RESTART_COMMANDS:
            reset_interview_state(state)

    async def _filter_tools_by_phase_callback(
        callback_context: Any, llm_request: Any
    ) -> None:
        """Expose only the tools in `ALLOWED_TOOLS_BY_PHASE[phase]`."""
        phase = Phase(callback_context.state.get("phase", Phase.INTAKE))
        allowed = set(ALLOWED_TOOLS_BY_PHASE[phase])
        if callback_context.state.get("pushed_this_turn"):
            allowed.discard("push_followup")

        for tool_name in list((getattr(llm_request, "tools_dict", None) or {}).keys()):
            if tool_name not in allowed:
                llm_request.tools_dict.pop(tool_name, None)

        for tool_obj in getattr(getattr(llm_request, "config", None), "tools", None) or []:
            decls = getattr(tool_obj, "function_declarations", None)
            if decls:
                tool_obj.function_declarations = [
                    d for d in decls if getattr(d, "name", None) in allowed
                ]

    async def _sync_mcp_tool_to_session_state(
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        tool_response: Any,
    ) -> dict[str, Any] | None:
        """Apply the MCP tool call (`start_interview` / `push_followup`) to ADK `tool_context.state`."""
        tool_name = getattr(tool, "name", "")
        if tool_name == "start_interview":
            return apply_start_interview_to_state(
                tool_context.state,
                user_name=str(args.get("user_name", "")),
                topic=str(args.get("topic", "")),
                questions=list(args.get("questions") or []),
                user_history=collect_user_messages_text(tool_context),
            )
        if tool_name == "push_followup":
            return apply_push_followup_to_state(
                tool_context.state,
                follow_up_question=str(args.get("follow_up_question", "")),
                referenced_answer=str(args.get("referenced_answer", "")),
            )
        return None

    return LlmAgent(
        name=name,
        description="Conducts a two-queue qualitative research interview (`question_queue` + `followup_queue`).",
        model=build_llm(model_name),
        instruction=build_harness_instruction,
        tools=[build_mcp_toolset(mcp_url)],
        before_agent_callback=_before_agent_queue_step,
        before_model_callback=_filter_tools_by_phase_callback,
        after_tool_callback=_sync_mcp_tool_to_session_state,
        retry_config=RetryConfig(max_attempts=3, initial_delay=0.2, backoff_factor=1.5, jitter=0.0),
    )


# Alias so `InterviewOrchestrator(...)` and `root_agent` keep working seamlessly.
InterviewOrchestrator = build_interview_harness

__all__ = [
    "ALLOWED_TOOLS_BY_PHASE",
    "END_ACTIONS",
    "InterviewOrchestrator",
    "Phase",
    "STATE_MACHINE_TRANSITIONS",
    "Signal",
    "advance_queues_on_user_answer",
    "build_harness_instruction",
    "build_interview_harness",
    "build_mcp_toolset",
    "persist_interview_to_json",
]
