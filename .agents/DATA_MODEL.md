# DATA_MODEL.md — Contracts, Per-User JSON Format & Neo4j Schema

Source of truth for all schemas lives in `shared/contracts/` (Pydantic v2) and `services/analysis/analysis_app/neo4j_store.py`.

---

## 1. Shared Primitives & LLM Schemas (`shared/contracts/`)

### `common.py`
- `SKIPPED_ANSWER = "[skipped]"`
- `SentimentLabel = Literal["negative", "neutral", "positive"]`
- `Score = Annotated[float, Field(ge=0.0, le=1.0), AfterValidator(_round3)]`
- `utc_now_iso() -> str` (`YYYY-MM-DDTHH:MM:SSZ`)

### `llm_outputs.py`
1. **`IntakeResult`**
   - `user_name: str | None` (placeholders like `"unknown"`, `"participant"`, `"none"` stripped to `None`)
   - `topic: str | None` (`max_length=120`)
   - `is_complete -> bool`
2. **`InterviewSummary`** (used as JSON Schema for Summary LLM)
   - `overview: str`
   - `themes: list[str]` (2–5)
   - `key_points: list[str]` (2–6)
   - `quotes: list[str]` (0–3)
   - `sentiment_explanation: str`
3. **`EmotionToneAnalysis`** (used as JSON Schema for Insights LLM)
   - `dominant_emotion: str`
   - `communication_tone: str`
   - `confidence_level: str` (`high | medium | low`)
   - `pain_points: list[str]`
   - `actionable_recommendations: list[str]`

### `nlp.py`
- `Sentiment(label: SentimentLabel, score: Score)`
- `Keyword(phrase: str, score: Score)` *(automatically lowercased)*
- `AnswerAnalysis(answer_id: str, sentiment: Sentiment, keywords: list[Keyword])`

---

## 2. Per-User JSON Record (`data/<user_id>.json`)

Each participant has their own JSON file (`data/victor.json`, `data/andrei.json`).

### Lifecycle Flags
| Stage | `phase` | `status` | `processed` |
|---|---|---|---|
| Interview active | `intake` / `main_queue` / `followup_queue` | `"in_progress"` | `false` |
| Queues finished / stopped | `done` | `"pending_analysis"` | `false` |
| Analysis + Neo4j complete | `done` | `"analyzed"` | `true` |

### Enriched JSON Structure (after `services/analysis`)
```json
{
  "interview_id": "a7d009b4-2678-48a8-8c75-bec6870f2901",
  "user_id": "andrei",
  "user_name": "Andrei",
  "topic": "AI productivity",
  "status": "analyzed",
  "processed": true,
  "phase": "done",
  "question_queue": [],
  "followup_queue": [],
  "current_question": null,
  "current_followup_context": null,
  "transcript": [
    {
      "source": "main_queue",
      "question": "How has AI changed your daily workflow?",
      "context": null,
      "answer": "Instead of coding manually I use AI to generate and I debug it.",
      "answered_at": "2026-11-16T21:38:38Z",
      "analysis": {
        "answer_id": "a7d009b4-2678-48a8-8c75-bec6870f2901:1",
        "sentiment": { "label": "positive", "score": 0.712 },
        "keywords": [{ "phrase": "coding manually", "score": 0.645 }]
      }
    }
  ],
  "completed_at": "2026-11-16T21:42:11Z",
  "analysis_report": {
    "analyzed_at": "2026-11-16T22:05:00Z",
    "session_sentiment": { "label": "positive", "score": 0.297 },
    "summary": { "overview": "...", "themes": ["..."], "key_points": ["..."], "quotes": ["..."], "sentiment_explanation": "..." },
    "emotion_and_insights": { "dominant_emotion": "...", "communication_tone": "...", "confidence_level": "...", "pain_points": ["..."], "actionable_recommendations": ["..."] },
    "models_used": {
      "sentiment": "nltk/vader_lexicon",
      "keywords": "ngram-tf-keyphrase",
      "summary_llm": "ollama_chat/mimo-v2.6:9b",
      "insights_llm": "ollama_chat/qwen3:1.7b"
    },
    "neo4j": { "status": "ok", "message": null, "cypher_query": "..." }
  },
  "neo4j_synced": true
}
```

---

## 3. Neo4j Graph Schema (`services/analysis/analysis_app/neo4j_store.py`)

### Nodes & Unique Constraints
| Label | Unique Key | Properties |
|---|---|---|
| `User` | `id` | `id`, `name` |
| `Interview` | `id` | `id`, `completed_at`, `analyzed_at`, `overview`, `sentiment_label`, `sentiment_score`, `dominant_emotion`, `communication_tone`, `confidence_level` |
| `Topic` | `name` | `name` |
| `Sentiment` | `label` | `label` |
| `Emotion` | `name` | `name` |
| `Theme` | `name` | `name` |
| `Answer` | `id` | `id` (`<interview_id>:<idx>`), `index`, `source`, `question`, `answer`, `answered_at`, `sentiment_label`, `sentiment_score` |
| `Keyword` | `phrase` | `phrase` |
| `PainPoint` | `text` | `text` |
| `Recommendation` | `text` | `text` |

### Relationships
- `(:User)-[:PARTICIPATED_IN]->(:Interview)`
- `(:User)-[:DISCUSSED_TOPIC]->(:Topic)`
- `(:Interview)-[:EXPLORES_TOPIC]->(:Topic)`
- `(:Interview)-[:HAS_OVERALL_SENTIMENT {score}]->(:Sentiment)`
- `(:Interview)-[:EXPRESSES_EMOTION {tone, confidence}]->(:Emotion)`
- `(:Interview)-[:HIGHLIGHTS_THEME]->(:Theme)-[:RELATES_TO_TOPIC]->(:Topic)`
- `(:Interview)-[:REVEALS_PAIN_POINT]->(:PainPoint)`
- `(:Interview)-[:SUGGESTS_RECOMMENDATION]->(:Recommendation)`
- `(:Interview)-[:HAS_ANSWER]->(:Answer)`
- `(:Answer)-[:HAS_SENTIMENT {score}]->(:Sentiment)` *(skipped answers omitted)*
- `(:Answer)-[:MENTIONS_KEYWORD {score}]->(:Keyword)`
