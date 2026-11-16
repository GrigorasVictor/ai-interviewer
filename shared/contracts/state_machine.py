"""Shared Two-Queue state machine and per-user JSON storage (`data/<user_id>.json`).

Lifecycle of a user's interview file (`data/<user_id>.json`):
- While questions are still being asked (`main_queue` / `followup_queue`):
  `status = "in_progress"`, `processed = False`
- As soon as both queues are empty (`phase = "done"`):
  `status = "pending_analysis"`, `processed = False`
- Once `services/analysis` finishes enriching the transcript + Neo4j:
  `status = "analyzed"`, `processed = True`
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

PERSISTED_KEYS = (
    "interview_id",
    "user_id",
    "user_name",
    "topic",
    "status",
    "processed",
    "phase",
    "question_queue",
    "followup_queue",
    "current_question",
    "current_followup_context",
    "transcript",
    "completed_at",
)

_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _PARENTS[2] if len(_PARENTS) > 2 else _PARENTS[-1]


class Phase(StrEnum):
    """The 4 phases of the interview."""

    INTAKE = "intake"
    MAIN_QUEUE = "main_queue"
    FOLLOWUP_QUEUE = "followup_queue"
    DONE = "done"


class Signal(StrEnum):
    """Signals that transition between phases in `STATE_MACHINE_TRANSITIONS`."""

    NEED_MORE_INFO = "need_more_info"
    READY = "ready"
    POP_MAIN = "pop_main"
    POP_FOLLOWUP = "pop_followup"
    QUEUES_EMPTY = "queues_empty"
    STOP = "stop"
    RESTART = "restart"


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

ALLOWED_TOOLS_BY_PHASE: dict[Phase, set[str]] = {
    Phase.INTAKE:         {"start_interview"},
    Phase.MAIN_QUEUE:     {"push_followup"},
    Phase.FOLLOWUP_QUEUE: set(),
    Phase.DONE:           set(),
}


def transition_phase(state: Any, signal: Signal) -> Phase:
    current = Phase(state.get("phase", Phase.INTAKE))
    next_phase = STATE_MACHINE_TRANSITIONS[current].get(signal, current)
    state["phase"] = str(next_phase)
    return next_phase


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "participant"


def parse_num_questions_env() -> int | None:
    raw = os.getenv("INTERVIEW_NUM_QUESTIONS", "").strip()
    return max(1, int(raw)) if raw else None


def resolve_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if not raw:
        return _REPO_ROOT / "data"
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (_REPO_ROOT / candidate)


def user_json_path(user_id: str) -> Path:
    """Path to the user's JSON file: `data/<user_id>.json`."""
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / f"{slugify(user_id)}.json"


