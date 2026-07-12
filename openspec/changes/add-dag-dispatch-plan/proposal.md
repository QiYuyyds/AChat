# Add DAG Dispatch Plan

## Why

The Unified Agent Loop replaced the old three-stage Orchestrator (PLAN → EXECUTE → AGGREGATE) with an LLM-driven `task_dispatch` tool where the orchestrator decides each dispatch turn-by-turn. This is flexible for exploratory tasks but loses structured dependency-aware parallel execution: serial dependencies require extra LLM round-trips, cross-wave parallelism is impossible, and the full plan is never visible as a unit. For structured multi-task work (e.g. "build a web app": PRD → design → frontend + backend → integration test), a single declarative DAG that the system schedules is more efficient and predictable.

## What Changes

- Add a `dispatch_plan` tool available in coordinated mode alongside the existing `task_dispatch` tool. It accepts a declarative task list with `dependsOn` dependencies, validates the DAG, and executes it via wave-based topological scheduling — reusing the existing `spawn_subagent_loop` for each node.
- Add a DAG executor that performs topological sort, executes independent tasks in parallel within each wave (via `asyncio.gather`), propagates `skipped` status to downstream tasks when an upstream task fails, and returns all results as a single tool result.
- Add optional plan approval: when enabled (conversation-level flag), `dispatch_plan` emits `dispatch.plan.pending` before execution and awaits user approval via the existing `pending_dispatch_plans` infrastructure. Approval re-validates the plan; rejection cancels the dispatch.
- The `task_dispatch` tool remains unchanged — both tools coexist. `dispatch_plan` is for structured multi-task DAGs; `task_dispatch` is for single immediate dispatches. The orchestrator LLM chooses per situation.
- **No** verification gates, **no** retry harness, **no** `report_task_result`, **no** LLM judge. Sub-agent `end_turn` is completion; errors return `failed`. This is the key simplification vs the old deleted orchestrator (spec 06).
- Update the coordinated-mode system prompt to explain when to use `dispatch_plan` vs `task_dispatch`.

## Capabilities

### New Capabilities

_None_ — DAG dispatch is an extension of the orchestrator capability, not a separate capability.

### Modified Capabilities

- `orchestrator`: Add requirements for the `dispatch_plan` tool, DAG scheduling semantics, and optional plan approval flow. The orchestrator now has two dispatch mechanisms (`task_dispatch` for single immediate dispatch, `dispatch_plan` for structured multi-task DAG).
- `tools`: Add the `dispatch_plan` tool definition and registration rules (coordinated mode only).
- `stream-events`: Reuse existing `dispatch.plan.pending` / `dispatch.plan.resolved` events for the optional approval flow; add `dispatch.plan.executing` event to signal DAG execution start with the wave breakdown.

## Impact

- **New code**: `backend/app/tools/dispatch_plan.py` (tool def + DAG executor ~110 lines), `backend/app/services/dag_executor.py` (topological sort + wave scheduling ~80 lines).
- **Modified code**: `backend/app/services/agent_loop.py` (inject `dispatch_plan` into coordinated tool list, update system prompt suffix), `backend/app/tools/registry.py` (register new tool).
- **Reused infrastructure**: `spawn_subagent_loop` (existing), `pending_dispatch_plans` store (existing, currently dormant), `DispatchPlanItem` schema (existing).
- **No DB schema changes**: Plan approval flag is a runtime concern (conversation-level), not persisted as a new column in this iteration. The existing `pending_dispatch_plans` is in-memory.
- **No breaking changes**: `task_dispatch` continues to work unchanged. `dispatch_plan` is additive.
- **Frontend**: Existing dispatch plan review UI (for `dispatch.plan.pending`) already exists from the old orchestrator era and remains compatible. A future enhancement may visualize the DAG wave structure; not required for this change.
