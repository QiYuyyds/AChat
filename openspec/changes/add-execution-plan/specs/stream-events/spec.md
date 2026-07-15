# Capability: stream-events (delta)

## ADDED Requirements

### Requirement: plan-created-event

The `StreamEvent` union type SHALL include a new event:

```typescript
| {
    type: 'plan.created'
    conversationId: string
    timestamp: number
    planId: string
    steps: PlanStep[]
    complexity: string
  }
```

This event is emitted when an Agent calls `create_plan` successfully. It is **pass-through** (not persisted to DB), used by `consume_stream` to inject the `execution_plan` part and by the frontend for real-time rendering.

#### Scenario: Plan created event flow
- **WHEN** `create_plan` tool returns successfully
- **THEN** `_execute_tool_call_to_result` SHALL append a `PlanCreatedEvent` to the events list, and `consume_stream` SHALL process it to inject the part

### Requirement: plan-step-update-event

The `StreamEvent` union type SHALL include a new event:

```typescript
| {
    type: 'plan.step_update'
    conversationId: string
    timestamp: number
    planId: string
    steps: PlanStep[]
  }
```

This event is emitted when plan step status changes (via `plan_step` or `add_plan_steps` tools, or run-end cleanup). It carries the **full updated steps array** (not a delta).

#### Scenario: Step update event flow
- **WHEN** `plan_step` or `add_plan_steps` tool returns successfully
- **THEN** `_execute_tool_call_to_result` SHALL append a `PlanStepUpdateEvent` to the events list

#### Scenario: Frontend reducer handles step update
- **WHEN** the frontend SSE reducer receives a `plan.step_update` event
- **THEN** it SHALL find the `execution_plan` part in the current message by `planId` and replace its `steps` array with the event's `steps` array

### Requirement: plan-step-update-persist

The `plan.step_update` event SHALL update the `parts_buffer` in `consume_stream` to keep the persisted `execution_plan` part in sync with the latest step states.

#### Scenario: Parts buffer updated on step change
- **WHEN** `consume_stream` receives a `plan.step_update` event
- **THEN** it SHALL find the `execution_plan` part in `parts_buffer` by `planId` and replace its `steps` with the event's `steps`
