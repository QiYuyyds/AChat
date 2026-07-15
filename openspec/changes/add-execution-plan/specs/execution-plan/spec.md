# Capability: execution-plan

## ADDED Requirements

### Requirement: execution-plan-overview

Agent SHALL be able to create a structured execution plan with named steps and track their progress in real-time. The plan SHALL be rendered as a checklist card in the chat UI, with step status transitions (pending → in_progress → done/failed/skipped) visible to the user without page refresh.

#### Scenario: Agent creates plan for complex task
- **WHEN** a solo-mode Agent determines a task requires 2+ steps and calls `create_plan` with steps and complexity
- **THEN** the system SHALL create an `execution_plan` MessagePart with all steps in `pending` status, inject it into the current message, and render a checklist card in the UI

#### Scenario: Agent skips plan for simple task
- **WHEN** a solo-mode Agent determines a task is simple (1-2 steps)
- **THEN** the Agent SHALL proceed without calling `create_plan`, using the normal ReAct loop

### Requirement: create-plan-tool

The system SHALL provide a `create_plan` tool that:
- Accepts `steps` (array of `{id, title}`, 2-10 items) and `complexity` (enum: simple/moderate/complex)
- Generates a unique `planId`
- Initializes all steps with `status: "pending"`
- Returns `{ planId, stepCount, steps, complexity }`

#### Scenario: Valid plan creation
- **WHEN** Agent calls `create_plan` with 3 steps and complexity "moderate"
- **THEN** the tool SHALL return a `planId`, register the plan in `plan_registry`, and emit a `plan.created` event

#### Scenario: Invalid step count
- **WHEN** Agent calls `create_plan` with 1 step
- **THEN** the tool SHALL return an error indicating minimum 2 steps are required

#### Scenario: Duplicate step IDs
- **WHEN** Agent calls `create_plan` with duplicate step IDs
- **THEN** the tool SHALL return an error indicating step IDs must be unique

### Requirement: plan-step-tool

The system SHALL provide a `plan_step` tool that:
- Accepts `planId` and `stepId`
- Marks the specified step as `in_progress`
- Automatically marks the previous `in_progress` step (if any) in the same plan as `done`
- Returns `{ planId, currentStep, previousStep, updatedSteps }`

#### Scenario: Sequential step progression
- **WHEN** Agent calls `plan_step(planId='p1', stepId='s2')` while step `s1` is `in_progress`
- **THEN** step `s1` SHALL be marked `done`, step `s2` SHALL be marked `in_progress`, and a `plan.step_update` event SHALL be emitted

#### Scenario: First step progression
- **WHEN** Agent calls `plan_step(planId='p1', stepId='s1')` and no step is currently `in_progress`
- **THEN** step `s1` SHALL be marked `in_progress` and a `plan.step_update` event SHALL be emitted

#### Scenario: Invalid planId
- **WHEN** Agent calls `plan_step` with a `planId` that does not exist in `plan_registry`
- **THEN** the tool SHALL return an error indicating the plan was not found

#### Scenario: Invalid stepId
- **WHEN** Agent calls `plan_step` with a `stepId` that does not exist in the plan
- **THEN** the tool SHALL return an error indicating the step was not found

### Requirement: add-plan-steps-tool

The system SHALL provide an `add_plan_steps` tool that:
- Accepts `planId` and `steps` (array of `{id, title}`, 1-5 items)
- Appends new steps to the end of the plan with `status: "pending"`
- Returns `{ planId, addedCount, totalSteps, updatedSteps }`

#### Scenario: Adding steps to existing plan
- **WHEN** Agent calls `add_plan_steps(planId='p1', steps=[{id:'s4',title:'新步骤'}])`
- **THEN** the new step SHALL be appended to the plan, a `plan.step_update` event SHALL be emitted, and the UI SHALL show the updated checklist

#### Scenario: Step count exceeds maximum
- **WHEN** Adding steps would cause total count to exceed 15
- **THEN** the tool SHALL return an error indicating the maximum step count would be exceeded

### Requirement: plan-registry

The system SHALL maintain an in-memory `plan_registry` that:
- Stores plan state keyed by `planId`
- Is populated when `create_plan` is called
- Is read/updated when `plan_step` or `add_plan_steps` is called
- Is cleaned up when the run ends

#### Scenario: Plan registry lifecycle
- **WHEN** a run starts and Agent calls `create_plan`
- **THEN** the plan SHALL be registered in `plan_registry`
- **WHEN** the run ends (complete/failed/aborted)
- **THEN** the plan SHALL be removed from `plan_registry`

### Requirement: plan-terminal-state-cleanup

When a run ends, the system SHALL finalize all `execution_plan` parts in the current message:
- `in_progress` steps → `done` (if run status is `complete`) or `failed` (if run status is `failed`/`aborted`)
- `pending` steps → `skipped`
- A final `plan.step_update` event SHALL be emitted with the terminal states

#### Scenario: Run completes with remaining pending steps
- **WHEN** a run completes successfully with steps s3 and s4 still `pending`
- **THEN** steps s3 and s4 SHALL be marked `skipped` and a final `plan.step_update` event SHALL be emitted

#### Scenario: Run fails with in-progress step
- **WHEN** a run fails with step s2 `in_progress`
- **THEN** step s2 SHALL be marked `failed` and a `plan.step_update` event SHALL be emitted

### Requirement: plan-tools-injection

The `create_plan`, `plan_step`, and `add_plan_steps` tools SHALL be injected into solo mode Agent tool lists only. Coordinated and subagent modes SHALL NOT have these tools in Phase 1.

#### Scenario: Solo mode tool injection
- **WHEN** an Agent runs in solo mode with `dispatch_depth < MAX_DISPATCH_DEPTH`
- **THEN** the tool list SHALL include `create_plan`, `plan_step`, and `add_plan_steps`

#### Scenario: Coordinated mode no injection
- **WHEN** an Agent runs in coordinated mode
- **THEN** the tool list SHALL NOT include `create_plan`, `plan_step`, or `add_plan_steps`

### Requirement: plan-prompt-guidance

The solo mode system prompt SHALL include guidance instructing the Agent:
- To call `create_plan` for complex tasks (3+ steps)
- NOT to call `create_plan` for simple tasks (1-2 steps)
- To call `plan_step` before starting actual work on each step
- To call `add_plan_steps` when discovering additional work is needed
- That `plan_step` and actual tool calls can be issued as parallel tool calls in the same turn
