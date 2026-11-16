"""Shared Two-Queue state machine constants, enums, transition maps, and queue logic.

Used by both `services/agent` (`orchestrator.py`) and `services/mcp` (`server.py`)
so neither microservice imports private internals from the other.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from .common import utc_now_iso
from .llm_outputs import IntakeResult
from .record import SKIPPED_ANSWER

MIN_QUESTIONS = 3
MAX_QUESTIONS = 5
MAX_FOLLOWUPS = 3
MIN_ANSWER_WORDS = 3

SKIP_COMMANDS = {"skip", "pass", "next"}
STOP_COMMANDS = {"stop", "quit", "exit", "end", "end interview"}
RESTART_COMMANDS = {"restart", "new", "new interview", "reset"}

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
ALLOWED_TOOLS_BY_PHASE: dict[Phase, set[str]] = {
    Phase.INTAKE:         {"start_interview"},
    Phase.MAIN_QUEUE:     {"push_followup"},
    Phase.FOLLOWUP_QUEUE: set(),
    Phase.DONE:           set(),
}


def transition_phase(state: Any, signal: Signal) -> Phase:
    """Look up `(current_phase, signal)` in `STATE_MACHINE_TRANSITIONS` and update `state['phase']`."""
    current = Phase(state.get("phase", Phase.INTAKE))
    next_phase = STATE_MACHINE_TRANSITIONS[current].get(signal, current)
    state["phase"] = str(next_phase)
    return next_phase


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "participant"


def parse_num_questions_env() -> int | None:
    raw = os.getenv("INTERVIEW_NUM_QUESTIONS", "").strip()
    if not raw:
        return None
    return max(1, int(raw))


def resolve_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if not raw:
        return _REPO_ROOT / "data"
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (_REPO_ROOT / candidate)


def collect_user_messages_text(ctx: Any) -> str:
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


def persist_interview_to_json(state: Any) -> Path:
    """Programmatically save the completed interview transcript to `data/interviews_output.json`."""
    data_dir = resolve_data_dir()
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


def finish_and_persist_if_needed(state: Any, signal: Signal) -> Phase:
    """Transition to `Phase.DONE` and automatically persist to `data/interviews_output.json`."""
    state["question_queue"] = []
    state["followup_queue"] = []
    state["current_question"] = None
    state["current_followup_context"] = None
    next_phase = transition_phase(state, signal)
    if state.get("transcript") and not state.get("saved_to_disk"):
        persist_interview_to_json(state)
    return next_phase


def reset_interview_state(state: Any) -> dict[str, Any]:
    """Reset the session state back to `Phase.INTAKE`."""
    override = state.get("num_questions_override") or parse_num_questions_env()
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
    transition_phase(state, Signal.RESTART)
    return {
        "status": "restarted",
        "phase": state["phase"],
        "next_step": "Session reset. Ask the user in plain text for their name and the new topic they'd like to explore.",
    }


def stop_interview_state(state: Any) -> dict[str, Any]:
    """Stop the current interview early and persist any collected answers."""
    finish_and_persist_if_needed(state, Signal.STOP)
    transcript = list(state.get("transcript") or [])
    return {
        "status": "stopped",
        "phase": state["phase"],
        "answers_recorded": len(transcript),
        "saved_to_disk": bool(state.get("saved_to_disk")),
        "next_step": "Thank the participant and let them know the interview has been closed.",
    }


END_ACTIONS = {
    "restart": reset_interview_state,
    "new": reset_interview_state,
    "reset": reset_interview_state,
    "stop": stop_interview_state,
}


def apply_start_interview_to_state(
    state: Any,
    *,
    user_name: str,
    topic: str,
    questions: list[str],
    user_history: str = "",
) -> dict[str, Any]:
    """Validate intake + initial questions and initialize `question_queue` & `followup_queue` in `state`."""
    intake = IntakeResult(user_name=user_name, topic=topic)
    history_cf = user_history.casefold()

    name_ok = not history_cf or bool(
        intake.user_name
        and any(tok in history_cf for tok in intake.user_name.casefold().split() if len(tok) >= 2)
    )
    topic_words = [
        w for w in re.findall(r"[a-z0-9]+", (intake.topic or "").casefold()) if len(w) >= 2
    ]
    topic_ok = not history_cf or bool(topic_words and any(w in history_cf for w in topic_words))
    distinct_fields = (intake.user_name or "").casefold() != (intake.topic or "").casefold()

    if not (intake.is_complete and name_ok and topic_ok and distinct_fields):
        transition_phase(state, Signal.NEED_MORE_INFO)
        return {
            "status": "wait_for_user",
            "phase": state["phase"],
            "next_step": (
                "STOP calling tools on this turn. The user has NOT yet clearly told you both their "
                "name and topic. Reply directly to the user in plain text and ask for what is missing."
            ),
        }

    override = state.get("num_questions_override") or parse_num_questions_env()
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
        transition_phase(state, Signal.NEED_MORE_INFO)
        return {
            "status": "invalid_questions",
            "phase": state["phase"],
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
    transition_phase(state, Signal.READY)

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


def apply_push_followup_to_state(
    state: Any,
    *,
    follow_up_question: str,
    referenced_answer: str,
) -> dict[str, Any]:
    """Push a follow-up question onto `state['followup_queue']` while draining `main_queue`."""
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

    if not state.get("current_question"):
        next_item = followup_queue.pop(0)
        state["followup_queue"] = followup_queue
        state["current_question"] = next_item["question"]
        state["current_followup_context"] = next_item["context"]
        transition_phase(state, Signal.POP_FOLLOWUP)
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
        return finish_and_persist_if_needed(state, Signal.STOP)

    if lower in RESTART_COMMANDS:
        reset_interview_state(state)
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
        return transition_phase(state, Signal.POP_MAIN)

    if followup_queue:
        next_item = followup_queue.pop(0)
        state["followup_queue"] = followup_queue
        state["current_question"] = next_item["question"]
        state["current_followup_context"] = next_item["context"]
        return transition_phase(state, Signal.POP_FOLLOWUP)

    return finish_and_persist_if_needed(state, Signal.QUEUES_EMPTY)
