# Capability: execution-plan (delta — coordinated mode)

## MODIFIED Requirements

### Requirement: plan-tools-injection

The `create_plan`, `plan_step`, and `add_plan_steps` tools SHALL be injected into both solo and coordinated mode Agent tool lists. Subagent mode SHALL NOT have these tools.

#### Scenario: Solo mode tool injection
- **WHEN** an Agent runs in solo mode
- **THEN** the tool list SHALL include `create_plan`, `plan_step`, and `add_plan_steps`

#### Scenario: Coordinated mode tool injection
- **WHEN** an Agent runs in coordinated mode (orchestrator)
- **THEN** the tool list SHALL include `create_plan`, `plan_step`, and `add_plan_steps` in addition to `task_dispatch` and `dispatch_plan`

#### Scenario: Subagent mode no injection
- **WHEN** an Agent runs in subagent mode
- **THEN** the tool list SHALL NOT include `create_plan`, `plan_step`, or `add_plan_steps`

### Requirement: plan-prompt-guidance

The coordinated mode system prompt SHALL include guidance instructing the orchestrator:
- To call `create_plan` first to show the overall work plan to the user
- To specify `planStepId` in `dispatch_plan` tasks to link dispatch tasks to plan steps
- To call `plan_step` manually for steps the orchestrator executes itself
- That plan steps are coarse-grained progress indicators, while dispatch tasks are fine-grained scheduling units
- That not every dispatch task needs a plan step, and not every plan step needs dispatch tasks

#### Scenario: Coordinated prompt includes plan guidance
- **WHEN** an Agent runs in coordinated mode with plan tools injected
- **THEN** the system prompt SHALL include a section explaining how to combine `create_plan` with `dispatch_plan`
