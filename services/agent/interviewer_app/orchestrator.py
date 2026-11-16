"""Two-Queue LLM AI Harness (`InterviewOrchestrator` / `build_interview_harness`).

How the Two-Queue architecture works (zero counters — only `.pop(0)` and `.append()`):
1. `question_queue` (Queue 1 — Main Questions):
   - Populated once at the start via `start_interview(user_name, topic, questions)`.
   - On every user answer, Python pops the next question with `question_queue.pop(0)`.
   - While in `Phase.MAIN_QUEUE`, if an answer warrants a follow-up, the LLM can call
     `push_followup(follow_up_question, referenced_answer)` which appends to `followup_queue`.
2. `followup_queue` (Queue 2 — Follow-up Questions):
   - Once `question_queue` is empty (`[]`), Python pops from `followup_queue.pop(0)`
     and transitions to `Phase.FOLLOWUP_QUEUE`.
   - In `Phase.FOLLOWUP_QUEUE`, `push_followup` is NOT in `ALLOWED_TOOLS_BY_PHASE`, so
     the LLM cannot push a follow-up to a follow-up; it simply asks:
     "Previously you mentioned <context> — <follow_up_question>?"
3. Automatic programmatic completion & save (`Phase.DONE`):
   - As soon as `advance_queues_on_user_answer` detects that both `question_queue` and
     `followup_queue` are empty (`[]`), it programmatically transitions to `Phase.DONE`
     and immediately calls `persist_interview_to_json(state)` (no LLM `end_interview`
     tool call or chat summary needed; analysis/summarization is delegated to a separate
     microservice).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools import ToolContext
from google.adk.workflow import RetryConfig

from .agents.prompts import PHASE_PROMPTS
from .config import _REPO_ROOT, build_llm, load_settings
from .schemas import SKIPPED_ANSWER, IntakeResult, utc_now_iso

MIN_QUESTIONS = 3
MAX_QUESTIONS = 5
MAX_FOLLOWUPS = 3
MIN_ANSWER_WORDS = 3

SKIP_COMMANDS = {"skip", "pass", "next"}
STOP_COMMANDS = {"stop", "quit", "exit", "end", "end interview"}
RESTART_COMMANDS = {"restart", "new", "new interview", "reset"}


# ==============================================================================
# 1. States (`Phase`), Signals (`Signal`), and Maps
# ==============================================================================


class Phase(StrEnum):
    """All states of the Two-Queue interview state machine."""

    INTAKE = "intake"
    MAIN_QUEUE = "main_queue"
    FOLLOWUP_QUEUE = "followup_queue"
    DONE = "done"


class Signal(StrEnum):
    """Signals driving transitions in `STATE_MACHINE_TRANSITIONS`."""

    NEED_MORE_INFO = "need_more_info"  # intake still missing name or topic
    READY = "ready"                    # `question_queue` populated & Q1 popped -> MAIN_QUEUE
    POP_MAIN = "pop_main"              # popped next item from `question_queue` -> MAIN_QUEUE
    POP_FOLLOWUP = "pop_followup"      # `question_queue` empty, popped from `followup_queue` -> FOLLOWUP_QUEUE
    QUEUES_EMPTY = "queues_empty"      # both `question_queue` and `followup_queue` empty -> DONE
    STOP = "stop"                      # user ended early -> DONE
    RESTART = "restart"                # reset state -> INTAKE


# Single source of truth for every state transition: (current_phase, signal) -> next_phase
STATE_MACHINE_TRANSITIONS: dict[Phase, dict[Signal, Phase]] = {
    Phase.INTAKE: {
        Signal.NEED_MORE_INFO: Phase.INTAKE,
        Signal.READY:          Phase.MAIN_QUEUE,
        Signal.STOP:           Phase.DONE,
        Signal.RESTART:        Phase.INTAKE,
    },
    Phase.MAIN_QUEUE: {
        Signal.POP_MAIN:       Phase.MAIN_QUEUE,
        Signal.POP_FOLLOWUP:   Phase.FOLLOWUP_QUEUE,
        Signal.QUEUES_EMPTY:   Phase.DONE,
        Signal.STOP:           Phase.DONE,
        Signal.RESTART:        Phase.INTAKE,
    },
    Phase.FOLLOWUP_QUEUE: {
        Signal.POP_FOLLOWUP:   Phase.FOLLOWUP_QUEUE,
        Signal.QUEUES_EMPTY:   Phase.DONE,
        Signal.STOP:           Phase.DONE,
        Signal.RESTART:        Phase.INTAKE,
    },
    Phase.DONE: {
        Signal.RESTART:        Phase.INTAKE,
        Signal.STOP:           Phase.DONE,
    },
}

# Which tools the LLM can call in each `Phase`.
# Notice:
# - `push_followup` is ONLY allowed in `MAIN_QUEUE`, NEVER in `FOLLOWUP_QUEUE`.
# - Ending & saving when both queues empty is 100% programmatic in Python (no LLM tool needed in `FOLLOWUP_QUEUE` or `DONE`).
ALLOWED_TOOLS_BY_PHASE: dict[Phase, set[str]] = {
    Phase.INTAKE:         {"start_interview"},
    Phase.MAIN_QUEUE:     {"push_followup"},
    Phase.FOLLOWUP_QUEUE: set(),
    Phase.DONE:           set(),
}


def _transition(state: Any, signal: Signal) -> Phase:
    """Look up `(current_phase, signal)` in `STATE_MACHINE_TRANSITIONS` and update `state['phase']`."""
    current = Phase(state.get("phase", Phase.INTAKE))
    next_phase = STATE_MACHINE_TRANSITIONS[current].get(signal, current)
    state["phase"] = str(next_phase)
    return next_phase


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "participant"


def _collect_user_messages_text(ctx: Any) -> str:
    """Collect all text typed by the user in the current session."""
    chunks: list[str] = []
    session = getattr(ctx, "session", None)
    for ev in getattr(session, "events", None) or []:
        if getattr(ev, "author", None) == "user" and getattr(ev, "content", None):
            chunks.extend(p.text for p in (ev.content.parts or []) if getattr(p, "text", None))
    user_content = getattr(ctx, "user_content", None)
    if user_content:
        chunks.extend(p.text for p in (user_content.parts or []) if getattr(p, "text", None))
    return " ".join(chunks).strip()


def _extract_current_user_text(ctx: Any) -> str:
    user_content = getattr(ctx, "user_content", None)
    if user_content and getattr(user_content, "parts", None):
        return " ".join(p.text for p in user_content.parts if getattr(p, "text", None)).strip()
    return ""


# ==============================================================================
# 2. Programmatic Persistence & Queue Advancement (`.pop(0)` / `.append()`)
# ==============================================================================


def _resolve_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if not raw:
        return _REPO_ROOT / "data"
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (_REPO_ROOT / candidate)


def persist_interview_to_json(state: Any) -> Path:
    """Programmatically save the completed interview transcript to `data/interviews_output.json`."""
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    output_file = data_dir / "interviews_output.json"

    existing: list[dict[str, Any]] = []
    if output_file.exists():
        try:
            loaded = json.loads(output_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except Exception:
            existing = []

    record = {
        "interview_id": state.get("interview_id") or str(uuid.uuid4()),
        "user_id": state.get("user_id") or "participant",
        "user_name": state.get("user_name") or "Participant",
        "topic": state.get("topic") or "",
        "status": "pending_analysis",
        "completed_at": utc_now_iso(),
        "transcript": list(state.get("transcript") or []),
    }

    existing = [item for item in existing if item.get("interview_id") != record["interview_id"]]
    existing.append(record)
    output_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    state["saved_to_disk"] = True
    state["saved_path"] = str(output_file)
    return output_file


def _finish_and_persist_if_needed(state: Any, signal: Signal) -> Phase:
    """Transition to `Phase.DONE` and automatically persist to `data/interviews_output.json`."""
    state["question_queue"] = []
    state["followup_queue"] = []
    state["current_question"] = None
    state["current_followup_context"] = None
    next_phase = _transition(state, signal)
    if state.get("transcript") and not state.get("saved_to_disk"):
        persist_interview_to_json(state)
    return next_phase


def advance_queues_on_user_answer(state: Any, user_text: str) -> Phase:
    """Record the user's answer to `current_question` and `.pop(0)` the next question.

    Priority order:
    1. If `question_queue` is non-empty -> `question_queue.pop(0)` (`Phase.MAIN_QUEUE`)
    2. Else if `followup_queue` is non-empty -> `followup_queue.pop(0)` (`Phase.FOLLOWUP_QUEUE`)
    3. Else -> both queues are empty -> programmatically transition to `Phase.DONE`
       and save immediately via `persist_interview_to_json(state)`.
    """
    cleaned = user_text.strip()
    lower = cleaned.lower()

    if lower in STOP_COMMANDS:
        return _finish_and_persist_if_needed(state, Signal.STOP)

    if lower in RESTART_COMMANDS:
        _handle_restart(state)
        return Phase.INTAKE

    current_q = state.get("current_question")
    current_phase = Phase(state.get("phase", Phase.INTAKE))

    if current_q:
        is_skip = lower in SKIP_COMMANDS
        recorded_answer = SKIPPED_ANSWER if is_skip else cleaned
        transcript = list(state.get("transcript") or [])
        transcript.append(
            {
                "source": str(current_phase),
                "question": current_q,
                "context": state.get("current_followup_context"),
                "answer": recorded_answer,
                "answered_at": utc_now_iso(),
            }
        )
        state["transcript"] = transcript
        state["last_answered_question"] = current_q
        state["last_user_answer"] = recorded_answer

        # If in `MAIN_QUEUE` and the user gave a very short answer (< 3 words, not skipped),
        # automatically push a follow-up into `followup_queue` if space remains.
        followup_q_list = list(state.get("followup_queue") or [])
        if (
            current_phase == Phase.MAIN_QUEUE
            and not is_skip
            and 0 < len(cleaned.split()) < MIN_ANSWER_WORDS
            and len(followup_q_list) < MAX_FOLLOWUPS
        ):
            followup_q_list.append(
                {
                    "question": f"Could you elaborate a bit more on your answer regarding: {current_q}",
                    "context": f"your brief answer '{cleaned}' to '{current_q}'",
                }
            )
            state["followup_queue"] = followup_q_list

    main_queue = list(state.get("question_queue") or [])
    followup_queue = list(state.get("followup_queue") or [])
    state["pushed_this_turn"] = False

    if main_queue:
        next_q = main_queue.pop(0)
        state["question_queue"] = main_queue
        state["current_question"] = next_q
        state["current_followup_context"] = None
        return _transition(state, Signal.POP_MAIN)

    if followup_queue:
        next_item = followup_queue.pop(0)
        state["followup_queue"] = followup_queue
        state["current_question"] = next_item["question"]
        state["current_followup_context"] = next_item["context"]
        return _transition(state, Signal.POP_FOLLOWUP)

    return _finish_and_persist_if_needed(state, Signal.QUEUES_EMPTY)


# ==============================================================================
# 3. Deterministic ADK Tools (`tool_context.state`)
# ==============================================================================


def start_interview(
    user_name: str,
    topic: str,
    questions: list[str],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Populate `question_queue` with the initial interview questions and pop Question 1.

    Args:
        user_name: The participant's real name or ID as explicitly stated by the user.
        topic: The subject the user wants to be interviewed about.
        questions: 3 to 5 open-ended interview questions (each ending with '?').
    """
    state = tool_context.state
    user_history = _collect_user_messages_text(tool_context).casefold()
    intake = IntakeResult(user_name=user_name, topic=topic)

    name_in_history = bool(
        intake.user_name
        and any(tok in user_history for tok in intake.user_name.casefold().split() if len(tok) >= 2)
    )
    topic_words = [
        w for w in re.findall(r"[a-z0-9]+", (intake.topic or "").casefold()) if len(w) >= 2
    ]
    topic_in_history = bool(topic_words and any(w in user_history for w in topic_words))
    distinct_fields = (intake.user_name or "").casefold() != (intake.topic or "").casefold()

    if not (intake.is_complete and name_in_history and topic_in_history and distinct_fields):
        _transition(state, Signal.NEED_MORE_INFO)
        return {
            "status": "wait_for_user",
            "phase": state["phase"],
            "next_step": (
                "STOP calling tools on this turn. The user has NOT yet clearly told you both their "
                "name and topic. Reply directly to the user in plain text and ask for what is missing."
            ),
        }

    override = state.get("num_questions_override") or load_settings().num_questions_override
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
        _transition(state, Signal.NEED_MORE_INFO)
        return {
            "status": "invalid_questions",
            "next_step": (
                f"Call `start_interview` again with at least {required_min} distinct open-ended "
                "questions in `questions`."
            ),
        }

    max_allowed = int(override) if override is not None else MAX_QUESTIONS
    queue = cleaned_questions[:max_allowed]
    first_question = queue.pop(0)

    state["user_name"] = intake.user_name
    state["user_id"] = slugify(intake.user_name)
    state["topic"] = intake.topic
    state["interview_id"] = str(uuid.uuid4())
    state["question_queue"] = queue
    state["followup_queue"] = []
    state["current_question"] = first_question
    state["current_followup_context"] = None
    state["last_answered_question"] = None
    state["last_user_answer"] = None
    state["transcript"] = []
    state["pushed_this_turn"] = False
    state["saved_to_disk"] = False
    _transition(state, Signal.READY)

    return {
        "status": "ready",
        "phase": state["phase"],
        "user_name": intake.user_name,
        "topic": intake.topic,
        "popped_question": first_question,
        "remaining_in_question_queue": len(queue),
        "next_step": (
            f"Do NOT call any more tools on this turn. Greet {intake.user_name}, include a "
            "1-sentence privacy note (avoid sharing confidential/PII details; they can type `skip` "
            f"or `stop` anytime), and ask the popped question: '{first_question}'"
        ),
    }


