## 1. Backend — Plan Tools Injection in Coordinated Mode

- [x] 1.1 In `_run_coordinated_loop`, inject `create_plan`, `plan_step`, `add_plan_steps` into tool_names (alongside existing `task_dispatch` + `dispatch_plan`)
- [x] 1.2 In `_run_coordinated_loop`, pass `plan_enabled=True` to `build_coordinated_system_prompt`

## 2. Backend — Plan-Dispatch Mapping Registry

- [x] 2.1 Create `backend/app/services/plan_dispatch_mapping.py` with in-memory mapping registry (forward + reverse index, register / lookup / cleanup methods)
- [x] 2.2 Add cleanup of `plan_dispatch_mapping` alongside `plan_registry` cleanup in run-end handler

## 3. Backend — dispatch_plan Tool Enhancement

- [x] 3.1 Add optional `planStepId` field to `DispatchPlanItem` schema in `backend/app/schemas/dispatch.py`
- [x] 3.2 Add `planStepId` to `dispatch_plan` tool parameters (optional string in task items schema)
- [x] 3.3 In `dispatch_plan` handler, after resolving dispatch tasks, register mappings in `plan_dispatch_mapping` for tasks that have `planStepId`
- [x] 3.4 Emit `plan.step_update` events when dispatch tasks with `planStepId` start execution (mark corresponding plan step as `in_progress`)

## 4. Backend — dispatch.end → Plan Step Status Update

- [x] 4.1 In `consume_stream`, add `dispatch.end` handler that checks `plan_dispatch_mapping` for the task ID
- [x] 4.2 Implement status aggregation logic: check all dispatch tasks mapped to the same `(plan_id, step_id)`, determine combined status
- [x] 4.3 Update plan step status in `plan_registry` and `parts_buffer`, emit `plan.step_update` event

## 5. Backend — Coordinated Mode Prompt

- [x] 5.1 Add `_COORDINATED_PLAN_SUFFIX` prompt section to `agent_loop.py` explaining how to combine `create_plan` with `dispatch_plan` (planStepId usage, manual plan_step for self-executed steps)
- [x] 5.2 Modify `build_coordinated_system_prompt` to accept `plan_enabled` parameter and append plan guidance when True

## 6. Spec Updates

- [x] 6.1 Update `specs/05-adapter-interface.md` or `specs/19-unified-agent-loop.md`: note that coordinated mode now injects plan tools
- [x] 6.2 Update `specs/02-stream-events.md`: document `planStepId` field in dispatch events and plan step auto-update behavior
