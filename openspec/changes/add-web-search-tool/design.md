## Context

AgentHub tools are `ToolDef`s (name / description / JSON-schema parameters / async handler) registered in a single global `tool_registry` (`backend/app/tools/registry.py`). At run time both `custom_adapter.py` and `claude_adapter.py` call `tool_registry.resolve(input.tool_names)` to expose the agent's tools to the model and `tool_registry.execute(name, args, ctx)` when the model calls one. `ToolContext` carries `conversation_id`, `workspace_path`, `agent_id`, `run_id`, and a `cancel_event` (`asyncio.Event`) set when the run is aborted.

Agent tool selection: `agents.py` persists `toolNames` only for `adapter_name == "custom"`; SDK adapters (`claude`, `codex`) are forced to `[]` and use their SDK's own tools. `agent_runner.py` additionally injects `memory_recall` (all custom agents) and the RAG tools (custom agents when `conv.rag_enabled`). This change deliberately does **not** add such an injection.

Existing precedent to mirror: `rag_search` (`app/tools/memory_rag.py`) — a read-only, no-approval tool that calls an external/service backend and bounds its output to top-5 results.

## Goals / Non-Goals

**Goals:**
- A `web_search` tool that, given a query, returns Tavily search results (and Tavily's synthesized `answer` when present) to the calling agent.
- Opt-in per agent via `toolNames` — zero implicit injection, zero `agent_runner` change.
- Key sourced from `.env` `TAVILY_API_KEY` through the existing `apply_env_overrides` fallback.
- Cancellable and bounded, consistent with existing tools.
- No new dependency; no DB/schema/frontend change.

**Non-Goals:**
- Making the tool available to SDK (`claude`/`codex`) agents.
- Auto-injecting the tool based on a conversation flag (no `web_search_enabled` field).
- Crawling/scraping beyond what Tavily returns, image/news verticals, or follow-up page fetching.
- Per-agent or app-settings key override (env-only for now; can extend later to the 3-tier pattern in CLAUDE.md §5.4).

## Decisions

### D1 — Strategy B (register only, opt-in via `toolNames`)
Register `web_search` in `tool_registry` and stop there. An agent gets it only by including `"web_search"` in its `toolNames` (set through the existing agent-builder UI). 
- *Why:* Minimal blast radius (no `agent_runner` edit), agent-level granularity, controls Tavily credit spend.
- *Alternatives:* (A1) always-inject for all custom agents like `memory_recall` — rejected: one-size-fits-all, uncontrolled cost. (A2) gate on a new `conv.web_search_enabled` column — rejected: requires a DB schema change.

### D2 — Raw `httpx` to Tavily REST, not `tavily-python`
Call `POST https://api.tavily.com/search` with an async `httpx.AsyncClient`. 
- *Why:* `httpx` is already a project dependency; CLAUDE.md requires justifying any new dependency. The Tavily endpoint is a single, stable JSON POST — an SDK adds no value here.
- *Request:* JSON body `{ "query": <str>, "max_results": 5, "include_answer": true }` with `Authorization: Bearer <TAVILY_API_KEY>` header.
- *Alternatives:* `tavily-python` SDK — rejected (new dep, no benefit). `aiohttp` (just installed) — rejected (httpx already used in code/tests, keep one client).

### D3 — Key via `config.tavily_api_key` + `apply_env_overrides`
Add `tavily_api_key: str | None = None` to `Settings`; read it in the handler via `get_settings().tavily_api_key`. Mirror it into `apply_env_overrides` for consistency with the other keys. Missing key → the handler returns a `ToolResult` error (never raises at startup), matching the "don't refuse service when a provider key is absent" rule.
- *Why:* Reuses the established env-fallback layer; never hardcodes a key (CLAUDE.md §5.4).

### D4 — Cancellation + timeout
Race the HTTP request against `ctx.cancel_event` using `asyncio.wait`; also pass an `httpx` timeout (e.g. 15s). If cancelled, return an error result and let the request task be cancelled.
- *Why:* CLAUDE.md §4.4 — external calls must honor the abort signal; a hung search must not block a stop.

### D5 — Output shape and bounding
Return `ok({ "answer": <str|None>, "results": [ {title, url, content, score} ][:5] })`. Truncate each result's `content` to a bounded length.
- *Why:* Parity with `rag_search`; Tavily result snippets are model-facing untrusted text (CLAUDE.md §5.1) and must not flood context.

### D6 — No approval gate
`web_search` executes without a pending-approval round-trip, like `rag_search`.
- *Why:* Read-only external lookup, no host/file/dependency mutation. (Cost is the only downside, mitigated by D1 opt-in + D5 bounding.)

## Risks / Trade-offs

- **Tavily credit consumption by an over-eager model** → Mitigated by D1 (opt-in per agent) and D5 (max_results=5).
- **Untrusted result text injected into the model context (prompt-injection vector)** → Mitigated by bounding length (D5); same trust posture as RAG results already in the system. Documented, not eliminated.
- **Missing/invalid `TAVILY_API_KEY`** → Handler returns a clear error result; agent run continues without crashing (D3).
- **Tavily API/network latency or outage** → Timeout + cancel race (D4) bound the wait; error surfaces to the model which can proceed without search.
- **Env-only key (no per-agent override)** → Accepted limitation; extensible later without breaking this design.
