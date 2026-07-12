# Design: DAG Dispatch Plan

## Context

The Unified Agent Loop (`run_agent_loop(mode='coordinated')`) gives the orchestrator a `task_dispatch` tool for single immediate dispatches. Each dispatch is one LLM round-trip; serial dependencies require the LLM to observe one result before issuing the next dispatch. This is flexible but inefficient for structured multi-task work with known dependencies.

The old three-stage Orchestrator (spec 06) had a full DAG scheduler (`executePlan`) but bundled it with verification gates (`report_task_result` + LLM judge), retry harnesses (`MAX_CHILD_TASK_APPROVERS`), and re-planning loops (`MAX_DISPATCH_ROUNDS`) — all of which added complexity that was deleted in the Unified Agent Loop change.

This design reintroduces **only** the DAG scheduling capability, as a tool within the unified loop, without the verification/retry complexity.

### Current state

- `task_dispatch` tool: single dispatch, returns `{ status, summary }`. Multiple calls in one turn run in parallel via `asyncio.gather` (already implemented in `agent_runner.py:_execute_tool_call_to_result`).
- `spawn_subagent_loop`: creates a child run, awaits completion, returns `LoopRunResult(status, text, artifact_ids, output_message_ids)`.
- `pending_dispatch_plans` store: in-memory, fully implemented but dormant (no new plans created since Unified Agent Loop).
- `DispatchPlanItem` schema: still exists with `id`, `agentId`, `task`, `dependsOn`, `inputs`, etc.
- `dispatch.plan.pending` / `dispatch.plan.resolved` events: still defined and wired.

### Constraints

- CLAUDE.md §3.6: Orchestrator MUST go through `run_agent_loop(mode='coordinated')` — no separate service path.
- CLAUDE.md §3.3: Stream events are the contract — new behavior must use existing or new event types.
- No DB schema changes in this iteration.
- Must not break `task_dispatch` (additive only).

## Goals / Non-Goals

### Goals

- Orchestrator can declare a complete DAG of tasks in one tool call; the system schedules waves respecting `dependsOn`.
- Parallel execution within a wave (independent tasks run concurrently).
- Failed upstream tasks cause downstream tasks to be `skipped` (not executed).
- Optional plan approval via existing `pending_dispatch_plans` infrastructure.
- Reuse `spawn_subagent_loop` for each DAG node — no new sub-agent spawning code.
- Coexist with `task_dispatch`; orchestrator LLM chooses per situation.

### Non-Goals

- Verification gates (`report_task_result`, LLM judge, acceptance criteria enforcement).
- Retry harness (`MAX_CHILD_TASK_APPROVERS`) or automatic re-planning (`MAX_DISPATCH_ROUNDS`).
- Worktree isolation per DAG node (existing `add-worktree-isolation` change handles that separately).
- File conflict detection across parallel nodes (out of scope; worktree isolation is the long-term answer).
- Persistent plan storage / cross-run plan history.
- Frontend DAG visualization (existing dispatch plan review card is reused as-is; wave visualization is a future enhancement).

## Decisions

### Decision 1: `dispatch_plan` as a tool, not a separate stage

**Choice**: Implement DAG dispatch as a `dispatch_plan` tool injected into the coordinated-mode tool list, not as a restored PLAN → EXECUTE stage.

**Why over alternatives**:
- *Alternative: restore `plan_tasks` + intercept in `execute_simple_run`*: Requires the loop to special-case one tool, pause, run DAG, inject results — effectively a separate service path. Violates §3.6.
- *Alternative: separate Orchestrator service*: Explicitly forbidden by §3.6.
- *Tool approach*: The tool handler runs synchronously within the loop's tool-execution phase. It calls `spawn_subagent_loop` for each node, gathers results, and returns them as the tool result. The loop continues naturally — the orchestrator's next turn sees the results and produces its final `end_turn` summary. No loop modification needed.

**Implication**: The entire DAG executes within a single tool call. The orchestrator LLM sees one `dispatch_plan` call and one result containing all node outcomes. This matches the "one tool = one logical action" model.

### Decision 2: DAG executor as a standalone module

**Choice**: `backend/app/services/dag_executor.py` with a pure `execute_dag()` function.

```
execute_dag(tasks, ctx) -> dict[task_id, NodeResult]
  1. Validate: no cycles, all dependsOn exist, no self-deps
  2. Topological sort into waves
  3. For each wave:
     - ready = tasks whose deps all completed successfully
     - skipped = tasks whose any dep failed/aborted → mark skipped, don't run
     - asyncio.gather(spawn_subagent_loop(node) for node in ready)
  4. Return all node results
```

**Why a separate module**: The DAG logic (validation + topological sort + wave scheduling) is testable in isolation with mocked `spawn_subagent_loop`. Keeps `dispatch_plan.py` (tool def) thin.

### Decision 3: Result shape — flat map, not nested

**Choice**: `dispatch_plan` returns `{ tasks: { t1: {status, summary}, t2: {status, summary}, ... } }`.

**Why**: The orchestrator LLM needs to see all outcomes at once to write its summary. A flat map is simpler to parse than a wave-structured result. The `skipped` status tells the LLM which tasks didn't run and why.

