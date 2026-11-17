# ROADMAP.md — Implementation Milestones

Current status of the implemented system.

---

## M0 — Skeleton & Environment
- [x] Repo structure (`shared/contracts`, `services/agent`, `services/mcp`, `services/analysis`, `tests`, `sample.env`, `docker-compose.yml`).
- [x] Full 5-service Docker Compose stack (`neo4j`, `neodash`, `mcp`, `agent`, `analysis`).

## M1 — Shared Contracts & Two-Queue State Machine
- [x] `shared/contracts/` (`common.py`, `llm_outputs.py`, `nlp.py`, `prompts.py`, `state_machine.py`).
- [x] Unit tests for contract validation (`tests/test_contracts.py`).

## M2 — Agent Orchestrator & MCP Server
- [x] `services/mcp/mcp_server/server.py` exposing `start_interview` & `push_followup` over Streamable HTTP.
- [x] `services/agent/interviewer_app/orchestrator.py` using ADK `LlmAgent` + `McpToolset` + table-driven callbacks (`PHASE_USER_TURN_HANDLERS`, `TOOL_RESULT_HANDLERS`) + OpenRouter empty-`Tool` fix.
- [x] Two-Queue + OpenRouter serialization tests (`tests/test_orchestrator.py`).

## M3 — Analysis Pipeline & Neo4j Graph Sync
- [x] Status/flag-driven watcher (`phase == "done"` and `processed == False`, no file hashing).
- [x] `SentimentEngine` + `KeywordEngine` (`nlp_models.py`).
- [x] Decoupled Ollama JSON Schema structured outputs (`mimo-v2.6:9b` + `qwen3:1.7b`), stored raw in `data/<user_id>.json`.
- [x] Idempotent Neo4j graph sync (`neo4j_store.py`) + step-by-step logging + pipeline tests (`tests/test_analysis_pipeline.py`).

## M4 — NeoDash Analytics & Documentation
- [x] Unified NeoDash dashboard (`services/analysis/dashboards/dashboard.json` — 2 pages, 16 cards incl. tables & 3 mini-graphs).
- [x] Walkthrough & Neo4j guides in `data/` (`CODE_WALKTHROUGH.md`, `NEO4J_GRAPH_GUIDE.md`) and specs in `.agents/`.
