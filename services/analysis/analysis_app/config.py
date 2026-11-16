"""Configuration for the `services/analysis` pipeline service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _PARENTS[3] if len(_PARENTS) > 3 else _PARENTS[-1]
load_dotenv(_REPO_ROOT / ".env", override=False)


def _resolve_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if not raw:
        return _REPO_ROOT / "data"
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (_REPO_ROOT / candidate)


@dataclass(frozen=True)
class AnalysisSettings:
    """Environment configuration for the analysis pipeline + Neo4j graph store."""

    data_dir: Path
    summary_model: str
    insights_model: str
    ollama_api_base: str
    sentiment_model: str
    keyword_model: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    poll_interval_seconds: float


def load_analysis_settings() -> AnalysisSettings:
    llm_default = os.getenv("LLM_MODEL", "ollama_chat/qwen3:1.7b").strip()
    summary_model = os.getenv("LLM_MODEL_SUMMARY", "").strip() or llm_default
    insights_model = os.getenv("LLM_MODEL_INSIGHTS", "").strip() or "ollama_chat/qwen3:1.7b"
    return AnalysisSettings(
        data_dir=_resolve_data_dir(),
        summary_model=summary_model,
        insights_model=insights_model,
        ollama_api_base=os.getenv("OLLAMA_API_BASE", "http://localhost:11434").strip(),
        sentiment_model=os.getenv(
            "SENTIMENT_MODEL", "cardiffnlp/twitter-roberta-base-sentiment-latest"
        ).strip(),
        keyword_model=os.getenv(
            "KEYWORD_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ).strip(),
        neo4j_uri=os.getenv("NEO4J_URI", "").strip(),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j").strip(),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "").strip(),
        poll_interval_seconds=float(os.getenv("ANALYSIS_POLL_SECONDS", "5")),
    )
