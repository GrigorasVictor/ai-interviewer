# DEPLOYMENT.md — Docker Compose Setup

Local deployment with **Docker Compose v2**. One command starts the stack:

```bash
cp sample.env .env        # fill in NEO4J_PASSWORD (and OPENROUTER_API_KEY if using cloud LLM)
docker compose up --build
```

- Chat UI: http://localhost:8000
- Neo4j Browser: http://localhost:7474 (user `neo4j`, password from `.env`)

---

## 1. Compose Services

| Service | Build / Image | Ports (host:container) | Env | Volumes | Healthcheck | `depends_on` |
|---|---|---|---|---|---|---|
| `neo4j` | `neo4j:5-community` | `7474:7474`, `7687:7687` | `NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}`, `NEO4J_server_memory_heap_max__size=1G` | `neo4j_data:/data`, `neo4j_logs:/logs` | `cypher-shell -u neo4j -p $NEO4J_PASSWORD "RETURN 1"` · interval 10 s · start_period 30 s | — |
| `mcp` | `./services/mcp` | none (dev override: `8001:8001`) | see [`MCP_SERVICE.md`](MCP_SERVICE.md) §5 | `./data:/app/data` | `GET http://localhost:8001/health` == 200 · start_period 120 s | `neo4j: service_started` (**not** healthy — graceful degradation) |
| `agent` | `./services/agent` | `8000:8000` | see [`AGENT_SERVICE.md`](AGENT_SERVICE.md) §9 + `MCP_SERVER_URL` | `agent_sessions:/app/sessions` | `GET http://localhost:8000/list-apps` == 200 | `mcp: service_healthy` |
| `ollama` *(profile `local-llm`)* | `ollama/ollama` | none | — | `ollama_models:/root/.ollama` | `ollama list` | — |

- Network: single bridge network `interviewer_net`; services address each other by name.
- Restart policy: `unless-stopped` for all long-running services.

---

## 2. Environment Variables (`sample.env`)

| Variable | Used by | Example | Required |
|---|---|---|---|
| `LLM_MODEL` | agent | `ollama_chat/qwen3:8b` or `openrouter/meta-llama/llama-3.3-70b-instruct` | yes |
| `LLM_MODEL_SUMMARY` | agent | *(empty = `LLM_MODEL`)* | no |
| `OLLAMA_API_BASE` | agent | `http://localhost:11434` | for `ollama_chat/...` |
| `OLLAMA_API_BASE_DOCKER` | agent (in Docker) | `http://host.docker.internal:11434` | for `ollama_chat/...` |
| `OPENROUTER_API_KEY` | agent | `sk-or-...` | for `openrouter/...` |
| `MCP_SERVER_URL` | agent | `http://localhost:8001/mcp` (`http://mcp:8001/mcp` in Docker) | yes |
| `MCP_AUTH_TOKEN` | agent, mcp | random string | no |
| `NEO4J_URI` | mcp | `bolt://localhost:7687` (`bolt://neo4j:7687` in Docker) | no (empty disables Neo4j) |
| `NEO4J_USER` | mcp | `neo4j` | with URI |
| `NEO4J_PASSWORD` | mcp, neo4j | min 8 chars | with URI |
| `SENTIMENT_MODEL` | mcp (build + runtime) | `cardiffnlp/twitter-roberta-base-sentiment-latest` | no |
| `KEYWORD_MODEL` | mcp (build + runtime) | `sentence-transformers/all-MiniLM-L6-v2` | no |
| `DATA_DIR` | mcp | `./data` (`/app/data` in Docker) | no |
| `LOG_LEVEL` | all | `INFO` | no |

`.env` is git-ignored.

---

## 3. Storage

| Volume / mount | Type | Service | Contents | Reset |
|---|---|---|---|---|
| `./data` | bind mount | mcp | `interviews_output.json` (visible on host) | delete the file |
| `neo4j_data` | named volume | neo4j | graph database | `docker compose down -v` |
| `neo4j_logs` | named volume | neo4j | logs | same |
| `agent_sessions` | named volume | agent | ADK session SQLite DB | same |
| `ollama_models` | named volume | ollama | local LLM weights | same |
| `/models` (`HF_HOME`) | baked in image | mcp | HF models | rebuild image |

---

## 4. Common Commands

| Task | Command |
|---|---|
| Run tests (offline, < 1 s) | `.venv\Scripts\python.exe -m pytest` |
| Try interview in terminal (mock LLM) | `.venv\Scripts\python.exe scripts/try_interview.py --mock` |
| Try interview in terminal (Ollama / OpenRouter) | `.venv\Scripts\python.exe scripts/try_interview.py` |
| Start ADK Web UI locally | `.venv\Scripts\adk.exe web services/agent` |
| Start full stack in Docker | `docker compose up --build` |
| Test graceful degradation | `docker compose stop neo4j`, run an interview, check `neo4j_synced: false` |
| Full reset | `docker compose down -v` and delete `data/interviews_output.json` |
