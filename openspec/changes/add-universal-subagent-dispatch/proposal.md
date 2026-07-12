# Proposal

## Why

Currently only the orchestrator in a coordinated (group) conversation can dispatch sub-agents via `task_dispatch`. Solo agents and dispatched sub-agents cannot delegate further. This limits capability: a single-chat agent handling a complex task cannot split work into parallel subtasks the way Claude Code or Codex do with their Agent tool. The unified agent loop already supports three modes (solo / coordinated / subagent), but the subagent path is hardcoded to `execute_simple_run` with no dispatch tools, and solo mode never injects `task_dispatch`.

## What Changes

- **Subagent runs can recursively dispatch**: `execute_run` no longer hardcodes `execute_simple_run` for `override_prompt` runs; instead it calls `run_agent_loop(mode="subagent")` which injects `task_dispatch` (subject to depth limit).
- **Solo agents can dispatch subagents**: `_run_solo_loop` injects `task_dispatch` when `dispatch_depth < MAX_DISPATCH_DEPTH`, allowing any solo agent to clone itself for subtasks.
- **`task_dispatch` agentId becomes optional**: When omitted (or equal to caller's own agent_id), the tool clones the calling agent. When specified in coordinated mode, it dispatches to a group member (existing behavior).
- **`dispatch_depth` field on `RunArgs`**: Tracks recursion depth. `MAX_DISPATCH_DEPTH = 3`. At max depth, `task_dispatch` is not injected — the agent is a terminal executor.
- **`dispatch_visibility` field on `RunArgs`**: `"visible"` for group-member dispatch (messages appear in conversation), `"hidden"` for clone-self dispatch (messages excluded from history and frontend rendering).
- **`Message.hidden` column**: New boolean column (default `false`). `build_history_for` filters `hidden = false`. Clone-subagent messages are persisted with `hidden = true` to prevent context pollution.
- **Group-member anti-loop**: Subagent runs (non-coordinated mode) can only clone themselves; they cannot dispatch to other group members, preventing A→B→A cycles.
- **Token panel roll-up**: Subagent run tokens are attributed to the top-level parent agent's usage card, with a "含 subagent: Nk tok" annotation.
- **System prompt guidance**: Solo and subagent modes get dispatch-usage instructions (when to use, when not to, subagent context isolation reminder).

## Capabilities

### New Capabilities

_(None — all changes extend existing capabilities.)_

### Modified Capabilities

- `orchestrator`: Solo and subagent agents can now use `task_dispatch` to clone themselves. Subagent runs no longer hardcode to solo mode. The `agentId` parameter on `task_dispatch` becomes optional.
- `conversation-context`: `build_history_for` filters `hidden = true` messages. Clone-subagent messages are excluded from conversation history to prevent context pollution.
- `persistence`: `Message` table gains a `hidden` boolean column (default `false`) to distinguish clone-subagent messages from normal conversation messages.
- `tools`: `task_dispatch` tool parameter `agentId` changes from required to optional. Validation logic splits by dispatch mode (coordinated validates agent ∈ conversation; non-coordinated only allows cloning self).
- `frontend`: Token usage panel attributes subagent run tokens to the top-level parent agent. `AgentUsageDetail` gains `subagentTokens` and `subagentRunCount` fields. Hidden messages are not rendered in the chat view.

## Impact

- **DB schema**: `messages` table gets `hidden BOOLEAN NOT NULL DEFAULT FALSE` via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `engine.py`. No data migration needed — old messages default to `visible`.
- **Backend services**: `agent_runner.py` (execute_run routing, RunArgs, persist_event), `agent_loop.py` (_run_solo_loop, new _run_subagent_loop, spawn_subagent_loop), `conversation_context.py` (history query), `dag_executor.py` (depth/visibility passthrough).
- **Backend tools**: `task_dispatch.py` (agentId optional, depth check, anti-loop, visibility), `dispatch_plan.py` (same), `base.py` (ToolContext new fields), `registry.py` (no structural change).
- **Frontend**: `app-store.ts` (usage roll-up logic), `usage-badge.tsx` (subagent annotation row), message rendering (filter hidden messages).
- **Specs**: `specs/19-unified-agent-loop.md` updated with recursive subagent dispatch, `dispatch_depth`, `dispatch_visibility`, `hidden` message concept. `CLAUDE.md §3.6` updated — Orchestrator is no longer the only agent that can dispatch.
- **No new dependencies**. No new external packages. No new event types (dispatch.start/dispatch.end already exist).
