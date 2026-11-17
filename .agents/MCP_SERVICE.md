# MCP_SERVICE.md — MCP Server & Analysis Pipeline Services

The backend processing is split into two focused Python services:
1. **`services/mcp` (`mcp_server`)** — Lightweight Streamable HTTP MCP server exposing `start_interview` and `push_followup`.
2. **`services/analysis` (`analysis_app`)** — Asynchronous post-interview pipeline running NLP sentiment/keywords, local Ollama AI summary/insights, and Neo4j graph sync.

---

## 1. MCP Server (`services/mcp/mcp_server/server.py`)

| Item | Value |
|---|---|
| Library | Official `mcp` Python SDK (`MCPServer("interview-tools")`) + `uvicorn` |
| Transport | Streamable HTTP, `host=0.0.0.0`, `port=8001`, path `/mcp` |
| DNS protection | `enable_dns_rebinding_protection=False` (allows `http://mcp:8001/mcp` inside Docker) |
| State logic | Shared directly from `shared/contracts/state_machine.py` |

### Exposed Tools

1. **`start_interview(user_name: str, topic: str, questions: list[str]) -> dict`**
   - Delegates to `apply_start_interview_to_state({"phase": "intake"}, ...)`.
   - Validates completeness, normalizes 3–5 questions ending in `?`, pops Question 1, and saves `data/<user_id>.json`.
2. **`push_followup(follow_up_question: str, referenced_answer: str, user_id: str = "") -> dict`**
   - Loads `data/<user_id>.json` if provided and delegates to `apply_push_followup_to_state(...)`.
   - Appends `{question, context}` to `followup_queue` (max `MAX_FOLLOWUPS = 3`).

*(Note: when called from the ADK agent, `orchestrator.py`'s `after_tool_callback` also applies both functions directly to `tool_ctx.state` so ADK session state and disk JSON stay 100% synchronized.)*

---