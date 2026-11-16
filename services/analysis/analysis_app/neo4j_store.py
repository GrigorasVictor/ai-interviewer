"""Neo4j graph persistence for analyzed interviews (`services/analysis/analysis_app/neo4j_store.py`).

Saves a rich knowledge graph of:
- `(:User {id, name})`
- `(:Interview {id, completed_at, analyzed_at, overview, dominant_emotion, communication_tone, confidence_level, sentiment_label, sentiment_score})`
- `(:Topic {name})`
- `(:Sentiment {label})`
- `(:Emotion {name})`
- `(:Theme {name})`
- `(:Answer {id, index, source, question, answer, sentiment_label, sentiment_score})`
- `(:Keyword {phrase})`
- `(:PainPoint {text})`
- `(:Recommendation {text})`

Gracefully degrades (`status="skipped"`) when `NEO4J_URI` is empty or Neo4j is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import AnalysisSettings

logger = logging.getLogger("analysis_app.neo4j")

CONSTRAINTS_CYPHER = (
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT interview_id IF NOT EXISTS FOR (i:Interview) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT sentiment_label IF NOT EXISTS FOR (s:Sentiment) REQUIRE s.label IS UNIQUE",
    "CREATE CONSTRAINT emotion_name IF NOT EXISTS FOR (e:Emotion) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT theme_name IF NOT EXISTS FOR (th:Theme) REQUIRE th.name IS UNIQUE",
    "CREATE CONSTRAINT answer_id IF NOT EXISTS FOR (a:Answer) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT keyword_phrase IF NOT EXISTS FOR (k:Keyword) REQUIRE k.phrase IS UNIQUE",
    "CREATE CONSTRAINT pain_point_text IF NOT EXISTS FOR (p:PainPoint) REQUIRE p.text IS UNIQUE",
    "CREATE CONSTRAINT recommendation_text IF NOT EXISTS FOR (r:Recommendation) REQUIRE r.text IS UNIQUE",
)

UPSERT_INTERVIEW_CYPHER = """
MERGE (u:User {id: $user_id})
  ON CREATE SET u.name = $user_name
  ON MATCH SET  u.name = $user_name

MERGE (t:Topic {name: $topic})

MERGE (i:Interview {id: $interview_id})
SET i.completed_at       = $completed_at,
    i.analyzed_at        = $analyzed_at,
    i.overview           = $overview,
    i.sentiment_label    = $sentiment_label,
    i.sentiment_score    = $sentiment_score,
    i.dominant_emotion   = $dominant_emotion,
    i.communication_tone = $communication_tone,
    i.confidence_level   = $confidence_level

MERGE (u)-[:PARTICIPATED_IN]->(i)
MERGE (i)-[:EXPLORES_TOPIC]->(t)
MERGE (u)-[:DISCUSSED_TOPIC]->(t)

FOREACH (_ IN CASE WHEN $sentiment_label IS NOT NULL AND $sentiment_label <> '' THEN [1] ELSE [] END |
  MERGE (s:Sentiment {label: $sentiment_label})
  MERGE (i)-[hs:HAS_OVERALL_SENTIMENT]->(s)
  SET hs.score = $sentiment_score
)

FOREACH (_ IN CASE WHEN $dominant_emotion IS NOT NULL AND $dominant_emotion <> '' THEN [1] ELSE [] END |
  MERGE (em:Emotion {name: $dominant_emotion})
  MERGE (i)-[fe:EXPRESSES_EMOTION]->(em)
  SET fe.tone = $communication_tone,
      fe.confidence = $confidence_level
)

WITH i, t
FOREACH (theme_name IN $themes |
  MERGE (th:Theme {name: theme_name})
  MERGE (i)-[:HIGHLIGHTS_THEME]->(th)
  MERGE (th)-[:RELATES_TO_TOPIC]->(t)
)

FOREACH (pain_text IN $pain_points |
  MERGE (pp:PainPoint {text: pain_text})
  MERGE (i)-[:REVEALS_PAIN_POINT]->(pp)
)

FOREACH (rec_text IN $recommendations |
  MERGE (rec:Recommendation {text: rec_text})
  MERGE (i)-[:SUGGESTS_RECOMMENDATION]->(rec)
)

WITH i
UNWIND $answers AS ans
  MERGE (a:Answer {id: ans.answer_id})
  SET a.index           = ans.index,
      a.source          = ans.source,
      a.question        = ans.question,
      a.answer          = ans.answer,
      a.answered_at     = ans.answered_at,
      a.sentiment_label = ans.sentiment_label,
      a.sentiment_score = ans.sentiment_score
  MERGE (i)-[:HAS_ANSWER]->(a)
  FOREACH (_ IN CASE WHEN ans.sentiment_label IS NOT NULL THEN [1] ELSE [] END |
    MERGE (asent:Sentiment {label: ans.sentiment_label})
    MERGE (a)-[ras:HAS_SENTIMENT]->(asent)
    SET ras.score = ans.sentiment_score
  )
  FOREACH (kw IN ans.keywords |
    MERGE (k:Keyword {phrase: kw.phrase})
    MERGE (a)-[mk:MENTIONS_KEYWORD]->(k)
    SET mk.score = kw.score
  )