**Alternative considered**: Return only summaries, omit skipped tasks. Rejected — the LLM needs to know what was skipped to explain gaps to the user.

### Decision 4: Optional plan approval, conversation-level flag

**Choice**: Add a runtime flag `plan_approval_enabled` (default `False`). When `True`, `dispatch_plan` handler emits `dispatch.plan.pending` and awaits approval via `pending_dispatch_plans` before executing the DAG.

**Why optional and off by default**:
- The Unified Agent Loop philosophy is "LLM decides, system executes" — adding a mandatory approval gate for every DAG conflicts with that.
- For trusted/dev workflows, no approval is faster.
- For production/critical workflows, approval provides a safety net.
- The flag is read from conversation metadata (not a new DB column) — e.g. `conversation.metadata.get('plan_approval_enabled', False)` or a future settings field. This avoids a schema migration in this iteration.

**Approval flow**:
```
dispatch_plan handler:
  1. Validate DAG
  2. if plan_approval_enabled:
       pending = pending_dispatch_plans.register(plan, validator=revalidate)
       emit dispatch.plan.pending
       outcome = await wait for resolver
       if outcome.kind == 'reject': return { status: 'rejected' }
       if outcome.kind == 'revise': return { status: 'revise_requested', feedback }
       plan = outcome.plan  # may be user-edited, re-validated
  3. emit dispatch.plan.executing (with waves)
  4. results = execute_dag(plan, ctx)
  5. return { tasks: results }
```

### Decision 5: Reuse `DispatchPlanItem` schema as-is

**Choice**: The `dispatch_plan` tool parameters use `DispatchPlanItem` structure (`id`, `agentId`, `task`, `dependsOn`). No schema changes.

**Why**: The schema already exists and is compatible. Adding `taskKind`, `acceptanceCriteria`, etc. are optional fields that the DAG executor ignores (no verification gates). Keeping the schema unchanged means no migration.

### Decision 6: System prompt guidance for `dispatch_plan` vs `task_dispatch`

**Choice**: Update `_COORDINATED_PROMPT_SUFFIX` to explain both tools and when to use each.

```
### 使用 dispatch_plan 的时机（结构化多任务）
- 任务有明确的依赖关系图（DAG）
- 需要 3+ 个子任务，且部分可并行
- 用户要求生成完整项目（PRD → 设计 → 前端+后端 → 集成）

### 使用 task_dispatch 的时机（即时单任务）
- 只需派发一个任务
- 需要根据上一个结果决定下一步（探索性）
- 快速试错
```

## Risks / Trade-offs

### [Risk] LLM produces incorrect `dependsOn`, causing wrong parallelism

LLMs sometimes write dependencies in the task text but forget the `dependsOn` field, or declare false dependencies.

→ **Mitigation**: The system prompt explicitly instructs "能并行的不写 dependsOn，有依赖的明确写". A lightweight dependency inference (like the old `compileDispatchPlan`) can be added later if needed, but is **not** included in this change to keep scope small. The DAG validator only rejects cycles and missing references — it does not infer dependencies.

### [Risk] Single tool call blocks for a long time (all sub-agents run sequentially within)

A 5-node DAG with 3 waves could take minutes. The orchestrator's loop is blocked waiting for the tool result.

→ **Mitigation**: This is inherent to any dispatch mechanism. `task_dispatch` has the same property for a single sub-agent. The existing SSE events (`dispatch.start` / `dispatch.end` per node) keep the UI live during execution. The `dispatch.plan.executing` event signals the start. Cancel propagation works via `parent_cancel_event` (already wired in `spawn_subagent_loop`).

### [Risk] Tool result too large if many nodes return long summaries

→ **Mitigation**: Each node returns only its `end_turn` text (already the case in `spawn_subagent_loop` → `_extract_run_final_text`). Artifacts are referenced by ID, not inlined. For very large DAGs, a future enhancement could truncate/summarize per-node results, but this is not needed now.

### [Risk] Plan approval blocks indefinitely if user doesn't respond

→ **Mitigation**: The `pending_dispatch_plans` store already supports `cancel()` (called on parent run abort). The orchestrator run's `cancel_event` propagates. No timeout is added in this iteration — if the user abandons, they can cancel the run.

### [Trade-off] No automatic re-planning on failure

Old system had `MAX_DISPATCH_ROUNDS` for auto re-planning. This change does not.

→ **Mitigation**: All node results (including `failed` and `skipped`) are returned to the orchestrator LLM. The LLM can issue a second `dispatch_plan` call for the failed/skipped nodes if it judges that worthwhile. This is "LLM-driven re-planning" rather than "system-driven re-planning" — consistent with the Unified Agent Loop philosophy. No retry loop is needed.

### [Trade-off] Coexistence of two dispatch tools may confuse the LLM

→ **Mitigation**: System prompt clearly delineates use cases. In practice, the LLM will naturally prefer `dispatch_plan` for multi-task structured work and `task_dispatch` for quick single dispatches. If confusion arises, the prompt can be tuned.
