# Design: enhance-claude-cli-adapter

## Context

The Claude Code CLI adapter (`ClaudeCLIAdapter` in `backend/app/adapters/claude_adapter.py`) was migrated from the SDK route to the CLI subprocess route via the `migrate-claude-codex-to-cli` change. The migration landed the core CLI communication (stream-json protocol, event translation, process lifecycle) but left several features incomplete or deferred:

- **Session resume**: `session_store.py` has a `claude_code_sessions` dict that is never written to or read from. `cli_resume_session_id` is hardcoded to `None`. Every run starts a fresh Claude Code session, losing all multi-turn context. There is no DB column to persist session IDs across restarts.
- **Security**: `--permission-mode bypassPermissions` + `_auto_approve` (blindly responds `allow` to every `control_request`). AChat's existing security infrastructure (`find_banned_pattern`, `classify_bash_approval`, `pending_writes`, `resolve_safe_path`) is not invoked.
- **Attachments**: `_write_prompt` sends only `{"type": "text", "text": input.prompt}`. The `input.attachments` field (already populated by `build_adapter_input`) is ignored — Claude Code never sees user-uploaded images.
- **MCP tools**: `CLI_MCP_TOOL_NAMES` in `mcp_bridge.py` is a hardcoded frozenset of 6 tools. `ACHAT_MCP_TOOL_HINT` in the adapter is also hardcoded. Neither reflects the agent's configured `tool_names`.
- **MCP Bridge event loop**: `_execute_tool` in `mcp_bridge.py` creates a new `asyncio.new_event_loop()` for every tool call. This is wasteful and can break cross-loop references (shared asyncio primitives).
- **Timeout**: No inactivity or first-turn timeout. A hung CLI process blocks `_read_events` indefinitely until user cancellation.
- **Blocked args gaps**: `--disallowedTools` and `--model` are not in `_claude_blocked_args`. Users can override security-critical flags via `custom_args` (e.g., clear the `AskUserQuestion` disallow list or override the model).
- **Dead code / naming**: `output_parts` list is accumulated but never read. `DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"` doesn't exist in `model_registry.py`. `is_sdk_agent = agent.adapter_name in ("claude-code", "codex")` in `_build_agent_hub_tool_guidance` is semantically wrong (CLI ≠ SDK) and the branch is dead code for CLI agents (the function is only called when `is_sdk=True`).

AChat already has a mature security stack for SDK agents: `security.py` (command blacklist), `bash_command_approval.py` (approval classification + wait), `pending_writes.py` (file write review), `workspace_utils.py` (path sandbox), and `hooks/tool_approval.py` (orchestration). All of this is reusable for CLI agents if `control_request` events are routed through it.

## Goals / Non-Goals

**Goals:**
- Wire up session resume so multi-turn conversations with Claude Code preserve context across runs and backend restarts
- Route `control_request` events through AChat's security infrastructure (blacklist, approval, pending write, path sandbox)
- Support image attachments via stream-json `image` content blocks
- Make MCP Bridge tool exposure dynamic based on agent configuration
- Fix MCP Bridge event loop to reuse a single loop instead of creating one per call
- Add timeout watchdog to prevent indefinite hangs
- Harden blocked args so users cannot override security-critical CLI flags
- Clean up dead code and fix misleading variable names

**Non-Goals:**
- In-memory-only session store (rejected — session loss on backend restart is a poor user experience; DB persistence is the baseline)
- Changing the fundamental CLI subprocess architecture (staying on CLI route, not reverting to SDK)
- Adding new MCP tools (e.g. `web_search`, `rag_search`) — only making existing tools dynamically available
- Archiving the `migrate-claude-codex-to-cli` change (separate concern; this change assumes CLI route as baseline)
- Fixing Windows ConPTY (separate platform issue; the STARTUPINFOEX alignment error 87 is unrelated to adapter correctness)

## Decisions

### D1: Session resume via DB column (A2), with in-memory cache layer

**Choice**: Add a `cli_session_id` column to the `AgentRun` model. After each run, persist the `session_id` captured from the result event into `AgentRun.cli_session_id`. Before the next run, query the latest `AgentRun` for the same `conversation_id` + `agent_id` to retrieve the `cli_session_id` for `--resume`. The existing `session_store.py` in-memory dict is retained as a hot-path cache layer on top of the DB lookup.

**Rationale**: The in-memory dict (A1) was rejected because backend restarts lose all session IDs, forcing users to restart conversations — a poor experience for a local-first tool where the user may restart the backend frequently. DB persistence (A2) survives restarts and supports future multi-instance backends. The schema change is straightforward (one nullable string column on `AgentRun`) and CLAUDE.md §6.2's "discuss DB schema changes" requirement is satisfied by this proposal review.

