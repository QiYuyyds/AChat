# Capability: execution-plan-dispatch-link

## ADDED Requirements

### Requirement: planstepid-field-in-dispatch-plan

The `dispatch_plan` tool SHALL accept an optional `planStepId` field on each task item. When provided, the system SHALL register a mapping from the dispatch task to the specified plan step.

#### Scenario: Dispatch plan with planStepId
- **WHEN** the orchestrator calls `dispatch_plan` with a task item containing `planStepId: "s2"`
- **THEN** the system SHALL register a mapping from that dispatch task ID to `(planId, "s2")` in `plan_dispatch_mapping`

#### Scenario: Dispatch plan without planStepId
- **WHEN** the orchestrator calls `dispatch_plan` with a task item that has no `planStepId`
- **THEN** no mapping SHALL be registered for that dispatch task, and the plan step status SHALL NOT be affected by dispatch events

### Requirement: plan-dispatch-mapping-registry

The system SHALL maintain an in-memory `plan_dispatch_mapping` registry that:
- Stores forward mapping: `(plan_id, step_id)` -> list of dispatch task IDs
- Stores reverse mapping: dispatch task ID -> `(plan_id, step_id)`
- Is populated when `dispatch_plan` executes tasks with `planStepId`
- Is cleaned up when the run ends (alongside `plan_registry`)

#### Scenario: Mapping registration
- **WHEN** `dispatch_plan` handler executes a task with `planStepId="s2"` and the generated task ID is `"t1"`
- **THEN** `plan_dispatch_mapping` SHALL record: forward `(plan_id, "s2") -> ["t1"]` and reverse `"t1" -> (plan_id, "s2")`

#### Scenario: Mapping cleanup
- **WHEN** a run ends and `plan_registry` is cleaned up
- **THEN** `plan_dispatch_mapping` entries for that run's plans SHALL also be removed

### Requirement: dispatch-end-updates-plan-step

When `consume_stream` processes a `dispatch.end` event, it SHALL check `plan_dispatch_mapping` for the task ID. If a mapping exists:

1. Look up the associated `(plan_id, step_id)`
2. Check the status of ALL dispatch tasks mapped to that `(plan_id, step_id)`
3. Update the plan step status:
   - All tasks `complete` -> step `done`
   - Any task `failed` -> step `failed`
   - Any task `skipped` (and no `failed`) -> step `skipped`
   - Mixed `complete` + `skipped` -> step `done`
   - Some tasks still running -> step remains `in_progress`
4. Emit a `plan.step_update` event with the updated steps

#### Scenario: All dispatch tasks complete for a plan step
- **WHEN** the last dispatch task mapped to plan step `"s2"` completes with `status="complete"`
- **THEN** step `"s2"` SHALL be marked `done` and a `plan.step_update` event SHALL be emitted

#### Scenario: One dispatch task fails for a plan step
- **WHEN** a dispatch task mapped to plan step `"s2"` fails with `status="failed"`
- **THEN** step `"s2"` SHALL be marked `failed` regardless of other task statuses, and a `plan.step_update` event SHALL be emitted

#### Scenario: Dispatch task with no mapping
- **WHEN** a `dispatch.end` event is received for a task ID not in `plan_dispatch_mapping`
- **THEN** no plan step update SHALL occur (standard dispatch handling continues)

#### Scenario: Partial completion (some tasks still running)
- **WHEN** a dispatch task mapped to step `"s2"` completes but other tasks for the same step are still running
- **THEN** step `"s2"` SHALL remain `in_progress` (no update emitted yet)
