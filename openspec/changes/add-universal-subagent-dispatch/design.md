# Design

## Context

The Unified Agent Loop (spec 19) already unifies three modes — solo, coordinated, subagent — through `run_agent_loop`. However, the routing in `execute_run` hardcodes subagent runs (those with `override_prompt`) to `execute_simple_run` directly, bypassing `run_agent_loop` entirely. This means subagents never get `task_dispatch` in their tool list. Similarly, solo mode (`_run_solo_loop`) never injects `task_dispatch`.

The result: only the orchestrator in a coordinated conversation can dispatch sub-agents. A solo agent or a dispatched sub-agent cannot delegate further — unlike Claude Code or Codex where any agent can spawn sub-agents.

The existing `spawn_subagent_loop` is already a generic dispatch primitive (takes any `agent_id`, creates `RunArgs`, calls `run_with_args`). The `AgentRun` table already has `parent_run_id` for call-chain tracking. The infrastructure is in place; the gaps are in routing, tool injection, context isolation, and token accounting.

## Goals / Non-Goals

**Goals:**

- Any agent (solo / coordinated / subagent) can clone itself via `task_dispatch` to handle subtasks.
- Subagent dispatch is recursive up to `MAX_DISPATCH_DEPTH = 3`.
- Clone-subagent messages are hidden from conversation history and frontend rendering (context isolation).
- Group-member dispatch (coordinated mode) remains visible — existing behavior unchanged.
- Token usage from subagent runs rolls up to the top-level parent agent in the usage panel.
- Group-member anti-loop: subagents can only clone themselves, not dispatch to other group members.

**Non-Goals:**

