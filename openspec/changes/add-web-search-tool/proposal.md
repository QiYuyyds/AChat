## Why

Custom agents currently have no way to retrieve fresh, real-world information from the public web — they are limited to the workspace files, attachments, and the RAG knowledge base. Tasks that need current facts (news, docs, prices, "look this up") cannot be served. Adding a web search tool closes this gap using Tavily, an LLM-oriented search API the user already has a key for.

## What Changes

- Add a new AChat-managed tool `web_search` backed by the Tavily Search REST API (`POST https://api.tavily.com/search`).
- Register `web_search` in `tool_registry` so it can be resolved/executed by the existing adapter tool loop (custom adapter).
- The tool is **opt-in per agent** (strategy B): an agent gains web search only when `web_search` is present in its `toolNames`. **No change to `agent_runner` auto-injection** — unlike `memory_recall`/RAG tools, it is never injected implicitly.
- Read the Tavily key from `.env` (`TAVILY_API_KEY`) via the existing `config.apply_env_overrides` env-fallback layer; add a `tavily_api_key` settings field.
- The tool respects the run cancel signal (`ctx.cancel_event`) and a request timeout, and bounds returned results (top-5) to protect the model context — parity with `rag_search`.
- No approval gate (parity with `rag_search`; it is a read-only external call).
- No new Python dependency — uses the existing `httpx` client.

Out of scope: SDK agents (`claude`, `codex`). Their `toolNames` are forced empty at creation (`agents.py`), so they use their own builtin tool sets and cannot resolve AChat-managed tools.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `tools`: Add a requirement that AChat SHALL provide a `web_search` tool with defined behavior (key source, cancellation, result bounding, opt-in availability, error handling). This is a spec-level addition to the existing tools capability.

## Impact

- **New file**: `backend/app/tools/web_search.py` (the `ToolDef` + handler).
- **Modified**: `backend/app/tools/registry.py` (import + one `reg.register(...)` line).
- **Modified**: `backend/app/config.py` (add `tavily_api_key: str | None` field + include it in `apply_env_overrides`).
- **Modified**: `.env.example` (add commented `TAVILY_API_KEY=`).
- **Spec sync** (CLAUDE.md §6.2 contract): `openspec/specs/tools/spec.md` and `specs/07-tools.md`.
- **External dependency**: outbound calls to `api.tavily.com`; consumes the user's Tavily API credits.
- **No DB schema change. No `agent_runner` change. No frontend code change** (agents enable the tool through the existing agent-builder toolNames UI).