"""


def build_neo4j_parameters(record: dict[str, Any]) -> dict[str, Any]:
    """Build the parameter dict passed to `UPSERT_INTERVIEW_CYPHER` directly from the raw analyzed record."""
    interview_id = record.get("interview_id") or record.get("user_id")
    report = record.get("analysis_report") or {}
    session_sentiment = report.get("session_sentiment") or {}
    summary = report.get("summary") or {}
    insights = report.get("emotion_and_insights") or {}

    answers_payload: list[dict[str, Any]] = []
    for idx, turn in enumerate(record.get("transcript") or [], start=1):
        turn_analysis = turn.get("analysis") or {}
        sent = turn_analysis.get("sentiment") or {}
        kws = turn_analysis.get("keywords") or []
        answers_payload.append(
            {
                "answer_id": turn_analysis.get("answer_id") or f"{interview_id}:{idx}",
                "index": idx,
                "source": turn.get("source"),
                "question": turn.get("question"),
                "answer": turn.get("answer"),
                "answered_at": turn.get("answered_at"),
                "sentiment_label": sent.get("label"),
                "sentiment_score": sent.get("score"),
                "keywords": [k for k in kws if isinstance(k, dict) and k.get("phrase")],
            }
        )

    return {
        "interview_id": interview_id,
        "user_id": record.get("user_id"),
        "user_name": record.get("user_name") or record.get("user_id"),
        "topic": record.get("topic"),
        "completed_at": record.get("completed_at"),
        "analyzed_at": report.get("analyzed_at"),
        "overview": summary.get("overview"),
        "sentiment_label": session_sentiment.get("label"),
        "sentiment_score": session_sentiment.get("score"),
        "dominant_emotion": insights.get("dominant_emotion"),
        "communication_tone": insights.get("communication_tone"),
        "confidence_level": insights.get("confidence_level"),
        "themes": [str(t) for t in (summary.get("themes") or []) if t],
        "pain_points": [str(p) for p in (insights.get("pain_points") or []) if p],
        "recommendations": [str(r) for r in (insights.get("actionable_recommendations") or []) if r],
        "answers": answers_payload,
    }


class Neo4jGraphStore:
    """Syncs analyzed interview JSON records into Neo4j (`MERGE` idempotent)."""

    def __init__(self, settings: AnalysisSettings) -> None:
        self.uri = settings.neo4j_uri
        self.user = settings.neo4j_user
        self.password = settings.neo4j_password

    def sync_interview(self, record: dict[str, Any]) -> dict[str, Any]:
        """Write `record` to Neo4j and return `{status, cypher_query, message?}`."""
        interview_id = str(record.get("interview_id") or "")
        cypher_preview = (
            f'MATCH p = (u:User)-[:PARTICIPATED_IN]->(i:Interview {{id: "{interview_id}"}})-[*1..2]->() '
            "RETURN p"
        )
        if not self.uri:
            logger.info("  [Neo4j] Skipped: NEO4J_URI is empty.")
            return {
                "status": "skipped",
                "message": "NEO4J_URI is empty.",
                "cypher_query": cypher_preview,
            }

        params = build_neo4j_parameters(record)
        total_keywords = sum(len(a["keywords"]) for a in params["answers"])
        logger.info(
            "  [Neo4j] Preparing graph payload for '%s' -> User(:%s), Topic(%r), Sentiment(%s=%s), Emotion(%r), Themes=%s, PainPoints=%d, Recommendations=%d, Answers=%d, Keywords=%d",
            self.uri,
            params["user_id"],
            params["topic"],
            params["sentiment_label"],
            params["sentiment_score"],
            params["dominant_emotion"],
            params["themes"],
            len(params["pain_points"]),
            len(params["recommendations"]),
            len(params["answers"]),
            total_keywords,
        )

        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]

            with GraphDatabase.driver(self.uri, auth=(self.user, self.password)) as driver:
                driver.verify_connectivity()
                with driver.session() as session:
                    for stmt in CONSTRAINTS_CYPHER:
                        session.run(stmt).consume()
                    result = session.run(UPSERT_INTERVIEW_CYPHER, **params)
                    counters = result.consume().counters
                    logger.info(
                        "  [Neo4j] Graph transaction committed -> nodes_created=%d, relationships_created=%d, properties_set=%d",
                        counters.nodes_created,
                        counters.relationships_created,
                        counters.properties_set,
                    )

            return {
                "status": "ok",
                "message": None,
                "cypher_query": cypher_preview,
            }
        except Exception as exc:
            logger.warning("  [Neo4j] Sync skipped/failed (%s): %s", self.uri, exc)
            return {
                "status": "skipped",
                "message": str(exc),
                "cypher_query": cypher_preview,
            }