- No per-agent `max_dispatch_depth` configuration (global constant only).
- No workspace isolation for subagents (they share the parent's workspace; worktree isolation is a separate change).
- No new event types (dispatch.start / dispatch.end already exist).
- No new tools (reuse `task_dispatch` with optional `agentId`).
- No changes to adapter layer (subagent runs go through the same `execute_simple_run`).
- No changes to `Conversation.dispatch_mode` (solo / orchestrated unchanged).
- No `dispatch_plan` for solo/subagent modes initially (only `task_dispatch`; `dispatch_plan` support can be added later if needed).

## Decisions

### Decision 1: Clone-self as the default subagent model

**Choice**: When `agentId` is omitted on `task_dispatch`, the calling agent's own `agent_id` is used. The subagent is a full clone (same model, same tools, same system prompt) with only a different task prompt.

**Alternatives considered**:
- *From Agent library*: Let the caller pick any agent from the `agents` table. More flexible but requires deciding who can be picked, adds validation complexity, and blurs the line between "group member dispatch" and "subagent clone."
- *Template presets*: Predefine lightweight subagent templates. Adds a new management mechanism for marginal benefit.

**Rationale**: Clone-self matches Claude Code's Agent tool behavior exactly. It's the simplest model — no new entity, no new validation path. The `agentId` parameter already exists on `task_dispatch`; making it optional is a minimal change.

### Decision 2: `dispatch_depth` on RunArgs (in-memory, not DB)

**Choice**: Add `dispatch_depth: int = 0` to `RunArgs`. Each `spawn_subagent_loop` call passes `dispatch_depth + 1`. When `dispatch_depth >= MAX_DISPATCH_DEPTH`, `task_dispatch` is not injected into the tool list.

**Rationale**: The depth is a runtime control, not a persistent property. `AgentRun.parent_run_id` already records the chain in DB; depth can be derived but passing it explicitly is simpler and avoids a DB query at tool-injection time.

**MAX_DISPATCH_DEPTH = 3**: Allows depth 0 → 1 → 2 → 3, where depth 3 is the terminal executor. This matches typical Claude Code usage (rarely more than 2-3 levels deep).

### Decision 3: `dispatch_visibility` on RunArgs (visible vs hidden)

**Choice**: Add `dispatch_visibility: str = "visible"` to `RunArgs`. Two values:
- `"visible"`: Group-member dispatch in coordinated mode. Messages appear in conversation, enter history, token counted independently. (Existing behavior.)
- `"hidden"`: Clone-self dispatch. Messages persisted with `hidden=true`, excluded from `build_history_for`, not rendered in frontend, tokens rolled up to parent.

**Determination logic** (in `task_dispatch` handler):
- `agentId` is None or equals `ctx.agent_id` → `hidden` (clone)
- `agentId` is specified and differs from `ctx.agent_id` → `visible` (group member, only valid in coordinated mode)

### Decision 4: `Message.hidden` column for context isolation

**Choice**: Add `hidden BOOLEAN NOT NULL DEFAULT FALSE` to the `messages` table. `build_history_for` adds `AND hidden = false` to its query. `persist_event` sets `hidden` based on `RunArgs.dispatch_visibility`.

**Why not filter by `parent_run_id IS NULL`**: Group-member dispatch also has `parent_run_id`, but those messages should remain visible. The `hidden` flag gives explicit per-message control rather than inferring from run hierarchy.

**Migration**: `ALTER TABLE messages ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE` in `engine.py`. Old messages default to `false` (visible). No data backfill needed.

### Decision 5: Subagent mode routing change in `execute_run`

**Choice**: Replace the `if args.override_prompt: execute_simple_run(...)` branch with `if args.override_prompt: run_agent_loop(..., mode="subagent")`.

The new `_run_subagent_loop` function:
1. Loads the agent.
2. Builds tool list: agent's own tools + `task_dispatch` (if `dispatch_depth < MAX_DISPATCH_DEPTH`).
3. Builds system prompt: agent's base prompt + `_SUBAGENT_SUFFIX` (dispatch guidance + context isolation reminder).
4. Delegates to `execute_simple_run` with overridden tools and prompt.

**Rationale**: This is the minimal change to unblock recursive dispatch. The existing `execute_simple_run` while-loop handles everything else (model calls, tool execution, streaming, persistence).

### Decision 6: Group-member anti-loop enforcement

**Choice**: In `task_dispatch` handler, when the caller is not in coordinated mode (i.e., the caller is a solo or subagent run), reject any `agentId` that differs from `ctx.agent_id`.

```python
if ctx.dispatch_mode != "coordinated" and agentId is not None and agentId != ctx.agent_id:
    return err("Subagent can only clone itself; cannot dispatch to other agents")
```

**Rationale**: In coordinated mode, the orchestrator dispatches to group members (visible). Once a group member is dispatched, it runs in subagent mode — it can only clone itself, not dispatch to other group members. This prevents A→B→A cycles without needing a dispatch-chain tracking data structure.

### Decision 7: Token roll-up to top-level parent agent

**Choice**: In the frontend `useConversationUsageTotal` hook, walks the `parent_run_id` chain to find the top-level run, then attributes the subagent's tokens to that run's `agent_id`. The `AgentUsageDetail` type gains `subagentTokens` and `subagentRunCount` fields. The `AgentUsageCard` component renders an additional line: "含 subagent: Nk tok · M 次".

**Why not count independently**: A clone-subagent has the same `agent_id` as its parent. Without roll-up, the usage panel would show "Agent A: 8k tok · 2 次" which is confusing — the user initiated one run, not two.

**Why not separate display**: A tree-structured usage panel is more complex UI for marginal benefit. A single annotation line is sufficient.

### Decision 8: `dispatch_plan` not injected for solo/subagent initially

**Choice**: Only `task_dispatch` is injected for solo and subagent modes. `dispatch_plan` (DAG) remains coordinated-mode-only.

**Rationale**: `dispatch_plan` is for structured multi-task DAGs with dependencies — primarily an orchestrator tool. Solo agents typically need ad-hoc single dispatches, not full DAG planning. Adding `dispatch_plan` to solo/subagent later is a trivial extension if needed.

## Risks / Trade-offs

- **[LLM over-dispatches subagents]** → System prompt explicitly instructs when to use vs not use `task_dispatch`. `MAX_DISPATCH_DEPTH` provides a hard ceiling. Economic cost (each subagent is a full run) provides natural friction.

- **[Context pollution if `hidden` flag is wrong]** → `persist_event` derives `hidden` from `RunArgs.dispatch_visibility`, which is set by `spawn_subagent_loop` based on the clone-vs-group-member determination. The logic is centralized in one place (the `task_dispatch` handler) to minimize error surface.

- **[Parallel subagents writing same file]** → Subagents share the parent's workspace. Concurrent writes to the same file can conflict. This matches Claude Code behavior — the LLM is expected to split tasks that don't overlap on the same files. No file-level locking is added.

- **[CLI agent (Claude Code / Codex) subagent compatibility]** → CLI agents use `override_prompt` to skip history/RAG/summary injection, which already works. The subagent run creates a fresh CLI session with the task prompt. Should be compatible but needs testing.

- **[Token panel complexity]** → The roll-up logic adds a `parent_run_id` chain walk to `useConversationUsageTotal`. This is O(n) in the number of runs, which is already the case for the existing aggregation. The chain walk uses a pre-built `runs` map, so no additional API calls.

- **[Deep recursion consuming excessive tokens]** → `MAX_DISPATCH_DEPTH = 3` limits the chain. Each level is a full run with its own context window, so total token consumption is bounded by `depth × context_window`. The system prompt guidance discourages unnecessary dispatch.
