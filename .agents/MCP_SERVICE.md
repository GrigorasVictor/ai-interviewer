# MCP_SERVICE.md — MCP Tools Server

The `mcp` service exposes **deterministic tools** over the Model Context Protocol: Hugging Face NLP (sentiment, keyphrases) and persistence (JSON + Neo4j). It never calls an LLM and holds no conversation state.

---

## 1. Server Setup

| Item | Value |
|---|---|
| Library | Official `mcp` Python SDK, `FastMCP` |
| Transport | Streamable HTTP, `host=0.0.0.0`, `port=8001`, path `/mcp` |
| Health | Custom HTTP route `GET /health` → `{status, models_loaded, neo4j: "up" \| "down" \| "disabled", version}` |
| Auth (optional) | If `MCP_AUTH_TOKEN` is set, reject requests without matching `Authorization: Bearer` header |
| Device | CPU only (`torch` CPU wheels) |

### Startup sequence
1. Load config from env.
2. Load sentiment pipeline and KeyBERT model from the baked cache (`HF_HOME`); run one warm-up inference each. `/health` returns `starting` (HTTP 503) until done.
3. Try to connect to Neo4j (`verify_connectivity`). On success create constraints (idempotent). On failure log a **warning**, mark `neo4j: down`, continue. Reconnect is attempted lazily on each `save_interview`.
4. Ensure `DATA_DIR` exists and `interviews_output.json` is a valid JSON array (create `[]` if missing).
5. Start serving.

---

## 2. Tool Contracts

All tools take and return JSON matching [`DATA_MODEL.md`](DATA_MODEL.md). Return values are emitted as **structured content** (Pydantic return types) so clients can parse them without guessing.

**Error policy:**
- Invalid input → raise (MCP returns `isError: true` with a message).
- Expected operational degradation (Neo4j down) → **normal result** with a status field, never an exception.

### 2.1 `analyze_answers` *(primary tool used by the agent)*
- **Input:** `answers: list[{answer_id: str, text: str}]` (1–10 items, each text ≤ 5 000 chars)
- **Output:** `AnalyzeAnswersResult`
  - `answers: list[{answer_id, sentiment: {label, score}, keywords: list[{phrase, score}]}]`
  - `session_sentiment: {label, score}`
  - `models: {sentiment: str, keywords: str}`
- Runs sentiment and keyword extraction in batch.

### 2.2 `analyze_sentiment`
- **Input:** `texts: list[str]`
- **Output:** `list[{label, score}]`

### 2.3 `extract_keywords`
- **Input:** `texts: list[str]`, `top_n: int = 5` (3–5)
- **Output:** `list[list[{phrase, score}]]`

### 2.4 `save_interview`
- **Input:** `record: InterviewRecord`
- **Output:** `SaveInterviewResult`
  - `interview_id`
  - `json: {status: "ok" | "error", path, message?}`
  - `neo4j: {status: "ok" | "skipped" | "error", message?}`
  - `cypher_query: str` (query that visualizes this interview)
- Behavior: JSON first (upsert by `interview_id`), then Neo4j. Sets `neo4j_synced` in the JSON record according to the outcome.

### 2.5 `list_interviews` *(optional)*
- **Input:** `limit: int = 10`, `user_id?: str`
- **Output:** list of `{interview_id, user_id, topic, timestamp, overall_sentiment}`

### 2.6 `resync_neo4j` *(optional)*
- **Input:** none
- **Output:** `{synced: int, failed: int}`
- Pushes every JSON record with `neo4j_synced: false` into Neo4j.

---

## 3. NLP Specification

### 3.1 Sentiment (`nlp/sentiment.py`)
| Setting | Value |
|---|---|
| Default model | `cardiffnlp/twitter-roberta-base-sentiment-latest` (env `SENTIMENT_MODEL`) |
| Multilingual alternative | `cardiffnlp/twitter-xlm-roberta-base-sentiment` |
| Pipeline | `text-classification`, `truncation=True`, `max_length=512`, batched |
| Output | label ∈ {`negative`, `neutral`, `positive`} (normalize casing/`LABEL_x`), score ∈ [0, 1] = top-class probability |

**Session sentiment aggregation (deterministic):**
1. Per answer: `signed = score × (+1 if positive, 0 if neutral, −1 if negative)`.
2. `mean_signed = average(signed)` over analyzed answers.
3. Label: `positive` if `mean_signed > 0.25`, `negative` if `< −0.25`, else `neutral`.
4. Score: `abs(mean_signed)` for positive/negative; `1 − abs(mean_signed)` for neutral. Rounded to 3 decimals.

