"""Configuration and LiteLLM model factory for the agent service.

Everything comes from environment variables (or the root `.env` file when
running locally). We use `LiteLlm` exclusively so switching between a local
Ollama model (`ollama_chat/...`) and a cloud OpenRouter model (`openrouter/...`)
is a one-line change in `.env` with zero code edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.adk.models import LiteLlm

# Load the repo-root `.env` if present (no-op in Docker where env vars are injected by Compose).
_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _PARENTS[3] if len(_PARENTS) > 3 else _PARENTS[-1]
load_dotenv(_REPO_ROOT / ".env", override=False)


def _parse_optional_positive_int(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    value = int(raw)
    if value < 1:
        raise ValueError(f"INTERVIEW_NUM_QUESTIONS must be >= 1, got {value}")
    return value


def _parse_bool(raw: str, default: bool = True) -> bool:
    cleaned = raw.strip().lower()
    if not cleaned:
        return default
    return cleaned in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentSettings:
    """Typed snapshot of the environment variables used by the agent service."""

    llm_model: str
    llm_model_summary: str
    ollama_api_base: str
    llm_thinking: bool
    num_questions_override: int | None
    mcp_server_url: str
    mcp_auth_token: str
    log_level: str


def load_settings() -> AgentSettings:
    """Read settings from environment variables with sensible local defaults."""
    llm_model = os.getenv("LLM_MODEL", "ollama_chat/qwen3:1.7b").strip()
    summary_model = os.getenv("LLM_MODEL_SUMMARY", "").strip() or llm_model
    return AgentSettings(
        llm_model=llm_model,
        llm_model_summary=summary_model,
        ollama_api_base=os.getenv("OLLAMA_API_BASE", "http://localhost:11434").strip(),
        llm_thinking=_parse_bool(os.getenv("LLM_THINKING", "true"), default=True),
        num_questions_override=_parse_optional_positive_int(
            os.getenv("INTERVIEW_NUM_QUESTIONS", "")
        ),
        mcp_server_url=os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp").strip(),
        mcp_auth_token=os.getenv("MCP_AUTH_TOKEN", "").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip(),
    )


def build_llm(model_name: str | None = None, *, thinking: bool | None = None) -> LiteLlm:
    """Build a `LiteLlm` wrapper for Ollama (`ollama_chat/...`) or OpenRouter (`openrouter/...`).

    Google ADK + LiteLLM natively parses reasoning traces into `Part(thought=True, text=...)`
    separate from tool calls and final user-facing text.
    """
    settings = load_settings()
    chosen = (model_name or settings.llm_model).strip()
    enable_thinking = settings.llm_thinking if thinking is None else thinking

    extra_kwargs: dict[str, Any] = {}
    if chosen.startswith(("ollama/", "ollama_chat/")):
        extra_kwargs["api_base"] = settings.ollama_api_base
        if "qwen3" in chosen.lower():
            extra_kwargs["think"] = enable_thinking

    return LiteLlm(model=chosen, **extra_kwargs)
