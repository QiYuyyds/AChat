# Implementation Tasks: DAG Dispatch Plan

## 1. DAG Executor Module

- [x] 1.1 Create `backend/app/services/dag_executor.py` with `validate_dag(tasks)` — checks duplicate ids, self-deps, missing dependsOn references, cycles (via topological sort); returns list of error strings or empty
- [x] 1.2 Implement `topological_waves(tasks)` — groups tasks into waves where each wave contains tasks whose all deps are in prior waves; raises on cycle
- [x] 1.3 Implement `execute_dag(tasks, ctx)` async function — iterates waves, runs ready tasks via `spawn_subagent_loop` in parallel (`asyncio.gather`), marks downstream of failed/aborted as `skipped`, returns `dict[task_id, NodeResult]`
- [x] 1.4 Define `NodeResult` dataclass: `task_id`, `status` (complete/failed/aborted/skipped), `summary`, `child_run_id` (None for skipped)
- [x] 1.5 Emit `dispatch.start` / `dispatch.end` events per node (reuse `DispatchStartEvent` / `DispatchEndEvent` from events.py); skipped nodes emit only `dispatch.end` with `status="skipped"` and no `childRunId`

## 2. dispatch_plan Tool Definition

- [x] 2.1 Create `backend/app/tools/dispatch_plan.py` with `DispatchPlanTool` definition
- [x] 2.2 Tool name: `dispatch_plan`; parameters: `tasks` (array of `{ id, agentId, task, dependsOn? }`)
- [x] 2.3 Handler: call `validate_dag()` — return error tool result on validation failure
- [x] 2.4 Handler: emit `dispatch.plan` event (reuse `DispatchPlanEvent`) with validated plan before execution
- [x] 2.5 Handler: call `execute_dag()` and collect results
- [x] 2.6 Handler: return `ok({ tasks: { <id>: { status, summary } } })` as tool result
- [x] 2.7 Verify target agents exist and are in conversation (reuse check from `task_dispatch.py`)

## 3. Optional Plan Approval Flow

- [x] 3.1 Add `plan_approval_enabled` runtime flag reader (read from conversation metadata or settings; default `False`)
- [x] 3.2 In `dispatch_plan` handler: when flag is True, register plan via `pending_dispatch_plans.register()` with a revalidation validator
- [x] 3.3 Emit `dispatch.plan.pending` (already done by `register()`); await resolver via `asyncio.Future`
- [x] 3.4 On `approve`: re-validate the (possibly edited) plan, then proceed to `execute_dag()`
- [x] 3.5 On `reject`: return `ok({ status: "rejected" })` tool result
- [x] 3.6 On parent run cancel: `pending_dispatch_plans.cancel()` is called; return `ok({ status: "aborted" })`

## 4. Wire into Coordinated Mode

- [x] 4.1 In `agent_loop.py:_run_coordinated_loop`, add `dispatch_plan` to the tool list alongside `task_dispatch`
- [x] 4.2 Register `dispatch_plan` in `backend/app/tools/registry.py`
- [x] 4.3 Update `_COORDINATED_PROMPT_SUFFIX` in `agent_loop.py` with guidance for `dispatch_plan` vs `task_dispatch` (when to use each)
- [x] 4.4 Verify `dispatch_plan` is NOT injected in solo or subagent mode

## 5. Tests

- [x] 5.1 `backend/tests/test_dag_executor.py` — `validate_dag`: duplicate id, self-dep, missing ref, cycle, valid plan
- [x] 5.2 `backend/tests/test_dag_executor.py` — `topological_waves`: diamond, chain, parallel-only, mixed
- [x] 5.3 `backend/tests/test_dag_executor.py` — `execute_dag` with mocked `spawn_subagent_loop`: all-complete, one-fails-skips-downstream, diamond
- [x] 5.4 `backend/tests/test_dispatch_plan_tool.py` — tool handler: valid plan returns results map, invalid plan returns error, agent-not-in-conversation rejected
- [x] 5.5 `backend/tests/test_dispatch_plan_tool.py` — plan approval flow (mocked `pending_dispatch_plans`): approve executes, reject returns rejected, cancel returns aborted
- [x] 5.6 `backend/tests/test_agent_loop.py` — coordinated mode injects both `task_dispatch` and `dispatch_plan`
- [x] 5.7 Run full test suite: `pytest`
- [x] 5.8 Run lint: `ruff check .`

## 6. Documentation

- [x] 6.1 Update `specs/19-unified-agent-loop.md` — add `dispatch_plan` tool section and DAG execution semantics
- [ ] 6.2 Update `openspec/specs/orchestrator/spec.md` (main spec) with DAG dispatch requirements (sync after archive)
- [ ] 6.3 Update `openspec/specs/tools/spec.md` (main spec) with `dispatch_plan` tool (sync after archive)
- [ ] 6.4 Update `openspec/specs/stream-events/spec.md` (main spec) with DAG dispatch events (sync after archive)
- [x] 6.5 Update `CLAUDE.md` §3.6 if needed to mention dual dispatch mechanism