### 3.2 Keyphrases (`nlp/keywords.py`)
| Setting | Value |
|---|---|
| Library | `KeyBERT` |
| Default embedding model | `sentence-transformers/all-MiniLM-L6-v2` (env `KEYWORD_MODEL`) |
| Multilingual alternative | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Params | `keyphrase_ngram_range=(1, 2)`, `stop_words="english"` (None for multilingual), `use_mmr=True`, `diversity=0.5`, `top_n=5` |
| Post-processing | lowercase, strip, deduplicate, drop phrases < 3 chars; keep 3–5; scores rounded to 3 decimals |
| Short answers | If text has < 4 words, return the content words with score `0.0` instead of calling the model |

> Default models are English. If interviews run in Romanian (or mixed), switch both env vars to the multilingual alternatives.

### 3.3 Model baking
`download_models.py` is executed during `docker build` to populate `HF_HOME` inside the image. At runtime set `HF_HUB_OFFLINE=1` so no network access is attempted.

---

## 4. Persistence

### 4.1 JSON store (`storage/json_store.py`)
- File: `${DATA_DIR}/interviews_output.json` (default `/app/data`, bind-mounted to `./data` on host).
- Format: a JSON **array** of `InterviewRecord` objects (see [`DATA_MODEL.md`](DATA_MODEL.md)), UTF-8, indented 2 spaces, `ensure_ascii=False`.
- **Upsert by `interview_id`** (retries never duplicate).
- **Atomic write:** write to a temp file in the same directory, `fsync`, then `os.replace`.
- **Concurrency:** a process-wide `asyncio.Lock` (single `mcp` replica). If the file is corrupt, rename it to `interviews_output.corrupt-<timestamp>.json`, log an error, start a new array.

### 4.2 Neo4j store (`storage/neo4j_store.py`)
- Driver: `neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))`, created once, closed on shutdown.
- If `NEO4J_URI` is empty → `neo4j: disabled`, all saves return `skipped`.
- Constraints created at startup (`IF NOT EXISTS`): uniqueness on `User.id`, `Topic.name`, `Area.name`, `Interview.id`, `Answer.id`, `Keyword.phrase`.
- One interview = **one write transaction** using `MERGE` + `UNWIND` (answers, keywords). Schema in [`DATA_MODEL.md`](DATA_MODEL.md).
- Connection/auth errors → caught, logged as warning, returned as `skipped` (unreachable) or `error` (query failure).

### 4.3 Seed data (`seed_demo.py`)
- 3 demo interviews, 2 users, 2 topics; designed to overlap on at least 2 areas and 4 keywords so the graph visibly connects.
- Goes through the same code path as `save_interview` (JSON + Neo4j), with fixed `interview_id`s → re-running is idempotent.
- Run: `docker compose exec mcp python -m mcp_server.seed_demo`.

---

## 5. Configuration

| Env var | Default | Meaning |
|---|---|---|
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8001` | Bind address |
| `MCP_AUTH_TOKEN` | empty | Optional bearer token |
| `DATA_DIR` | `/app/data` | JSON output directory |
| `NEO4J_URI` | `bolt://neo4j:7687` | Empty disables Neo4j |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / — | Credentials |
| `SENTIMENT_MODEL` | `cardiffnlp/twitter-roberta-base-sentiment-latest` | HF model id |
| `KEYWORD_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | KeyBERT backbone |
| `HF_HOME` | `/models` | Model cache (baked) |
| `HF_HUB_OFFLINE` | `1` | No downloads at runtime |
| `LOG_LEVEL` | `INFO` | Logging |

---

## 6. Testing

| Level | What |
|---|---|
| Unit | Label normalization, session aggregation thresholds, keyword post-processing, JSON upsert + atomic write + corrupt-file recovery |
| Integration | Real models on 3 fixed sentences (positive/neutral/negative) — assert labels; Neo4j via Testcontainers or the compose `neo4j` service |
| Degradation | `NEO4J_URI` unreachable → `save_interview` returns `json.ok` + `neo4j.skipped`, record has `neo4j_synced: false` |
| Manual | MCP Inspector against `http://localhost:8001/mcp` (dev port mapping) |
