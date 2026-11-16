# ROADMAP.md — Implementation Milestones

Work milestone by milestone; each ends with a runnable state and explicit checks. Update the checkboxes as work lands.

---

## M0 — Skeleton & Environment
- [x] Create repo structure from [`GOALS.md`](GOALS.md) §6 (`shared/contracts`, `services/agent`, `services/mcp`, `tests`, `scripts`, `sample.env`, `.env`, `.dockerignore`, `.gitignore`).
- [x] `mcp`: FastMCP server stub with `ping`, `analyze_answers` and `save_interview` returning valid contracts.
- [ ] `docker-compose.yml` with `neo4j`, `mcp`, `agent`, network, volumes, healthchecks ([`DEPLOYMENT.md`](DEPLOYMENT.md)).

## M1 — Contracts
- [x] Implement Pydantic models from [`DATA_MODEL.md`](DATA_MODEL.md) in `shared/contracts/` and re-export in `interviewer_app/schemas.py`.
- [x] Unit tests for contract validations ([`tests/test_contracts.py`](../tests/test_contracts.py)).

## M2 — MCP NLP tools
- [ ] `download_models.py` + model baking in the Dockerfile (CPU torch).
- [ ] `analyze_sentiment`, `extract_keywords`, `analyze_answers` incl. session aggregation.
- [ ] Unit tests (normalization, aggregation, post-processing) + integration test with real models.

## M3 — Agent orchestrator (Intake + Planner + Interviewer)
- [x] `config.py` model selection via `LiteLlm` (local Ollama or cloud OpenRouter — no Gemini).
- [x] `IntakeAgent`, `PlannerAgent`, `InterviewerAgent` + modular prompts in `interviewer_app/agents/`.
- [x] `InterviewOrchestrator` state machine, answer validation, clarification, `skip` / `stop` / `restart`.
- [x] End-to-end state-machine tests with fake sub-agents ([`tests/test_orchestrator.py`](../tests/test_orchestrator.py)) + interactive CLI ([`scripts/try_interview.py`](../scripts/try_interview.py)).
- [x] Conceptual & line-by-line walkthrough guide ([`../.venv/CODE_WALKTHROUGH.md`](../.venv/CODE_WALKTHROUGH.md)).

## M4 — Persistence
- [ ] JSON store (upsert, atomic write, corrupt-file recovery).
- [ ] Neo4j store (constraints, single-transaction MERGE, degradation statuses).
- [ ] `save_interview`, optional `list_interviews`, `resync_neo4j`.
- [ ] `seed_demo.py` with 3 overlapping interviews.

## M5 — Analysis pipeline & final report
- [ ] `EnrichmentStep`, `SummarizerAgent`, `PersistStep`, quote validation.
- [ ] Final report rendering ([`AGENT_SERVICE.md`](AGENT_SERVICE.md) §7).

## M6 — Quality & docs
- [ ] `adk eval` evalsets (2–3 scripted conversations).
- [ ] E2E test script (normal + Neo4j-down variants).
- [ ] Root `README.md`: quick start, architecture diagram, env vars, Cypher queries.
