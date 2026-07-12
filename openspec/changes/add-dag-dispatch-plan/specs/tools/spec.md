# Tools — Delta: DAG Dispatch Plan

## ADDED Requirements

### Requirement: dispatch_plan tool SHALL be registered for coordinated mode

AChat MUST provide a `dispatch_plan` tool that accepts a declarative task list with `dependsOn` dependencies and executes them as a DAG. The tool MUST be registered in the `tool_registry` and injected into the orchestrator's tool list only when `mode='coordinated'`. The tool MUST NOT be available in solo or subagent mode.

#### Scenario: dispatch_plan available in coordinated mode

- **WHEN** the orchestrator runs in `mode='coordinated'`
- **THEN** the tool list includes `dispatch_plan`
- **AND** the tool's parameters schema accepts `tasks` (array of `{ id, agentId, task, dependsOn? }`)

#### Scenario: dispatch_plan not available in solo mode

- **WHEN** an agent runs in `mode='solo'` or `mode='subagent'`
- **THEN** the tool list does NOT include `dispatch_plan`

### Requirement: dispatch_plan parameters SHALL follow DispatchPlanItem structure

The `dispatch_plan` tool MUST accept a `tasks` array where each item has: `id` (string, required), `agentId` (string, required), `task` (string, required), `dependsOn` (string array, optional). The tool MUST reject plans with duplicate `id`s, self-dependencies, missing `dependsOn` references, or cycles.

#### Scenario: Valid plan is accepted

- **WHEN** `dispatch_plan` receives `{ tasks: [{ id: "t1", agentId: "a1", task: "do X" }, { id: "t2", agentId: "a2", task: "do Y", dependsOn: ["t1"] }] }`
- **THEN** the handler accepts the plan and proceeds to execution (or approval if enabled)

#### Scenario: Duplicate task ids are rejected

- **WHEN** two tasks have `id: "t1"`
- **THEN** the handler returns an error tool result identifying the duplicate

#### Scenario: Self-dependency is rejected

- **WHEN** a task declares `dependsOn: ["t1"]` where its own id is "t1"
- **THEN** the handler returns an error tool result

#### Scenario: Agent not in conversation is rejected

- **WHEN** a task's `agentId` does not belong to the current conversation
- **THEN** the handler returns an error tool result naming the invalid agent