**Query pattern**: On `build_adapter_input`, query the most recent completed `AgentRun` for the same `conversation_id` + `agent_id` where `cli_session_id IS NOT NULL`, ordered by `started_at DESC`. This naturally handles the case where only some runs in a conversation are CLI runs (SDK agent runs have `cli_session_id = NULL`).

**Cache layer**: `session_store.py`'s in-memory dict is retained and used as a first-level cache — if the dict has a session_id for the `conversation_id:agent_id` key, skip the DB query. On a cache miss, fall back to DB and populate the cache. This avoids a DB round-trip on the hot path while keeping durability.

**Cache invalidation**: `conversation_service` already calls `clear_claude_code_session(conversation_id)` on delete/clear/withdraw/regenerate. This clears the in-memory cache. The DB column is not cleared (old runs retain their `cli_session_id`), but the query naturally picks the latest run — if the latest run after a delete has no `cli_session_id` (because it's a fresh SDK run or the CLI run was deleted), resume is skipped.

**Key format change**: The in-memory dict key changes from `conversation_id` to `conversation_id:agent_id` (matching Codex's `adapter_session_key` pattern), fixing group chat with multiple Claude Code agents.

### D2: Permission mode `acceptEdits` + `control_request` routing (B1)

**Choice**: Change `--permission-mode` from `bypassPermissions` to `acceptEdits`. Parse `control_request` events and route through AChat security.

**Rationale**: `acceptEdits` lets Claude Code auto-approve read-only operations (Read, Glob, Grep) while asking for permission on write operations (Bash, Write, Edit). This maps naturally to AChat's security model:
- Bash → `find_banned_pattern` + `classify_bash_approval` + `wait_for_bash_approval`
- Write/Edit → `pending_writes.register` + `await_pending_decision` (review mode) or auto-allow (trust mode)
- Path checks → `resolve_safe_path` / `assert_path_within_workspace`

**Alternatives considered**:
- B2 (MCP-wrapped tools): Disable built-in Bash/Write/Edit via `--disallowedTools`, provide MCP replacements. Rejected because Claude Code is trained to use its built-in tools; MCP tool adoption is unreliable and the inter-process overhead per tool call is significant.
- B3 (CLI `--allowedTools` / `--disallowedTools` rules): Rejected because Claude Code's rule syntax is less expressive than AChat's blacklist, has no pending write flow, and doesn't enforce workspace path sandbox.

**Risk**: The exact `control_request` JSON schema is not well-documented. A **spike** (Task T1) logs the raw `request` dict before implementing the handler, to verify field names for tool name, input, and command.

### D3: Image attachments via stream-json content blocks

**Choice**: In `_write_prompt`, when `input.attachments` contains image entries, add `{"type": "image", "source": {"type": "base64", "media_type": "<mime>", "data": "<base64>"}}` content blocks to the user message.

**Rationale**: Claude Code's stream-json `--input-format` accepts Anthropic Messages API content blocks, which include the `image` type with base64 source. The adapter already has `abs_path` on each `AdapterAttachment`, so reading and base64-encoding the file is straightforward.

**File attachments** (non-image): Appended as a text note in the prompt ("Attached file: <fileName> (<mimeType>) at <absPath>") so Claude Code can use its built-in Read tool to access it.

### D4: Dynamic MCP tool set + event loop fix

**Choice**: Pass agent `tool_names` to the MCP Bridge via a `--tool-names` CLI argument. MCP Bridge filters its exposed tools accordingly. `ACHAT_MCP_TOOL_HINT` is generated dynamically from the actual tool list. Additionally, fix `_execute_tool` to reuse a single module-level event loop instead of creating `asyncio.new_event_loop()` per call.

**Rationale**: Currently `CLI_MCP_TOOL_NAMES` is a hardcoded frozenset and `ACHAT_MCP_TOOL_HINT` is a hardcoded string. If an agent has `web_search` or `rag_search` configured, neither the MCP Bridge nor the system prompt reflects it. Passing the tool list dynamically ensures the agent's configuration is the single source of truth.

The event loop fix addresses a separate issue: `_execute_tool` creates a new `asyncio.new_event_loop()` for every async tool call. This is wasteful (loop creation/teardown per call) and can break if a tool handler holds a reference to an asyncio primitive (e.g., `asyncio.Event`, `asyncio.Lock`) created on a different loop. The fix creates a single module-level loop on first use and reuses it for all subsequent calls.

### D5: Timeout watchdog mirroring Codex adapter

**Choice**: Add `semantic_inactivity_timeout` (10 min) and `first_turn_no_progress_timeout` (30 s) to `_read_events`, using `asyncio.wait_for` with a 5-second polling interval.

**Rationale**: The Codex adapter already has this pattern (`DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT`, `DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT`). Claude adapter has no timeout at all — a hung CLI process blocks indefinitely. The same constants and logic structure are reused for consistency.

### D6: Blocked args hardening

**Choice**: Add `--disallowedTools` and `--model` to `_claude_blocked_args`.

**Rationale**: `--disallowedTools` is hardcoded as `AskUserQuestion` in `_build_args`. If a user adds `--disallowedTools ""` via `custom_args`, it would clear the disallow list and re-enable `AskUserQuestion` (which returns empty answers in non-interactive mode, causing silent inference). `--model` is set from `agent.model_id` in `_build_args`; if a user also adds `--model` via `custom_args`, the CLI's last-wins behavior makes the outcome unpredictable. Blocking both prevents accidental security or behavior overrides.

### D7: Dead code cleanup + naming fix

**Choice**: Remove `output_parts` accumulator (filled but never read). Fix `DEFAULT_CLAUDE_MODEL` from `"claude-opus-4-8"` (not in `model_registry.py`) to `"claude-opus-4-7"` or `None`. Rename `is_sdk_agent` to `is_cli_agent` in `_build_agent_hub_tool_guidance`.

**Rationale**: `output_parts` is dead code violating CLAUDE.md §4.3 "不留废代码". `DEFAULT_CLAUDE_MODEL` produces incorrect usage metadata. `is_sdk_agent = agent.adapter_name in ("claude-code", "codex")` is semantically wrong — Claude Code and Codex are CLI adapters, not SDK — and the branch is dead code because the function is only called when `is_sdk=True` (i.e., `adapter_name in SDK_ADAPTERS = {"custom"}`).

## Risks / Trade-offs

- **[Risk] `control_request` schema unknown** → Mitigation: Task T1 is a spike that logs raw `control_request` JSON before implementing the handler. If the schema differs significantly from assumptions, the handler adapts.
- **[Risk] `acceptEdits` may send control_request for every write, slowing down long file-edit sequences** → Mitigation: AChat's `pending_writes` already has a trust mode that auto-approves. The conversation's `fs_write_approval_mode` controls this — `trust` mode auto-allows, `review` mode asks the user.
- **[Risk] Session resume fails if Claude Code's session file expired** → Mitigation: If `--resume` yields a different session_id or the run fails, clear the stored session_id (both in-memory cache and by not persisting the new run's `cli_session_id`) and retry with a fresh session. The `migrate-claude-codex-to-cli` delta spec already designed this scenario.
- **[Risk] Large image base64 in stdin payload** → Mitigation: Cap image size (e.g., 10 MB); reject oversized images with a clear error. Most user screenshots are well under this.
- **[Risk] DB migration on existing installations** → Mitigation: The `cli_session_id` column is nullable with no default; existing rows get NULL. The query in `build_adapter_input` filters `WHERE cli_session_id IS NOT NULL`, so pre-migration runs are simply ignored. Migration is additive (no data loss, no column rename).
- **[Trade-off] DB query on every `build_adapter_input` for CLI agents** → Mitigation: The in-memory cache layer avoids the DB query on cache hit (the common case — same backend process, same conversation). The DB query only runs on cache miss (first run after restart or after cache invalidation).

## Migration Plan

This change requires a DB migration but is backwards-compatible at the API level:
- **DB migration**: Add nullable `cli_session_id` column to `agent_runs` table. Existing rows get NULL (no data loss). The migration is additive — rollback drops the column.
- Existing Claude Code agents continue to work (permission mode change is transparent — users just start seeing approval prompts for dangerous commands, which is the intended behavior).
- No API contract change (new `session_id` field on `RunUsageEvent` is optional).
- Rollback: revert code changes + drop `cli_session_id` column. No data migration needed (the column only stores transient CLI session IDs that are safe to lose).
- The in-memory `session_store.py` cache layer ensures that even if the DB migration hasn't run yet, the system degrades gracefully to A1 behavior (in-memory only, lost on restart).

## Open Questions

1. **`control_request` field names**: What exact field names does Claude Code use for tool name, input, and command in `control_request` events? (Resolved by spike in Task T1)
2. **Image content block format**: Does Claude Code stream-json accept `{"type": "image", "source": {"type": "base64", ...}}` (Anthropic Messages API format) or a different shape? (Resolved by spike or documentation check)
3. **`--permission-mode` compatibility**: Does `acceptEdits` work correctly with `--include-partial-messages` and `--input-format stream-json`? (Needs verification)
