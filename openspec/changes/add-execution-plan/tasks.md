## 1. Backend — Data Types & Registry

- [x] 1.1 Create `backend/app/schemas/plan.py` with `PlanStep` and `PlanState` Pydantic models
- [x] 1.2 Create `backend/app/services/plan_registry.py` with in-memory plan registry (register / get / update / cleanup)

## 2. Backend — Event Schemas

- [x] 2.1 Add `PlanCreatedEvent` and `PlanStepUpdateEvent` to `backend/app/schemas/events.py`
- [x] 2.2 Add both event types to the `StreamEvent` discriminated union

## 3. Backend — Tool Definitions

- [x] 3.1 Create `backend/app/tools/execution_plan.py` with `create_plan_tool` (handler: validate steps, generate planId, register in plan_registry, return ok)
- [x] 3.2 Add `plan_step_tool` to `backend/app/tools/execution_plan.py` (handler: auto-mark previous in_progress as done, mark target as in_progress, return updated steps)
- [x] 3.3 Add `add_plan_steps_tool` to `backend/app/tools/execution_plan.py` (handler: validate no duplicate IDs, append to plan, return updated steps)
- [x] 3.4 Register all three tools in `backend/app/tools/registry.py`

## 4. Backend — Agent Runner Integration

- [x] 4.1 In `_execute_tool_call_to_result`, add detection for `create_plan` success → append `PlanCreatedEvent`
- [x] 4.2 In `_execute_tool_call_to_result`, add detection for `plan_step` and `add_plan_steps` success → append `PlanStepUpdateEvent`
- [x] 4.3 In `consume_stream`, handle `plan.created` event → push `execution_plan` part to `parts_buffer` + emit `part.start` (symmetric to `artifact.create` → `artifact_ref`)
- [x] 4.4 In `consume_stream`, handle `plan.step_update` event → update `execution_plan` part steps in `parts_buffer` + SSE publish
- [x] 4.5 Add `plan.created` and `plan.step_update` to `_VISIBLE_EVENT_TYPES` set
- [x] 4.6 In `consume_stream`, add run-end cleanup: finalize all `execution_plan` parts (in_progress → done/failed, pending → skipped) and emit final `PlanStepUpdateEvent`

## 5. Backend — Agent Loop Integration

- [x] 5.1 In `_run_solo_loop`, inject `create_plan`, `plan_step`, `add_plan_steps` into tool_names
- [x] 5.2 Add `_PLAN_SUFFIX` prompt guidance to `build_solo_system_prompt` (when plan tools are injected)
- [x] 5.3 In `orchestrator_prompts.py` `extract_text_from_parts`, add `execution_plan` case: compact one-line summary with status emojis

## 6. Frontend — Type Definitions

- [x] 6.1 Add `PlanStepStatus`, `PlanStep` types and `execution_plan` branch to `MessagePart` union in `src/shared/types.ts`
- [x] 6.2 Add `plan.created` and `plan.step_update` branches to `StreamEvent` union in `src/shared/types.ts`

## 7. Frontend — SSE Reducer

- [x] 7.1 Add `plan.step_update` case to SSE reducer in `src/stores/app-store.ts`: find `execution_plan` part by planId, replace steps array

## 8. Frontend — UI Component

- [x] 8.1 Create `ExecutionPlanPart` component in `src/components/message-parts.tsx` with checklist card rendering (status icons, step titles, visual states for pending/in_progress/done/failed/skipped)
- [x] 8.2 Add `execution_plan` case to `PartRenderer` switch in `src/components/message-parts.tsx`

## 9. Spec Updates

- [x] 9.1 Update `specs/03-message-parts.md`: add `execution_plan` part type section (type definition, render contract, history serialization)
- [x] 9.2 Update `specs/02-stream-events.md`: add `plan.created` and `plan.step_update` event sections
