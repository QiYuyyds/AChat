# Capability: orchestrator (delta)

## MODIFIED Requirements

### Requirement: coordinated-plan-guidance-prompt

The coordinated mode system prompt SHALL include a section titled "执行计划与调度配合" that instructs the orchestrator:

1. Use `create_plan` to show the user the overall work plan before dispatching
2. Use `dispatch_plan` to schedule sub-tasks, optionally linking each to a plan step via `planStepId`
3. Use `plan_step` to manually mark steps the orchestrator executes itself
4. Plan steps are coarse-grained progress indicators (3-8 steps), while dispatch tasks are fine-grained scheduling units
5. Not every dispatch task needs a corresponding plan step, and not every plan step needs dispatch tasks (some the orchestrator does itself)

#### Scenario: Prompt injected for coordinated mode
- **WHEN** an Agent runs in coordinated mode with plan tools available
- **THEN** the system prompt SHALL include both the existing `dispatch_plan` guidance AND the new `create_plan` / `planStepId` guidance