def persist_interview_to_json(state: Any) -> Path:
    """Save the user's interview to `data/<user_id>.json` (works with `dict` or ADK `State`)."""
    uid = state.get("user_id") or slugify(state.get("user_name") or "participant")
    state["user_id"] = uid
    if state.get("processed") is None:
        state["processed"] = False
    path = user_json_path(uid)
    record = {key: state.get(key) for key in PERSISTED_KEYS}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_user_interview(user_id: str) -> dict[str, Any] | None:
    """Load `data/<user_id>.json` if it exists."""
    path = user_json_path(user_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def finish_and_persist_if_needed(state: Any, signal: Signal) -> Phase:
    """Mark both queues empty, transition to `DONE`, set `status='pending_analysis'` & `processed=False`, and save."""
    state["question_queue"] = []
    state["followup_queue"] = []
    state["current_question"] = None
    state["current_followup_context"] = None
    state["status"] = "pending_analysis"
    state["processed"] = False
    state["completed_at"] = utc_now_iso()
    next_phase = transition_phase(state, signal)
    if state.get("transcript"):
        persist_interview_to_json(state)
    return next_phase


def reset_interview_state(state: Any) -> dict[str, Any]:
    """Reset the user's state back to `intake`."""
    override = state.get("num_questions_override") or parse_num_questions_env()
    state.update({key: None for key in PERSISTED_KEYS})
    state.update(
        {
            "phase": str(Phase.INTAKE),
            "processed": False,
            "target_questions": override or MIN_QUESTIONS,
            "question_queue": [],
            "followup_queue": [],
            "last_answered_question": None,
            "last_user_answer": None,
            "transcript": [],
            "pushed_this_turn": False,
        }
    )
    return {"status": "restarted", "phase": state["phase"]}


def stop_interview_state(state: Any) -> dict[str, Any]:
    finish_and_persist_if_needed(state, Signal.STOP)
    return {"status": "stopped", "phase": state["phase"]}


END_ACTIONS = {
    "restart": reset_interview_state,
    "new": reset_interview_state,
    "reset": reset_interview_state,
    "stop": stop_interview_state,
}


def apply_start_interview_to_state(
    state: Any,
    *,
    user_name: str = "",
    topic: str = "",
    questions: list[str] | None = None,
    user_history: str = "",
) -> dict[str, Any]:
    """Validate intake + initial questions, pop Q1, and save `data/<user_id>.json`."""
    if not user_name.strip() and not topic.strip() and not (questions or []):
        transition_phase(state, Signal.NEED_MORE_INFO)
        return {
            "status": "missing_tool_arguments",
            "phase": state["phase"],
            "next_step": (
                "`start_interview` was called with empty arguments. Call it again as "
                "`start_interview(user_name=\"...\", topic=\"...\", questions=[\"...?\", \"...?\", \"...?\"])`."
            ),
        }

    intake = IntakeResult(user_name=user_name, topic=topic)
    history_cf = user_history.casefold()

    name_tokens = [tok for tok in re.findall(r"[a-z0-9]+", (intake.user_name or "").casefold()) if len(tok) >= 2]
    name_ok = not history_cf or bool(name_tokens and any(tok in history_cf for tok in name_tokens))
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
            "next_step": "Ask the user in plain text for their name and the topic they want to discuss.",
        }

    override = state.get("num_questions_override") or parse_num_questions_env()
    target_count = int(override) if override is not None else MIN_QUESTIONS

    cleaned_questions: list[str] = []
    for raw_q in questions or []:
        q = str(raw_q).strip()
        if q:
            q = q if q.endswith("?") else f"{q}?"
            if q not in cleaned_questions:
                cleaned_questions.append(q)

    required_min = min(target_count, MIN_QUESTIONS)
    if len(cleaned_questions) < required_min:
        transition_phase(state, Signal.NEED_MORE_INFO)
        return {
            "status": "invalid_questions",
            "phase": state["phase"],
            "next_step": (
                f"Call `start_interview` again with at least {required_min} distinct "
                "open-ended questions in `questions`."
            ),
        }

    max_allowed = int(override) if override is not None else MAX_QUESTIONS
    queue = cleaned_questions[:max_allowed]
    first_question = queue.pop(0)

    state.update(
        {
            "interview_id": str(uuid.uuid4()),
            "user_name": intake.user_name,
            "user_id": slugify(intake.user_name),
            "topic": intake.topic,
            "status": "in_progress",
            "processed": False,
            "question_queue": queue,
            "followup_queue": [],
            "current_question": first_question,
            "current_followup_context": None,
            "last_answered_question": None,
            "last_user_answer": None,
            "transcript": [],
            "pushed_this_turn": False,
        }
    )
    transition_phase(state, Signal.READY)
    persist_interview_to_json(state)

    return {
        "status": "ready",
        "phase": state["phase"],
        "user_id": state["user_id"],
        "user_name": intake.user_name,
        "topic": intake.topic,
        "popped_question": first_question,
        "remaining_in_question_queue": len(queue),
        "next_step": (
            f"Greet {intake.user_name}, mention a 1-sentence privacy note (they can type `skip` or `stop` "
            f"anytime), and ask Question 1: '{first_question}'"
        ),
    }


def apply_push_followup_to_state(
    state: Any,
    *,
    follow_up_question: str = "",
    referenced_answer: str = "",
) -> dict[str, Any]:
    """Append a follow-up question onto `followup_queue` and update `data/<user_id>.json`."""
    if state.get("phase") != str(Phase.MAIN_QUEUE):
        return {"status": "ignored", "reason": "Only allowed in `main_queue`."}

    if not follow_up_question.strip():
        return {"status": "ignored", "reason": "Empty follow_up_question."}

    followup_queue = list(state.get("followup_queue") or [])
    if state.get("pushed_this_turn") or len(followup_queue) >= MAX_FOLLOWUPS:
        return {"status": "queue_full_or_already_pushed"}

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

    persist_interview_to_json(state)
    return {
        "status": "pushed_to_followup_queue",
        "followup_queue_size": len(state["followup_queue"]),
        "next_step": f"Now ask the current main question: '{state.get('current_question')}'",
    }


def advance_queues_on_user_answer(state: Any, user_text: str) -> Phase:
    """Record the user's answer, `.pop(0)` from `question_queue` then `followup_queue`, and update `data/<user_id>.json`."""
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
        state["current_question"] = main_queue.pop(0)
        state["question_queue"] = main_queue
        state["current_followup_context"] = None
        phase = transition_phase(state, Signal.POP_MAIN)
        persist_interview_to_json(state)
        return phase

    if followup_queue:
        next_item = followup_queue.pop(0)
        state["followup_queue"] = followup_queue
        state["current_question"] = next_item["question"]
        state["current_followup_context"] = next_item["context"]
        phase = transition_phase(state, Signal.POP_FOLLOWUP)
        persist_interview_to_json(state)
        return phase

    return finish_and_persist_if_needed(state, Signal.QUEUES_EMPTY)


def collect_user_messages_text(ctx: Any) -> str:
    chunks: list[str] = []
    session = getattr(ctx, "session", None)
    for ev in getattr(session, "events", None) or []:
        if getattr(ev, "author", None) == "user" and getattr(ev, "content", None):
            chunks.extend(p.text for p in (ev.content.parts or []) if getattr(p, "text", None))
    user_content = getattr(ctx, "user_content", None)
    if user_content:
        chunks.extend(p.text for p in (user_content.parts or []) if getattr(p, "text", None))
    return " ".join(chunks).strip()
