# DATA_MODEL.md — Contracts, Storage Formats, Graph Schema

This file is the **source of truth** for every payload crossing a service boundary and for every persisted format. Both `services/agent/interviewer_app/schemas.py` and `services/mcp/mcp_server/schemas.py` implement these models with Pydantic v2.

Conventions: `snake_case` fields, timestamps ISO 8601 UTC (`2026-11-16T10:00:00Z`), scores are floats in [0, 1] rounded to 3 decimals, IDs are strings.

---

## 1. LLM Structured Outputs (agent-internal)

### `IntakeResult`
| Field | Type | Notes |
|---|---|---|
| `user_name` | `str \| null` | As given by the user |
| `topic` | `str \| null` | Normalized, ≤ 80 chars |

### `RubricArea`
| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Title Case, ≤ 40 chars, unique within rubric (e.g., `Task Automation`) |
| `description` | `str` | One sentence |
| `angles` | `list[str]` | 1–2 example angles to explore |

### `Rubric`
| Field | Type | Notes |
|---|---|---|
| `topic` | `str` | |
| `areas` | `list[RubricArea]` | **3–5** items |

### `InterviewerTurn`
| Field | Type | Notes |
|---|---|---|
| `area` | `str` | Must equal one of `rubric.areas[].name` |
| `question` | `str` | Single open-ended question, ends with `?` |
| `is_follow_up` | `bool` | True if it builds on the previous answer |
| `coverage_complete` | `bool` | Interviewer's signal that all areas are covered |

### `InterviewSummary`
| Field | Type | Notes |
|---|---|---|
| `overview` | `str` | 2–4 sentence paragraph |
| `themes` | `list[str]` | 2–5 items |
| `key_points` | `list[str]` | 3–6 items |
| `quotes` | `list[str]` | 0–3 verbatim substrings of answers |
| `sentiment_explanation` | `str` | Qualitative description, consistent with `session_sentiment` |

---

## 2. MCP Payloads (agent ↔ mcp)

### `Sentiment`
| Field | Type |
|---|---|
| `label` | `"negative" \| "neutral" \| "positive"` |
| `score` | `float` |

### `Keyword`
| Field | Type |
|---|---|
| `phrase` | `str` (lowercase) |
| `score` | `float` |

### `AnswerInput` → `analyze_answers` input item
| Field | Type |
|---|---|
| `answer_id` | `str` |
| `text` | `str` |

### `AnswerAnalysis`
| Field | Type |
|---|---|
| `answer_id` | `str` |
| `sentiment` | `Sentiment` |
| `keywords` | `list[Keyword]` (3–5, may be fewer for very short answers) |

### `AnalyzeAnswersResult`
| Field | Type |
|---|---|
| `answers` | `list[AnswerAnalysis]` |
| `session_sentiment` | `Sentiment` (aggregation rule in [`MCP_SERVICE.md`](MCP_SERVICE.md) §3.1) |
| `models` | `{sentiment: str, keywords: str}` |

### `SaveInterviewResult`
| Field | Type |
|---|---|
| `interview_id` | `str` |
| `json` | `{status: "ok" \| "error", path: str, message: str \| null}` |
| `neo4j` | `{status: "ok" \| "skipped" \| "error", message: str \| null}` |
| `cypher_query` | `str` |

---

## 3. Persisted Record

### `TranscriptTurn`
| Field | Type | Notes |
|---|---|---|
| `index` | `int` | 1-based |
| `answer_id` | `str` | `"{interview_id}:{index}"` |
| `area` | `str` | Rubric area name |
| `question` | `str` | |
| `answer` | `str` | `"[skipped]"` if skipped |
| `is_follow_up` | `bool` | |
| `asked_at`, `answered_at` | `str` | ISO timestamps |
| `analysis` | `AnswerAnalysis \| null` | Filled at persist time; null for skipped |

### `InterviewRecord` (one element of `interviews_output.json`)
| Field | Type |
|---|---|
| `schema_version` | `"1.0"` |
| `interview_id` | `str` (uuid4) |
| `timestamp` | `str` (completion time) |
| `user` | `{id: str, name: str}` |
| `topic` | `str` |
| `rubric` | `Rubric` |
| `transcript` | `list[TranscriptTurn]` |
| `summary` | `InterviewSummary` |
| `session_sentiment` | `Sentiment` |
| `models` | `{llm: str, sentiment: str, keywords: str}` |
| `neo4j_synced` | `bool` (set by `mcp`) |

