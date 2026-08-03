# Proposal: enhance-claude-cli-adapter

## Why

The Claude Code CLI adapter was migrated from the SDK route to the CLI subprocess route (`migrate-claude-codex-to-cli` change), but several critical features were left incomplete or deferred: session resume was never wired up (every run starts a fresh session, losing multi-turn context), security checks are fully bypassed (`bypassPermissions` + auto-approve all `control_request`), image attachments are ignored, the MCP Bridge tool set is hardcoded rather than reflecting agent configuration, and there is no timeout watchdog. These gaps make the Claude Code integration unsafe and unusable for real multi-turn workflows.

## What Changes

- **Session resume**: Add a `cli_session_id` column to the `AgentRun` model — capture `session_id` from the result event, persist it in DB, and pass `--resume` on subsequent runs by querying the latest run's `cli_session_id`. Add resume-failure fallback to fresh session. **BREAKING** (requires DB migration).
- **Smart approval**: Change `--permission-mode` from `bypassPermissions` to `acceptEdits`, parse `control_request` events, and route them through AChat's existing security infrastructure (`find_banned_pattern`, `classify_bash_approval`, `wait_for_bash_approval`, `pending_writes`, `resolve_safe_path`).
- **Attachment support**: Add image content blocks to `_write_prompt` so Claude Code receives user-uploaded images via stream-json `image` content blocks.
- **Dynamic MCP tools**: Pass agent-configured `tool_names` to the MCP Bridge process and dynamically generate the `ACHAT_MCP_TOOL_HINT` system prompt instead of hardcoding both. Refactor the MCP Bridge's `_execute_tool` to reuse a single module-level event loop instead of creating a new loop per tool call.
- **Timeout watchdog**: Add `semantic_inactivity_timeout` (10 min) and `first_turn_no_progress_timeout` (30 s) to `_read_events`, mirroring the Codex adapter pattern.
- **Blocked args hardening**: Add `--disallowedTools` and `--model` to `_claude_blocked_args` so users cannot override security-critical flags via `custom_args`.
- **Dead code cleanup**: Remove unused `output_parts` accumulator, fix `DEFAULT_CLAUDE_MODEL` to match the model registry, and rename the misleading `is_sdk_agent` variable in `_build_agent_hub_tool_guidance` (Claude Code / Codex are CLI adapters, not SDK).

## Capabilities

### New Capabilities

_(none — all changes enhance the existing `adapters` capability)_

### Modified Capabilities

- `adapters`: ClaudeCodeAdapter requirements change — permission mode shifts from `bypassPermissions` (no checks) to `acceptEdits` (smart routing through AChat security), session resume is wired up, attachment support is added, MCP tool exposure becomes dynamic, MCP Bridge event loop is fixed, blocked args are hardened, and timeout watchdog is introduced.

## Impact

- `backend/app/adapters/claude_adapter.py` — major changes: permission mode, control_request handler, attachment support, timeout watchdog, session_id capture, dynamic MCP tool hint, blocked args
- `backend/app/db/models.py` — add `cli_session_id` column to `AgentRun` model
- `backend/app/db/migrations/` — new migration script for `agent_runs.cli_session_id`
- `backend/app/adapters/session_store.py` — refactor to act as a DB-backed cache layer (in-memory dict for hot-path reads, DB for durability)
- `backend/app/mcp_bridge.py` — accept `--tool-names` argument, filter exposed tools dynamically, fix `_execute_tool` event loop reuse
- `backend/app/services/agent_runner.py` — query latest `cli_session_id` from `AgentRun` in `build_adapter_input`, persist `cli_session_id` after run, rename `is_sdk_agent` to `is_cli_agent`
- `backend/app/schemas/events.py` — add optional `session_id` field to `RunUsageEvent` / `RunEndEvent`
- `backend/app/utils/security.py` — no change (reused as-is)
- `backend/app/services/bash_command_approval.py` — no change (reused as-is)
- `backend/app/services/pending_writes.py` — no change (reused as-is)
- `openspec/specs/adapters/spec.md` — update ClaudeCodeAdapter requirements after `migrate-claude-codex-to-cli` is archived
