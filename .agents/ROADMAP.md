# ROADMAP.md — Implementation Milestones

Work milestone by milestone; each ends with a runnable state and explicit checks. Update the checkboxes as work lands.

---

## M0 — Skeleton & Compose
- [ ] Create repo structure from [`GOALS.md`](GOALS.md) §6 (empty packages, Dockerfiles, `requirements.txt`, `.env.example`, `.dockerignore`, `.gitignore`).
- [ ] `docker-compose.yml` with `neo4j`, `mcp`, `agent`, network, volumes, healthchecks ([`DEPLOYMENT.md`](DEPLOYMENT.md)).
- [ ] `mcp`: FastMCP server with a `ping` tool and `/health`.
- [ ] `agent`: placeholder `root_agent` that echoes and can call `ping` via `mcp_client.py`.

**Check:** `docker compose up --build` → all three healthy; chat at `:8000` returns the `ping` result.

## M1 — Contracts
- [ ] Implement Pydantic models from [`DATA_MODEL.md`](DATA_MODEL.md) in both `schemas.py` files.
- [ ] Contract test comparing generated JSON Schemas.

**Check:** contract test passes.

## M2 — MCP NLP tools
- [ ] `download_models.py` + model baking in the Dockerfile (CPU torch).
- [ ] `analyze_sentiment`, `extract_keywords`, `analyze_answers` incl. session aggregation.
- [ ] Unit tests (normalization, aggregation, post-processing) + integration test with real models.

**Check:** calling `analyze_answers` from MCP Inspector returns valid `AnalyzeAnswersResult` in < 3 s for 5 answers on CPU.

## M3 — Agent orchestrator
- [ ] `config.py` model selection (Gemini / LiteLLM).
- [ ] Intake, Planner, Interviewer sub-agents + prompts.
- [ ] `InterviewOrchestrator` state machine, answer validation, clarification, `skip` / `stop`.
- [ ] Unit tests with fake LLM agents and fake MCP client.
- [ ] Remove the legacy `my_agent/` prototype.

**Check:** full interview in `adk web` reaches `phase = analysis` after 3–5 questions; restarting the `agent` container mid-interview resumes correctly.

## M4 — Persistence
- [ ] JSON store (upsert, atomic write, corrupt-file recovery).
- [ ] Neo4j store (constraints, single-transaction MERGE, degradation statuses).
- [ ] `save_interview`, optional `list_interviews`, `resync_neo4j`.
- [ ] `seed_demo.py` with 3 overlapping interviews.

**Check:** seed → `MATCH (n)-[r]->(m) RETURN n, r, m` shows connected users/areas/keywords; with `neo4j` stopped, `save_interview` returns `json.ok` + `neo4j.skipped`.

## M5 — Analysis pipeline & final report
- [ ] `EnrichmentStep`, `SummarizerAgent`, `PersistStep`, quote validation.
- [ ] Final report rendering ([`AGENT_SERVICE.md`](AGENT_SERVICE.md) §7).

**Check:** all items in [`GOALS.md`](GOALS.md) §7 pass manually.

## M6 — Quality & docs
- [ ] `adk eval` evalsets (2–3 scripted conversations).
- [ ] E2E test script (normal + Neo4j-down variants).
- [ ] Root `README.md`: quick start, architecture diagram, env vars, Cypher queries, screenshots of the chat and the graph.

**Check:** fresh clone → `cp .env.example .env` → `docker compose up --build` → complete interview with no other steps.
