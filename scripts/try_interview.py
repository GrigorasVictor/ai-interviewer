"""Interactive CLI runner to play with the Two-Queue `LlmAgent` interview harness in your terminal.

Examples:
  1. Live LLM mode with thinking enabled (uses `LLM_MODEL` from `.env`):
       .venv\\Scripts\\python.exe scripts/try_interview.py

  2. Override the number of initial questions (e.g. only 2 questions):
       .venv\\Scripts\\python.exe scripts/try_interview.py --questions 2

  3. Hide internal `<think>` traces in terminal output:
       .venv\\Scripts\\python.exe scripts/try_interview.py --hide-thoughts
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make sure `shared` and `services/agent` are importable when running from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "shared", REPO_ROOT / "services" / "agent", REPO_ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from interviewer_app.config import load_settings
from interviewer_app.orchestrator import build_interview_harness


async def main(num_questions: int | None, hide_thoughts: bool) -> None:
    settings = load_settings()
    q_label = num_questions or settings.num_questions_override or "auto (3-5)"
    print("=" * 72)
    print(
        f" Mini AI Interviewer — Two-Queue CLI "
        f"[model={settings.llm_model} | thinking={settings.llm_thinking} | questions={q_label}]"
    )
    print(" Commands inside the interview: `skip`, `stop`, `restart`, or `Ctrl+C` to quit.")
    print("=" * 72)

    session_service = InMemorySessionService()
    orchestrator = build_interview_harness(num_questions=num_questions)
    runner = Runner(
        app_name="cli_interviewer",
        agent=orchestrator,
        session_service=session_service,
    )
    session = await session_service.create_session(app_name="cli_interviewer", user_id="cli_user")

    print("\nTip: start with e.g.  Hi, I'm Victor and I want to talk about AI in the workplace\n")

    while True:
        try:
            user_input = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        content = types.Content(role="user", parts=[types.Part.from_text(text=user_input)])
        print()
        async for event in runner.run_async(
            user_id="cli_user", session_id=session.id, new_message=content
        ):
            if not (event.content and event.content.parts):
                continue
            for part in event.content.parts:
                if getattr(part, "function_call", None):
                    print(f"   [tool] {part.function_call.name}({dict(part.function_call.args)})")
                elif getattr(part, "thought", False) and getattr(part, "text", None):
                    if not hide_thoughts:
                        thought_clean = part.text.strip().replace("\n", "\n   ")
                        print(f"   [thinking] {thought_clean}\n")
                elif getattr(part, "text", None):
                    print(part.text)

        current = await session_service.get_session(
            app_name="cli_interviewer", user_id="cli_user", session_id=session.id
        )
        main_q_left = len(current.state.get("question_queue") or [])
        followup_q_left = len(current.state.get("followup_queue") or [])
        print(
            f"\n   [state] phase={current.state.get('phase')} | "
            f"question_queue={main_q_left} left | "
            f"followup_queue={followup_q_left} left | "
            f"saved={current.state.get('saved_to_disk', False)}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Play an interview in the terminal.")
    parser.add_argument(
        "-q",
        "--questions",
        type=int,
        default=None,
        help="Override the number of initial interview questions (or set INTERVIEW_NUM_QUESTIONS in .env).",
    )
    parser.add_argument(
        "--hide-thoughts",
        action="store_true",
        help="Hide the model's internal [thinking] trace in terminal output.",
    )
    args = parser.parse_args()
    asyncio.run(main(num_questions=args.questions, hide_thoughts=args.hide_thoughts))
