# Tasks: enhance-claude-cli-adapter

## Phase 1: Spike — control_request Format Detection

- [x] 1.1 Create a debug branch in `claude_adapter.py` `_handle_control_request` that logs the full raw `request` dict (JSON-serialized) before any processing
- [x] 1.2 Run a manual test conversation that triggers Bash, Write, Edit, and AskUserQuestion tool calls, capturing the raw `control_request` JSON for each
- [x] 1.3 Document the confirmed field names for: tool name, tool input (command/path/content), and any session/state fields
- [x] 1.4 Verify whether `acceptEdits` mode sends `control_request` for read-only operations (Read, Glob, Grep) or only for write operations (Bash, Write, Edit)
- [x] 1.5 If the schema differs significantly from assumptions, update `design.md` Decision D2 and the `control_request` handler implementation plan

## Phase 2: Session Resume — DB Persistence + Cache Layer

- [x] 2.1 Add `cli_session_id` column to `AgentRun` model in `backend/app/db/models.py` (nullable String, no default)
- [x] 2.2 Create a migration script in `backend/app/db/migrations/` to add `agent_runs.cli_session_id`
- [x] 2.3 Change `session_store.py` session key from `conversation_id` to `conversation_id:agent_id` (matching Codex's `adapter_session_key` pattern)
- [x] 2.4 Refactor `session_store.py` to act as a DB-backed cache layer: in-memory dict for hot-path reads, DB query for cache misses, populate cache on DB hit
- [x] 2.5 In `claude_adapter.py`, capture `session_id` from the result event in `_handle_result`
- [x] 2.6 In `agent_runner.py` `consume_stream`, persist `cli_session_id` to `AgentRun` after the run completes (write to DB)
- [x] 2.7 In `agent_runner.py` `build_adapter_input`, query the latest `AgentRun` for the same `conversation_id` + `agent_id` where `cli_session_id IS NOT NULL`, ordered by `started_at DESC`, to get `cli_resume_session_id`
- [x] 2.8 Use the in-memory cache first; on cache miss, fall back to DB query and populate cache
- [x] 2.9 Pass `--resume <session_id>` in `_build_args` when `cli_resume_session_id` is present
- [x] 2.10 Implement resume-failure fallback: if `--resume` yields a different `session_id` or the run fails, retry without `--resume` (fresh session), capture new `session_id`, emit warning event
- [x] 2.11 Update `conversation_service` to clear the in-memory cache entry on conversation delete/clear/withdraw/regenerate (verify existing `clear_claude_code_session` calls still work with the new key format)
- [x] 2.12 Add `session_id` as an optional field to `RunUsageEvent` / `RunEndEvent` in `backend/app/schemas/events.py`

## Phase 3: Smart Approval — control_request Routing

- [x] 3.1 Change `--permission-mode` from `bypassPermissions` to `acceptEdits` in `_build_args`
- [x] 3.2 Remove `_auto_approve` blind-approval logic; replace with a routing dispatcher
- [x] 3.3 Implement `control_request` handler that extracts tool name and input from the raw request (using field names confirmed in Phase 1 spike)
- [x] 3.4 Route Bash tool calls through: `find_banned_pattern` → `classify_bash_approval` → `wait_for_bash_approval`, respond `allow`/`deny`
- [x] 3.5 Route Write/Edit tool calls through: `resolve_safe_path` → `pending_writes.register`, respond `allow`/`deny`
- [x] 3.6 Emit `ToolApprovalEvent` with `decision`, `reason`, `command`/`path`, `tool_name` for each routed control_request
- [x] 3.7 Handle edge case: unknown tool name in `control_request` → default to `deny` with a warning event
- [x] 3.8 Handle edge case: malformed `control_request` JSON → log warning, respond `deny`, continue processing
- [x] 3.9 Verify that `acceptEdits` mode does not send `control_request` for read-only operations (Read, Glob, Grep) — if it does, add fast-path auto-allow for those tool names

## Phase 4: Attachment Support

- [x] 4.1 In `_write_prompt`, iterate `input.attachments` and separate images from files
- [x] 4.2 For image attachments, read the file, base64-encode, add `{"type": "image", "source": {"type": "base64", "media_type": "<mime>", "data": "<base64>"}}` content block
- [x] 4.3 For file attachments, append a text note: "Attached file: <fileName> (<mimeType>) at <absPath>"
- [x] 4.4 Add a 10 MB size cap for image attachments; reject oversized images with a clear error event
- [x] 4.5 Verify stream-json `image` content block format is accepted by Claude Code (spike or documentation check)

## Phase 5: Dynamic MCP Tools

- [x] 5.1 In `claude_adapter.py`, pass agent `tool_names` to the MCP Bridge via `--tool-names` CLI argument (comma-separated)
- [x] 5.2 In `mcp_bridge.py`, accept `--tool-names` argument and filter the exposed tools accordingly
- [x] 5.3 Generate `ACHAT_MCP_TOOL_HINT` system prompt dynamically from the actual exposed tool list instead of hardcoding
- [x] 5.4 Handle edge case: agent has empty `tool_names` → MCP Bridge exposes no tools, hint is empty
- [x] 5.5 Handle edge case: agent has tool names not in the MCP Bridge's known tool registry → log warning, skip unknown tools
- [x] 5.6 Verify that CLI agents (Claude Code / Codex) are excluded from baseline tool merging in `agent_runner.py` (existing behavior, verify no regression)
- [x] 5.7 In `mcp_bridge.py`, fix `_execute_tool` to reuse a single module-level event loop instead of creating `asyncio.new_event_loop()` per call

## Phase 6: Timeout Watchdog

- [x] 6.1 Add `semantic_inactivity_timeout` (10 min) and `first_turn_no_progress_timeout` (30 s) constants to `claude_adapter.py` (mirror Codex adapter)
- [x] 6.2 In `_read_events`, use `asyncio.wait_for` with a 5-second polling interval to detect inactivity
- [x] 6.3 On timeout, emit `RunErrorEvent` with timeout details and terminate the CLI process
- [x] 6.4 Distinguish between first-turn timeout (no events at all within 30s) and inactivity timeout (no meaningful events for 10 min after the first event)
- [x] 6.5 Verify the watchdog does not trigger on slow-but-progressing runs (meaningful events reset the inactivity timer)

## Phase 7: Cleanup & Fixes

- [x] 7.1 Remove the unused `output_parts` accumulator in `claude_adapter.py` `run()` method
- [x] 7.2 Fix `DEFAULT_CLAUDE_MODEL` from `"claude-opus-4-8"` (not in `model_registry.py`) to a valid model ID or `None`
- [x] 7.3 Rename `is_sdk_agent` to `is_cli_agent` in `agent_runner.py` `_build_agent_hub_tool_guidance` function (variable is semantically wrong: Claude Code / Codex are CLI adapters, not SDK)
- [x] 7.4 Add `--disallowedTools` and `--model` to `_claude_blocked_args` in `claude_adapter.py`

## Phase 8: Testing

- [x] 8.1 Unit test: `_build_args` correctly includes `--resume` when `cli_resume_session_id` is present, omits it when absent
- [x] 8.2 Unit test: `control_request` handler correctly routes Bash commands through blacklist + approval, denies banned commands, allows safe ones
- [x] 8.3 Unit test: `control_request` handler correctly routes Write/Edit through path sandbox + pending writes, denies out-of-workspace paths
- [x] 8.4 Unit test: `_write_prompt` correctly encodes image attachments as base64 content blocks, rejects oversized images
- [x] 8.5 Unit test: MCP Bridge filters tools based on `--tool-names` argument, exposes only configured tools
- [x] 8.6 Unit test: timeout watchdog triggers `RunErrorEvent` after inactivity period, does not trigger on active runs
- [x] 8.7 Integration test: multi-turn conversation preserves session context across runs (first run captures session_id, second run resumes)

## Phase 9: Spec Archiving

- [x] 9.1 Run `ruff check .` and fix any lint errors introduced
- [x] 9.2 Run `pytest` and ensure all tests pass (including new tests from Phase 8)
- [x] 9.3 Update `openspec/specs/adapters/spec.md` with the new requirements after `migrate-claude-codex-to-cli` is archived (sync delta specs to main specs)
