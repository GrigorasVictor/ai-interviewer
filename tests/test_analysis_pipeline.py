"""Tests for the status/processed-driven interview analysis pipeline (`services/analysis`)."""

from __future__ import annotations

import json
from pathlib import Path

from analysis_app.llm_analyzers import _build_json_schema_response_format
from analysis_app.neo4j_store import build_neo4j_parameters
from analysis_app.pipeline import AnalysisPipeline
from contracts import EmotionToneAnalysis, InterviewSummary


def _sample_interview(user_id: str = "andrei", *, phase: str = "done") -> dict:
    return {
        "interview_id": "2b16b3da-15bf-417e-86d8-62d458bad3d0",
        "user_id": user_id,
        "user_name": user_id.capitalize(),
        "topic": "AI productivity",
        "status": "pending_analysis" if phase == "done" else "in_progress",
        "processed": False,
        "phase": phase,
        "question_queue": [] if phase == "done" else ["Next question?"],
        "followup_queue": [],
        "current_question": None if phase == "done" else "Current question?",
        "current_followup_context": None,
        "transcript": [
            {
                "source": "main_queue",
                "question": "What does 'AI productivity' mean to you personally?",
                "context": None,
                "answer": "It helps me be twice as productive and ship features much faster.",
                "answered_at": "2026-11-16T19:58:14Z",
            },
            {
                "source": "main_queue",
                "question": "How have you used AI tools so far?",
                "context": None,
                "answer": "I use AI daily to generate code, but I still need to debug edge cases.",
                "answered_at": "2026-11-16T19:58:56Z",
            },
            {
                "source": "main_queue",
                "question": "What would make AI genuinely productive for you?",
                "context": None,
                "answer": "[skipped]",
                "answered_at": "2026-11-16T20:00:11Z",
            },
        ],
        "completed_at": "2026-11-16T20:00:11Z" if phase == "done" else None,
    }


def _stub_summary(record, session_sentiment, settings) -> dict:
    return {
        "overview": f"Summary for {record['user_name']} on {record['topic']}.",
        "themes": [record["topic"], "Developer Velocity"],
        "key_points": ["Faster feature delivery", "Debugging edge cases"],
        "quotes": ["It helps me be twice as productive and ship features much faster."],
        "sentiment_explanation": f"Overall {session_sentiment.label} ({session_sentiment.score}).",
    }


def _stub_insights(record, session_sentiment, settings) -> dict:
    return {
        "dominant_emotion": "optimism",
        "communication_tone": "pragmatic",
        "confidence_level": "high",
        "pain_points": ["Still need to debug edge cases"],
        "actionable_recommendations": ["Combine AI code generation with automated edge-case tests"],
    }


def test_json_schema_response_format_builds_strict_grammar_schema() -> None:
    fmt = _build_json_schema_response_format(InterviewSummary)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "InterviewSummary"
    assert "sentiment_explanation" in fmt["json_schema"]["schema"]["properties"]

    fmt_insights = _build_json_schema_response_format(EmotionToneAnalysis)
    assert fmt_insights["json_schema"]["name"] == "EmotionToneAnalysis"
    assert "dominant_emotion" in fmt_insights["json_schema"]["schema"]["properties"]


def test_analysis_pipeline_waits_for_done_then_sets_processed_flag(tmp_path: Path) -> None:
    """Skips `in_progress` interviews, processes `done` interviews with `processed=False`, and skips once `processed=True`."""
    in_progress_file = tmp_path / "victor.json"
    done_file = tmp_path / "andrei.json"

    in_progress_file.write_text(
        json.dumps(_sample_interview("victor", phase="main_queue"), indent=2),
        encoding="utf-8",
    )
    done_file.write_text(
        json.dumps(_sample_interview("andrei", phase="done"), indent=2),
        encoding="utf-8",
    )

    pipeline = AnalysisPipeline(
        summary_fn=_stub_summary,
        insights_fn=_stub_insights,
        sync_neo4j=False,
    )

    # 1. First scan: `victor.json` is skipped (`in_progress`), `andrei.json` is analyzed and marked `processed=True`.
    summary1 = pipeline.process_directory(tmp_path)
    assert summary1["analyzed"] == ["andrei.json"]
    assert summary1["skipped_in_progress"] == ["victor.json"]
    assert summary1["already_processed"] == []

    enriched = json.loads(done_file.read_text(encoding="utf-8"))
    assert enriched["status"] == "analyzed"
    assert enriched["processed"] is True
    assert "analysis_report" in enriched

    report = enriched["analysis_report"]
    assert "content_hash" not in report
    assert report["session_sentiment"]["label"] in {"positive", "neutral", "negative"}
    assert len(report["summary"]["themes"]) >= 2
    assert len(report["summary"]["key_points"]) >= 2
    assert report["emotion_and_insights"]["dominant_emotion"] == "optimism"
    assert len(report["emotion_and_insights"]["actionable_recommendations"]) == 1

    # Verify Neo4j parameters built from the analyzed record.
    neo4j_params = build_neo4j_parameters(enriched)
    assert neo4j_params["user_id"] == "andrei"
    assert neo4j_params["topic"] == "AI productivity"
    assert len(neo4j_params["answers"]) == 3
    assert neo4j_params["answers"][2]["sentiment_label"] is None

    # 2. Second scan: `andrei.json` has `processed: True` -> skipped (`already_processed`).
    summary2 = pipeline.process_directory(tmp_path)
    assert summary2["analyzed"] == []
    assert summary2["already_processed"] == ["andrei.json"]
    assert summary2["skipped_in_progress"] == ["victor.json"]
