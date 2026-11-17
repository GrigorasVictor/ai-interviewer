# DEPLOYMENT.md — Docker Compose Setup

All 5 services run via **Docker Compose v2** (`docker-compose.yml`).

```powershell
# Local-only (exclusively Ollama):
cp sample-local.env .env  # set NEO4J_PASSWORD

# OR Cloud-only (exclusively OpenRouter):
cp sample-cloud.env .env  # set OPENROUTER_API_KEY and NEO4J_PASSWORD

docker compose up -d --build
```

- **Chat UI (Google ADK Web):** http://localhost:8000
- **Neo4j Browser:** http://localhost:7474 (user `neo4j`, password from `.env`)
- **NeoDash Dashboards (Tables & Mini-Graphs):** http://localhost:5005
- **MCP Server:** http://localhost:8001/mcp

---

## 1. Compose Services

| Service | Container | Image / Build | Ports | Volumes / Mounts | Depends On |
|---|---|---|---|---|---|
| `neo4j` | `interviewer-neo4j` | `neo4j:5-community` | `7474:7474`, `7687:7687` | `neo4j_data:/data`, `neo4j_logs:/logs` | — |
| `neodash` | `interviewer-neodash` | `neo4jlabs/neodash:latest` | `5005:5005` | — | `neo4j` |
| `mcp` | `interviewer-mcp` | `./services/mcp` | `8001:8001` | `./data:/app/data` | — |
| `agent` | `interviewer-agent` | `./services/agent` | `8000:8000` | `./data:/app/data` | `mcp` |
| `analysis` | `interviewer-analysis` | `./services/analysis` | — (watcher) | `./data:/app/data` | `neo4j` |

`agent` and `analysis` include `extra_hosts: host.docker.internal:host-gateway` so containers reach local Ollama on the host (`OLLAMA_API_BASE_DOCKER=http://host.docker.internal:11434`).

---

## 2. Environment Variables (`sample-local.env` / `sample-cloud.env`)

| Variable | Used by | Example / Default |
|---|---|---|
| `LLM_MODEL` | `agent` | `ollama_chat/mimo-v2.6:9b` (`sample-local.env`) or `openrouter/nvidia/nemotron-3.5-super-vl:free` (`sample-cloud.env`) |
| `ANALYSIS_LLM_MODEL` | `analysis` | `ollama_chat/mimo-v2.6:9b` (`sample-local.env`) or `openrouter/...` (`sample-cloud.env`) |
| `LLM_MODEL_SUMMARY` | `analysis` | `ollama_chat/mimo-v2.6:9b` (`sample-local.env`) or `openrouter/...` (`sample-cloud.env`) |
| `LLM_MODEL_INSIGHTS` | `analysis` | `ollama_chat/qwen3:1.7b` (`sample-local.env`) or `openrouter/...` (`sample-cloud.env`) |
| `OLLAMA_API_BASE` / `_DOCKER` | `agent`, `analysis` | `http://localhost:11434` / `http://host.docker.internal:11434` (local mode) |
| `OPENROUTER_API_KEY` | `agent`, `analysis` | `sk-or-...` (cloud mode) |
| `INTERVIEW_MIN_QUESTIONS` / `INTERVIEW_MAX_QUESTIONS` | `agent`, `mcp` | `3` / `5` (configurable min & max questions) |
| `INTERVIEW_NUM_QUESTIONS` | `agent`, `mcp` | Optional fixed count overriding min..max |
| `MCP_SERVER_URL` | `agent` | `http://localhost:8001/mcp` (`http://mcp:8001/mcp` in Docker) |
| `NEO4J_URI` / `USER` / `PASSWORD` | `analysis`, `neo4j` | `bolt://localhost:7687` (`bolt://neo4j:7687` in Docker) |
| `NEODASH_PORT` / `STANDALONE` / `HOST` / `PROTOCOL` | `neodash` | `5005` / `false` / `localhost` / `bolt` |
| `SENTIMENT_MODEL` / `KEYWORD_MODEL` | `analysis` | RoBERTa (fallback VADER) / MiniLM (fallback n-gram TF) |
| `DATA_DIR` | `agent`, `mcp`, `analysis` | `./data` (`/app/data` in Docker) |

---

## 3. Common Commands

```powershell
# Run full unit test suite (offline)
.\scripts\run_tests.ps1

# Start or rebuild all containers
docker compose up -d --build

# Force re-analyze all existing data/*.json files + sync Neo4j
docker compose exec analysis python -m analysis_app --force

# View live analysis model & Neo4j logs
docker compose logs -f analysis

# Reset Neo4j database & volumes
docker compose down -v
```