### Example (abridged)
```json
[
  {
    "schema_version": "1.0",
    "interview_id": "6f1c2b9e-3a4d-4b7e-9c11-2f0a8d5e7b21",
    "timestamp": "2026-11-16T10:12:44Z",
    "user": { "id": "ana-popescu", "name": "Ana Popescu" },
    "topic": "AI in the workplace",
    "rubric": {
      "topic": "AI in the workplace",
      "areas": [
        { "name": "Task Automation", "description": "Which tasks AI takes over.", "angles": ["daily routines"] },
        { "name": "Trust & Reliability", "description": "Confidence in AI output.", "angles": ["verification habits"] },
        { "name": "Skills & Learning", "description": "How roles and skills change.", "angles": ["upskilling"] }
      ]
    },
    "transcript": [
      {
        "index": 1,
        "answer_id": "6f1c2b9e-3a4d-4b7e-9c11-2f0a8d5e7b21:1",
        "area": "Task Automation",
        "question": "Which parts of your work have changed most since you started using AI tools?",
        "answer": "Mostly drafting emails and summarizing meetings, it saves me about an hour a day.",
        "is_follow_up": false,
        "asked_at": "2026-11-16T10:05:02Z",
        "answered_at": "2026-11-16T10:06:10Z",
        "analysis": {
          "answer_id": "6f1c2b9e-3a4d-4b7e-9c11-2f0a8d5e7b21:1",
          "sentiment": { "label": "positive", "score": 0.874 },
          "keywords": [
            { "phrase": "drafting emails", "score": 0.612 },
            { "phrase": "summarizing meetings", "score": 0.587 },
            { "phrase": "saves", "score": 0.341 }
          ]
        }
      }
    ],
    "summary": {
      "overview": "Ana sees AI as a practical time-saver for routine communication...",
      "themes": ["Time savings", "Verification burden"],
      "key_points": ["Uses AI daily for emails and meeting notes", "..."],
      "quotes": ["it saves me about an hour a day"],
      "sentiment_explanation": "Generally positive, with reservations about accuracy."
    },
    "session_sentiment": { "label": "positive", "score": 0.412 },
    "models": {
      "llm": "gemini-2.5-flash",
      "sentiment": "cardiffnlp/twitter-roberta-base-sentiment-latest",
      "keywords": "sentence-transformers/all-MiniLM-L6-v2"
    },
    "neo4j_synced": true
  }
]
```

---

## 4. Neo4j Graph Schema

### Nodes
| Label | Key | Properties |
|---|---|---|
| `User` | `id` | `name` |
| `Topic` | `name` | — |
| `Area` | `name` | `description` (last written wins) |
| `Interview` | `id` | `timestamp`, `summary` (= `overview`), `themes` (list), `overall_sentiment`, `overall_sentiment_score` |
| `Answer` | `id` (= `answer_id`) | `index`, `question_text`, `answer_text`, `sentiment_label`, `sentiment_score` |
| `Keyword` | `phrase` | — |

### Relationships
| Pattern | Properties | MERGE key |
|---|---|---|
| `(:User)-[:PARTICIPATED_IN]->(:Interview)` | — | endpoints |
| `(:Interview)-[:EXPLORES_TOPIC]->(:Topic)` | — | endpoints |
| `(:Interview)-[:HAS_ANSWER]->(:Answer)` | — | endpoints |
| `(:Answer)-[:BELONGS_TO_AREA]->(:Area)` | — | endpoints |
| `(:Answer)-[:MENTIONS_KEYWORD]->(:Keyword)` | `relevance_score` | endpoints |
| `(:User)-[:ANSWERED_IN_AREA]->(:Area)` | `answer_id`, `interview_id`, `sentiment_label`, `sentiment_score` | endpoints **+ `answer_id`** (one rel per answer, so multiple interviews never overwrite each other) |

Skipped answers create an `Answer` node (with `answer_text = "[skipped]"`, no sentiment) but no keywords and no `ANSWERED_IN_AREA`.

### Constraints (created by `mcp` at startup)
```cypher
CREATE CONSTRAINT user_id      IF NOT EXISTS FOR (n:User)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT topic_name   IF NOT EXISTS FOR (n:Topic)     REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT area_name    IF NOT EXISTS FOR (n:Area)      REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT interview_id IF NOT EXISTS FOR (n:Interview) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT answer_id    IF NOT EXISTS FOR (n:Answer)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT keyword      IF NOT EXISTS FOR (n:Keyword)   REQUIRE n.phrase IS UNIQUE;
```

### Ingestion rules
- One write transaction per interview; all writes are `MERGE` → idempotent re-runs.
- Area names are matched case-sensitively as produced by the rubric; keyword phrases are already lowercase.

---

## 5. Cypher Queries (for README and the final report)

**Everything:**
```cypher
MATCH (n)-[r]->(m) RETURN n, r, m
```

**One interview** (template returned as `cypher_query` by `save_interview`):
```cypher
MATCH p = (u:User)-[:PARTICIPATED_IN]->(i:Interview {id: "<interview_id>"})-[*1..2]->()
RETURN p
```

**Keywords shared across different users:**
```cypher
MATCH (u1:User)-[:PARTICIPATED_IN]->(:Interview)-[:HAS_ANSWER]->(:Answer)-[:MENTIONS_KEYWORD]->(k:Keyword)
      <-[:MENTIONS_KEYWORD]-(:Answer)<-[:HAS_ANSWER]-(:Interview)<-[:PARTICIPATED_IN]-(u2:User)
WHERE u1.id < u2.id
RETURN k.phrase AS keyword, collect(DISTINCT u1.name) + collect(DISTINCT u2.name) AS users
```

**Average sentiment per area:**
```cypher
MATCH (a:Answer)-[:BELONGS_TO_AREA]->(ar:Area)
WHERE a.sentiment_label IS NOT NULL
RETURN ar.name AS area, count(a) AS answers,
       avg(CASE a.sentiment_label WHEN 'positive' THEN a.sentiment_score
                                  WHEN 'negative' THEN -a.sentiment_score ELSE 0 END) AS mean_signed_sentiment
ORDER BY mean_signed_sentiment DESC
```
