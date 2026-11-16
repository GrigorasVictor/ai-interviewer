# DEPLOYMENT.md — Docker Compose Setup

Local deployment with **Docker Compose v2**. One command starts the stack:

```bash
cp .env.example .env      # fill in GOOGLE_API_KEY and NEO4J_PASSWORD
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
- Containers run as a non-root user (except `neo4j`/`ollama`, which use their official images' defaults).

---

## 2. Environment Variables (`.env.example`)

| Variable | Used by | Example | Required |
|---|---|---|---|
| `GOOGLE_API_KEY` | agent | `AIza...` | yes, for Gemini |
| `LLM_MODEL` | agent | `gemini-2.5-flash` | yes |
| `LLM_MODEL_SUMMARY` | agent | `gemini-2.5-flash` | no |
| `OLLAMA_API_BASE` | agent | `http://ollama:11434` | only for Ollama models |
| `MCP_SERVER_URL` | agent | `http://mcp:8001/mcp` | yes |
| `MCP_AUTH_TOKEN` | agent, mcp | random string | no |
| `NEO4J_URI` | mcp | `bolt://neo4j:7687` | no (empty disables Neo4j) |
| `NEO4J_USER` | mcp | `neo4j` | with URI |
| `NEO4J_PASSWORD` | mcp, neo4j | min 8 chars | with URI |
| `SENTIMENT_MODEL` | mcp (build + runtime) | `cardiffnlp/twitter-roberta-base-sentiment-latest` | no |
| `KEYWORD_MODEL` | mcp (build + runtime) | `sentence-transformers/all-MiniLM-L6-v2` | no |
| `LOG_LEVEL` | all | `INFO` | no |

`.env` is git-ignored. Model names used at build time are passed as Docker build args so the baked models match runtime config.

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

## 4. Dockerfile Guidelines

### `services/mcp/Dockerfile`
- Base `python:3.12-slim`.
- Install CPU-only torch first from `https://download.pytorch.org/whl/cpu` (keeps the image ~1.5 GB instead of ~5 GB), then the rest of `requirements.txt`.
- Build args `SENTIMENT_MODEL`, `KEYWORD_MODEL` → run `python -m mcp_server.download_models` to bake models into `/models`.
- Copy source **after** dependencies and models (layer caching).
- `ENV HF_HOME=/models HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1`.
- Non-root user; `EXPOSE 8001`; `CMD python -m mcp_server.server`.

### `services/agent/Dockerfile`
- Base `python:3.12-slim`; install `requirements.txt` (no ML libraries).
- Copy `interviewer_app/` to `/app/agents/interviewer_app/`.
- Non-root user; `EXPOSE 8000`; `CMD adk web --host 0.0.0.0 --port 8000 --session_service_uri sqlite:////app/sessions/sessions.db /app/agents`.

### Both
- `.dockerignore`: `.venv`, `__pycache__`, `.env`, `.idea`, `data/`, `.adk/`, `tests/`.
- Pin dependency versions in `requirements.txt`.

---

## 5. Common Commands

| Task | Command |
|---|---|
| Start (build) | `docker compose up --build` |
| Start with local LLM | `docker compose --profile local-llm up --build` then `docker compose exec ollama ollama pull qwen3:8b` |
| Seed demo interviews | `docker compose exec mcp python -m mcp_server.seed_demo` |
| Re-sync unsynced interviews to Neo4j | call the MCP tool `resync_neo4j` (e.g., from MCP Inspector) |
| Logs of one service | `docker compose logs -f mcp` |
| Test graceful degradation | `docker compose stop neo4j`, run an interview, check `neo4j_synced: false` |
| Inspect MCP tools (dev) | expose `8001`, run `npx @modelcontextprotocol/inspector`, connect to `http://localhost:8001/mcp` |
| Full reset | `docker compose down -v` and delete `data/interviews_output.json` |

---

## 6. Windows / Docker Desktop Notes

- Use the WSL2 backend; allocate **≥ 6 GB RAM** (torch + Neo4j + agent).
- Keep shell scripts with LF line endings (`.gitattributes`: `*.sh text eol=lf`).
- To use an Ollama instance installed on the host instead of the container: `OLLAMA_API_BASE=http://host.docker.internal:11434`.
- First build downloads models (~600 MB); subsequent builds use the layer cache.
