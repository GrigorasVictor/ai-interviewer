"""Tests for the status/processed-driven interview analysis pipeline (`services/analysis`)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from analysis_app.llm_analyzers import (
    _build_json_schema_response_format,
    _build_multi_model_context,
    generate_ai_summary,
    generate_emotion_and_insights,
)
from analysis_app.neo4j_store import build_neo4j_parameters
from analysis_app.pipeline import AnalysisPipeline
from contracts import (
    INSIGHTS_SYSTEM_PROMPT,
    INSIGHTS_USER_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
    EmotionToneAnalysis,
    InterviewSummary,
)


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


def _is_ollama_reachable(api_base: str) -> bool:
    try:
        with urlopen(f"{api_base.rstrip('/')}/api/tags", timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


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


def test_sample_interview_injected_prompts() -> None:
    """Print the injected prompts and outputs strictly for `_sample_interview()` (live Ollama if up, else stub)."""
    captured_runs: list[tuple[str, str, str, str, dict]] = []

    def capture_summary(record, session_sentiment, settings) -> dict:
        ctx = _build_multi_model_context(record, session_sentiment)
        use_live = not settings.summary_model.startswith(("ollama/", "ollama_chat/")) or _is_ollama_reachable(
            settings.ollama_api_base
        )
        if use_live:
            output = generate_ai_summary(record, session_sentiment, settings)
            source = f"LIVE ({settings.summary_model})"
        else:
            output = _stub_summary(record, session_sentiment, settings)
            source = "STUB (Ollama offline)"
        captured_runs.append(
            ("1. SUMMARY LLM", source, SUMMARY_SYSTEM_PROMPT, SUMMARY_USER_PROMPT.format(**ctx), output)
        )
        return output

    def capture_insights(record, session_sentiment, settings) -> dict:
        ctx = _build_multi_model_context(record, session_sentiment)
        use_live = not settings.insights_model.startswith(("ollama/", "ollama_chat/")) or _is_ollama_reachable(
            settings.ollama_api_base
        )
        if use_live:
            output = generate_emotion_and_insights(record, session_sentiment, settings)
            source = f"LIVE ({settings.insights_model})"
        else:
            output = _stub_insights(record, session_sentiment, settings)
            source = "STUB (Ollama offline)"
        captured_runs.append(
            ("2. INSIGHTS LLM", source, INSIGHTS_SYSTEM_PROMPT, INSIGHTS_USER_PROMPT.format(**ctx), output)
        )
        return output

    pipeline = AnalysisPipeline(
        summary_fn=capture_summary,
        insights_fn=capture_insights,
        sync_neo4j=False,
    )
    analyzed = pipeline.analyze_record(_sample_interview())

    border = "=" * 80
    sub_border = "-" * 80
    for title, source, system_prompt, user_prompt, output in captured_runs:
        print(f"\n{border}")
        print(f"  {title} [{source}]")
        print(border)
        print("[SYSTEM PROMPT]")
        print(system_prompt.strip())
        print(sub_border)
        print("[USER PROMPT]")
        print(user_prompt.strip())
        print(sub_border)
        print("[OUTPUT]")
        print(json.dumps(output, indent=2, ensure_ascii=False))
        print(f"{border}\n")

    print(f"\n{border}")
    print("  3. FINAL ANALYSIS REPORT OUTPUT")
    print(border)
    print(json.dumps(analyzed["analysis_report"], indent=2, ensure_ascii=False))
    print(f"{border}\n")

    assert len(captured_runs) == 2
    assert "Sentiment Trajectory Across Interview:" in captured_runs[1][3]
    assert "Upstream Summary LLM Synthesis:" in captured_runs[1][3]
