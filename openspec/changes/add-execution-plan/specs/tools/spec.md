# Capability: tools (delta)

## ADDED Requirements

### Requirement: create-plan-tool-definition

A new tool `create_plan` SHALL be registered in `tool_registry` with:
- **Parameters**: `{ steps: [{id, title}], complexity: enum }`
  - `steps`: required, array of 2-10 items, each with required `id` (string) and `title` (string)
  - `complexity`: required, one of `"simple"`, `"moderate"`, `"complex"`
- **Handler**: validates input, generates `planId`, registers plan in `plan_registry`, returns `ok({ planId, stepCount, steps, complexity })`
- **Error cases**: steps count < 2 or > 10, duplicate step IDs, missing required fields

#### Scenario: Tool registered in registry
- **WHEN** the application starts
- **THEN** `create_plan` SHALL be available in `tool_registry` and listed in tool discovery APIs

### Requirement: plan-step-tool-definition

A new tool `plan_step` SHALL be registered in `tool_registry` with:
- **Parameters**: `{ planId: string, stepId: string }`
- **Handler**: reads plan from `plan_registry`, auto-marks previous in_progress step as done, marks specified step as in_progress, returns `ok({ planId, currentStep, previousStep, updatedSteps })`
- **Error cases**: planId not found, stepId not found in plan

#### Scenario: Tool registered in registry
- **WHEN** the application starts
- **THEN** `plan_step` SHALL be available in `tool_registry`

### Requirement: add-plan-steps-tool-definition

A new tool `add_plan_steps` SHALL be registered in `tool_registry` with:
- **Parameters**: `{ planId: string, steps: [{id, title}] }`
  - `steps`: required, array of 1-5 items
- **Handler**: reads plan from `plan_registry`, appends new steps with `pending` status, returns `ok({ planId, addedCount, totalSteps, updatedSteps })`
- **Error cases**: planId not found, total steps would exceed 15, duplicate step IDs with existing steps

#### Scenario: Tool registered in registry
- **WHEN** the application starts
- **THEN** `add_plan_steps` SHALL be available in `tool_registry`

### Requirement: plan-tool-event-generation

When `create_plan`, `plan_step`, or `add_plan_steps` tool calls succeed, `_execute_tool_call_to_result` SHALL detect the tool name and append the corresponding event (`PlanCreatedEvent` or `PlanStepUpdateEvent`) to the events list, symmetric to how `write_artifact` generates `ArtifactCreateEvent`.

#### Scenario: create_plan generates PlanCreatedEvent
- **WHEN** `_execute_tool_call_to_result` processes a successful `create_plan` tool result
- **THEN** it SHALL append a `PlanCreatedEvent` with planId, steps, and complexity to the events list

#### Scenario: plan_step generates PlanStepUpdateEvent
- **WHEN** `_execute_tool_call_to_result` processes a successful `plan_step` tool result
- **THEN** it SHALL append a `PlanStepUpdateEvent` with planId and updated steps to the events list

#### Scenario: add_plan_steps generates PlanStepUpdateEvent
- **WHEN** `_execute_tool_call_to_result` processes a successful `add_plan_steps` tool result
- **THEN** it SHALL append a `PlanStepUpdateEvent` with planId and updated steps to the events list