def push_followup(
    follow_up_question: str,
    referenced_answer: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Push a follow-up question onto `followup_queue` to be asked after `question_queue` finishes.

    Args:
        follow_up_question: A single open-ended follow-up question ending with '?'.
        referenced_answer: Short summary or quote of what the user just said that triggered this follow-up.
    """
    state = tool_context.state
    if state.get("phase") != str(Phase.MAIN_QUEUE):
        return {
            "status": "ignored",
            "reason": "`push_followup` is only allowed while draining `main_queue`.",
        }

    followup_queue = list(state.get("followup_queue") or [])
    if state.get("pushed_this_turn") or len(followup_queue) >= MAX_FOLLOWUPS:
        return {
            "status": "queue_full_or_already_pushed",
            "remaining_in_followup_queue": len(followup_queue),
            "next_step": (
                "Do NOT call `push_followup` again on this turn. Now reply to the user and ask "
                f"the current main question: '{state.get('current_question')}'"
            ),
        }

    q = follow_up_question.strip()
    if not q.endswith("?"):
        q = f"{q}?"
    ctx_summary = referenced_answer.strip() or (state.get("last_user_answer") or "your earlier point")

    followup_queue.append({"question": q, "context": ctx_summary})
    state["followup_queue"] = followup_queue
    state["pushed_this_turn"] = True

    # If `current_question` was None (meaning the user just answered the last main question
    # before `push_followup` was called), pop the newly pushed follow-up immediately.
    if not state.get("current_question"):
        next_item = followup_queue.pop(0)
        state["followup_queue"] = followup_queue
        state["current_question"] = next_item["question"]
        state["current_followup_context"] = next_item["context"]
        _transition(state, Signal.POP_FOLLOWUP)
        return {
            "status": "popped_followup_immediately",
            "phase": state["phase"],
            "next_step": (
                f"Ask this follow-up question now, referencing '{next_item['context']}': "
                f"'{next_item['question']}'"
            ),
        }

    return {
        "status": "pushed_to_followup_queue",
        "followup_queue_size": len(followup_queue),
        "next_step": (
            "Follow-up saved in `followup_queue` for the second stage! Do NOT ask the follow-up right now; "
            f"instead, acknowledge their answer and ask the current popped main question: '{state.get('current_question')}'"
        ),
    }


def _handle_restart(state: Any) -> dict[str, Any]:
    override = state.get("num_questions_override") or load_settings().num_questions_override
    state["user_name"] = None
    state["user_id"] = None
    state["topic"] = None
    state["interview_id"] = None
    state["target_questions"] = override or MIN_QUESTIONS
    state["question_queue"] = []
    state["followup_queue"] = []
    state["current_question"] = None
    state["current_followup_context"] = None
    state["last_answered_question"] = None
    state["last_user_answer"] = None
    state["transcript"] = []
    state["pushed_this_turn"] = False
    state["saved_to_disk"] = False
    _transition(state, Signal.RESTART)
    return {
        "status": "restarted",
        "phase": state["phase"],
        "next_step": "Session reset. Ask the user in plain text for their name and the new topic they'd like to explore.",
    }


def _handle_stop(state: Any) -> dict[str, Any]:
    _finish_and_persist_if_needed(state, Signal.STOP)
    transcript = list(state.get("transcript") or [])
    return {
        "status": "stopped",
        "phase": state["phase"],
        "answers_recorded": len(transcript),
        "saved_to_disk": bool(state.get("saved_to_disk")),
        "next_step": "Thank the participant and let them know the interview has been closed.",
    }


END_ACTIONS = {
    "restart": _handle_restart,
    "new": _handle_restart,
    "reset": _handle_restart,
    "stop": _handle_stop,
}


def end_interview(
    action: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Programmatic helper to stop or restart an interview session."""
    handler = END_ACTIONS.get(action.strip().lower(), _handle_stop)
    return handler(tool_context.state)


# ==============================================================================
# 4. Dynamic Prompt Lookup (`PHASE_PROMPTS`) & Harness Factory
# ==============================================================================


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


def build_interview_harness(
    *,
    name: str = "interview_orchestrator",
    model_name: str | None = None,
    num_questions: int | None = None,
) -> LlmAgent:
    """Create the root `LlmAgent` harness driven by `question_queue` and `followup_queue`."""

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
            _handle_restart(state)

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

    return LlmAgent(
        name=name,
        description="Conducts a two-queue qualitative research interview (`question_queue` + `followup_queue`).",
        model=build_llm(model_name),
        instruction=build_harness_instruction,
        tools=[start_interview, push_followup],
        before_agent_callback=_before_agent_queue_step,
        before_model_callback=_filter_tools_by_phase_callback,
        retry_config=RetryConfig(max_attempts=3, initial_delay=0.2, backoff_factor=1.5, jitter=0.0),
    )


# Alias so `InterviewOrchestrator(...)` and `root_agent` keep working seamlessly.
InterviewOrchestrator = build_interview_harness
