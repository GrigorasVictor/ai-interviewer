"""Interview analysis pipeline (`services/analysis/analysis_app/pipeline.py`).

Clean, status/flag-driven flow (no file hashes):
1. While an interview is running, `data/<user_id>.json` has:
   `phase != "done"`, `status = "in_progress"`, `processed = False`.
2. The moment both queues finish, the state machine sets:
   `phase = "done"`, `status = "pending_analysis"`, `processed = False`.
3. `AnalysisPipeline` picks up any file where `phase == "done"` and `processed is False`
   (or `status == "pending_analysis"`), logs every step across the NLP / LLM models and
   Neo4j ingestion, and saves `processed = True` + `status = "analyzed"`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from contracts import SKIPPED_ANSWER, AnswerAnalysis, Sentiment, utc_now_iso

from .config import AnalysisSettings, load_analysis_settings
from .llm_analyzers import generate_ai_summary, generate_emotion_and_insights
from .neo4j_store import Neo4jGraphStore
from .nlp_models import KeywordEngine, SentimentEngine

logger = logging.getLogger("analysis_app")

AnalyzerCallable = Callable[[dict[str, Any], Sentiment, AnalysisSettings], dict[str, Any]]


def is_ready_for_analysis(record: dict[str, Any], *, force: bool = False) -> bool:
    """Return True only when the interview has finished (`phase == 'done'`) and is not yet processed."""
    done_with_transcript = record.get("phase") == "done" and bool(record.get("transcript"))
    needs_processing = force or not bool(record.get("processed")) or record.get("status") == "pending_analysis"
    return done_with_transcript and needs_processing


class AnalysisPipeline:
    """Status-driven interview analysis pipeline + Neo4j graph sync with step-by-step logging."""

    def __init__(
        self,
        settings: AnalysisSettings | None = None,
        *,
        summary_fn: AnalyzerCallable = generate_ai_summary,
        insights_fn: AnalyzerCallable = generate_emotion_and_insights,
        sync_neo4j: bool = True,
    ) -> None:
        self.settings = settings or load_analysis_settings()
        self.summary_fn = summary_fn
        self.insights_fn = insights_fn
        self.sync_neo4j = sync_neo4j
        self.sentiment_engine = SentimentEngine(self.settings.sentiment_model)
        self.keyword_engine = KeywordEngine(self.settings.keyword_model)
        self.neo4j_store = Neo4jGraphStore(self.settings)
        logger.info(
            "Pipeline initialized | sentiment_model=%s | keyword_model=%s | summary_llm=%s | insights_llm=%s | neo4j_uri=%s",
            self.sentiment_engine.model_name,
            self.keyword_engine.model_name,
            self.settings.summary_model,
            self.settings.insights_model,
            self.settings.neo4j_uri or "(disabled)",
        )

    def _analyze_turn(
        self,
        turn: dict[str, Any],
        idx: int,
        total_turns: int,
        interview_id: str,
    ) -> Sentiment | None:
        answer_id = f"{interview_id}:{idx}"
        answer_text = str(turn.get("answer") or "").strip()

        if not answer_text or answer_text == SKIPPED_ANSWER:
            turn["analysis"] = None
            logger.info("  [Turn %d/%d] Skipped by user -> analysis=None", idx, total_turns)
            return None

        logger.info(
            "  [Turn %d/%d] Running NLP (%s + %s) on answer: %r",
            idx,
            total_turns,
            self.sentiment_engine.model_name,
            self.keyword_engine.model_name,
            answer_text[:100],
        )
        sentiment = self.sentiment_engine.analyze_text(answer_text)
        keywords = self.keyword_engine.extract(answer_text, top_n=5)

        logger.info(
            "  [Turn %d/%d] -> sentiment=%s (%.3f) | keywords=%s",
            idx,
            total_turns,
            sentiment.label,
            sentiment.score,
            [f"{k.phrase} ({k.score})" for k in keywords],
        )

        turn["analysis"] = AnswerAnalysis(
            answer_id=answer_id,
            sentiment=sentiment,
            keywords=keywords,
        ).model_dump(mode="json")
        return sentiment

    def analyze_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Enrich `record` in-place with raw per-turn NLP, raw AI summary, raw AI insights, and Neo4j sync."""
        interview_id = str(record.get("interview_id") or "")
        user_id = str(record.get("user_id") or "")
        topic = str(record.get("topic") or "")
        transcript = list(record.get("transcript") or [])

        logger.info(
            "=== Starting analysis for user='%s' | interview_id='%s' | topic='%s' | turns=%d ===",
            user_id,
            interview_id,
            topic,
            len(transcript),
        )

        turn_sentiments = [
            self._analyze_turn(turn, idx, len(transcript), interview_id)
            for idx, turn in enumerate(transcript, start=1)
        ]
        non_skipped_sentiments = [s for s in turn_sentiments if s is not None]

        session_sentiment = self.sentiment_engine.aggregate_session_sentiment(non_skipped_sentiments)
        logger.info(
            "  [Session Sentiment] Aggregated across %d answer(s) -> label=%s, score=%.3f",
            len(non_skipped_sentiments),
            session_sentiment.label,
            session_sentiment.score,
        )

        logger.info("  [Summary LLM] Calling '%s' for full-transcript summary...", self.settings.summary_model)
        summary = self.summary_fn(record, session_sentiment, self.settings)
        logger.info("  [Summary LLM] Raw output -> %s", json.dumps(summary, ensure_ascii=False))

        logger.info("  [Insights LLM] Calling '%s' for emotion, tone & actionable insights...", self.settings.insights_model)
        insights = self.insights_fn(record, session_sentiment, self.settings)
        logger.info("  [Insights LLM] Raw output -> %s", json.dumps(insights, ensure_ascii=False))

        record["transcript"] = transcript
        record["status"] = "analyzed"
        record["processed"] = True
        record["analysis_report"] = {
            "analyzed_at": utc_now_iso(),
            "session_sentiment": session_sentiment.model_dump(mode="json"),
            "summary": summary,
            "emotion_and_insights": insights,
            "models_used": {
                "sentiment": self.sentiment_engine.model_name,
                "keywords": self.keyword_engine.model_name,
                "summary_llm": self.settings.summary_model,
                "insights_llm": self.settings.insights_model,
            },
        }

        if self.sync_neo4j:
            neo4j_result = self.neo4j_store.sync_interview(record)
            record["analysis_report"]["neo4j"] = neo4j_result
            record["neo4j_synced"] = neo4j_result["status"] == "ok"

        logger.info(
            "=== Finished analysis for user='%s' (processed=True, neo4j_synced=%s) ===",
            user_id,
            record.get("neo4j_synced"),
        )
        return record

    def _try_resync_neo4j(self, file_path: Path, raw_data: dict[str, Any]) -> dict[str, Any] | None:
        if not (self.sync_neo4j and not raw_data.get("neo4j_synced") and self.settings.neo4j_uri):
            return None
        logger.info("File '%s' is already processed but neo4j_synced=False -> retrying Neo4j sync...", file_path.name)
        neo4j_res = self.neo4j_store.sync_interview(raw_data)
        if neo4j_res["status"] != "ok":
            return None
        raw_data.setdefault("analysis_report", {})["neo4j"] = neo4j_res
        raw_data["neo4j_synced"] = True
        file_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"file": file_path.name, "status": "neo4j_resynced"}

    def process_file(self, file_path: Path, *, force: bool = False) -> dict[str, Any]:
        """Process a single `data/<user_id>.json` file when `phase == 'done'` and `processed == False`."""
        raw_data = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            return {"file": file_path.name, "status": "skipped_invalid_format"}

        if raw_data.get("phase") != "done" or not raw_data.get("transcript"):
            logger.debug("Skipping '%s': interview still in progress (phase=%s).", file_path.name, raw_data.get("phase"))
            return {"file": file_path.name, "status": "skipped_in_progress"}

        if not is_ready_for_analysis(raw_data, force=force):
            return self._try_resync_neo4j(file_path, raw_data) or {
                "file": file_path.name,
                "status": "already_processed",
            }

        logger.info(
            "Detected completed unprocessed interview '%s' (status=%s, processed=%s).",
            file_path.name,
            raw_data.get("status"),
            raw_data.get("processed"),
        )
        updated = self.analyze_record(raw_data)
        file_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved enriched report to '%s'.", file_path)
        return {
            "file": file_path.name,
            "status": "analyzed",
            "processed": True,
            "neo4j_synced": bool(updated.get("neo4j_synced")),
        }

    def process_directory(self, data_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
        """Scan `data_dir` for `*.json` interview files and analyze any finished, unprocessed interviews."""
        target_dir = data_dir or self.settings.data_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        results = [
            self.process_file(file_path, force=force)
            for file_path in sorted(target_dir.glob("*.json"))
            if not file_path.name.startswith(".")
        ]
        return {
            "data_dir": str(target_dir),
            "analyzed": [r["file"] for r in results if r["status"] == "analyzed"],
            "already_processed": [r["file"] for r in results if r["status"] == "already_processed"],
            "skipped_in_progress": [r["file"] for r in results if r["status"] == "skipped_in_progress"],
            "results": results,
        }


def process_interview_file(file_path: Path, *, force: bool = False) -> dict[str, Any]:
    return AnalysisPipeline().process_file(file_path, force=force)


def process_data_directory(data_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    return AnalysisPipeline().process_directory(data_dir, force=force)
