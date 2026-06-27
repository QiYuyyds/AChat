## 1. Configuration & key

- [x] 1.1 Add `tavily_api_key: str | None = None` field to `Settings` in `backend/app/config.py`.
- [x] 1.2 Include `("TAVILY_API_KEY", s.tavily_api_key)` in the `apply_env_overrides()` loop in `backend/app/config.py`.
- [x] 1.3 Add a commented `TAVILY_API_KEY=` entry to `backend/.env.example` (under an "Web Search (Tavily)" section).

## 2. Tool implementation

- [x] 2.1 Create `backend/app/tools/web_search.py` defining `web_search_tool: ToolDef` with name `web_search`, a clear description, and JSON-schema parameters `{ query: string (required) }`.
- [x] 2.2 Implement the async handler: validate `query` (non-empty); read key via `get_settings().tavily_api_key`; return `err(...)` if the key is missing.
- [x] 2.3 Call `POST https://api.tavily.com/search` with `httpx.AsyncClient` — body `{query, max_results: 5, include_answer: true}`, `Authorization: Bearer <key>`, 15s timeout.
- [x] 2.4 Race the request against `ctx.cancel_event` (via `asyncio.wait`); on cancel, return an error result and cancel the request task.
- [x] 2.5 Shape the result as `ok({answer, results[:5]})` with each result `{title, url, content, score}` and content truncated to a bounded length; map HTTP/JSON errors to `err(...)`.

## 3. Registration

- [x] 3.1 In `backend/app/tools/registry.py`, import `web_search_tool` and add `reg.register(web_search_tool)` inside `_build_registry()`.

## 4. Spec sync (CLAUDE.md §6.2 contract)

- [ ] 4.1 Add the `web_search` requirement/scenarios to `openspec/specs/tools/spec.md` (promoted automatically by `openspec archive` — task 5.5).
- [x] 4.2 Add the `web_search` tool entry (signature, params, behavior) to `specs/07-tools.md`.

## 5. Verification

- [x] 5.1 `pnpm typecheck`/`ruff` on the backend pass (or backend equivalent: `ruff check` + import the module). — `ruff check` clean; `tool_registry.get('web_search')` resolves.
- [ ] 5.2 Manual smoke test: set `TAVILY_API_KEY` in `backend/.env`, create/edit a custom agent with `web_search` in its tools, ask it to search something, confirm results return.
- [ ] 5.3 Confirm a custom agent WITHOUT `web_search` in `toolNames` cannot call it, and an SDK agent never sees it.
- [ ] 5.4 Confirm missing-key path returns a clean error (no crash) and cancel/stop interrupts an in-flight search.
- [ ] 5.5 `openspec archive add-web-search-tool` after acceptance.
