# Orchestrator — Delta: DAG Dispatch Plan

## ADDED Requirements

### Requirement: Orchestrator SHALL have a dispatch_plan tool for structured DAG dispatch

In coordinated mode, the orchestrator's tool list MUST include a `dispatch_plan` tool alongside the existing `task_dispatch` tool. The `dispatch_plan` tool accepts a declarative list of tasks with `dependsOn` dependencies, validates the DAG, and executes it via wave-based topological scheduling. The orchestrator LLM chooses `dispatch_plan` for structured multi-task work with known dependencies and `task_dispatch` for single immediate dispatches.

#### Scenario: Orchestrator dispatches a structured multi-task plan

- **WHEN** the orchestrator calls `dispatch_plan({ tasks: [{ id: "t1", agentId, task, dependsOn: [] }, { id: "t2", agentId, task, dependsOn: ["t1"] }] })`
- **THEN** the handler validates the DAG (no cycles, all `dependsOn` references exist)
- **AND** executes wave 1 (t1) via `spawn_subagent_loop`
- **AND** after t1 completes, executes wave 2 (t2)
- **AND** returns `{ tasks: { t1: {status, summary}, t2: {status, summary} } }` as the tool result

#### Scenario: Orchestrator uses task_dispatch for a single immediate task

- **WHEN** the orchestrator calls `task_dispatch({ agentId, taskDescription })`
- **THEN** the existing single-dispatch behavior runs unchanged
- **AND** `dispatch_plan` is not involved

#### Scenario: Both tools are available in coordinated mode

- **WHEN** the orchestrator runs in `mode='coordinated'`
- **THEN** the tool list includes both `task_dispatch` and `dispatch_plan`
- **AND** the coordinated system prompt explains when to use each

### Requirement: DAG execution SHALL schedule tasks in dependency waves

The `dispatch_plan` handler MUST execute tasks in topological order. Tasks with no unresolved dependencies (the "ready" set) run in parallel within a wave via `asyncio.gather`. After a wave completes, the next wave of newly-ready tasks starts. A task whose any upstream dependency did not complete successfully MUST be marked `skipped` and not executed.

#### Scenario: Diamond dependency executes in three waves

- **WHEN** the plan is `t1 → (t2, t3) → t4` where t2 and t3 depend on t1, and t4 depends on t2 and t3
- **THEN** wave 1 executes t1 alone
- **AND** wave 2 executes t2 and t3 in parallel
- **AND** wave 3 executes t4 after both t2 and t3 complete

#### Scenario: Upstream failure skips downstream tasks

- **WHEN** task t1 fails and task t2 depends on t1
- **THEN** t2 is marked `skipped` with reason "Upstream task t1 did not complete"
- **AND** t2's sub-agent is not spawned
- **AND** the result map includes t2 with `status: "skipped"`

#### Scenario: Cycle in dependencies is rejected

- **WHEN** the plan contains a cycle (t1 depends on t2, t2 depends on t1)
- **THEN** the handler returns an error tool result naming the cycle
- **AND** no sub-agents are spawned

#### Scenario: Missing dependsOn reference is rejected

- **WHEN** a task declares `dependsOn: ["t99"]` but no task with id "t99" exists in the plan
- **THEN** the handler returns an error tool result identifying the missing reference
- **AND** no sub-agents are spawned

### Requirement: DAG node execution SHALL reuse spawn_subagent_loop

Each DAG node MUST be executed by calling the existing `spawn_subagent_loop` with the node's `agentId`, `task` description, conversation context, and parent run/cancel propagation. The node's result (`LoopRunResult`) maps directly to the result map entry.

#### Scenario: DAG node completes successfully

- **WHEN** `spawn_subagent_loop` for a node returns `status="complete"`
- **THEN** the result map entry for that node has `status: "complete"` and `summary` from the sub-agent's final text

#### Scenario: DAG node fails

- **WHEN** `spawn_subagent_loop` for a node returns `status="failed"`
- **THEN** the result map entry has `status: "failed"` and `summary` with the error text
- **AND** downstream tasks are skipped

### Requirement: dispatch_plan SHALL support optional plan approval

When plan approval is enabled (conversation-level runtime flag, default off), the `dispatch_plan` handler MUST emit `dispatch.plan.pending` and await user approval before executing the DAG. Approval re-validates the plan via the existing `pending_dispatch_plans` store. Rejection cancels the dispatch and returns a `rejected` status to the orchestrator. When approval is disabled (default), the handler executes the DAG immediately after validation.

#### Scenario: Plan approval disabled (default)

- **WHEN** `dispatch_plan` is called and plan approval is not enabled
- **THEN** the handler validates the DAG and executes it immediately
- **AND** no `dispatch.plan.pending` event is emitted

#### Scenario: Plan approval enabled — user approves

- **WHEN** `dispatch_plan` is called and plan approval is enabled
- **THEN** the handler emits `dispatch.plan.pending` with the plan
- **AND** waits for the user's decision
- **WHEN** the user approves
- **THEN** the handler re-validates the (possibly edited) plan
- **AND** executes the DAG

#### Scenario: Plan approval enabled — user rejects

- **WHEN** the user rejects the pending plan
- **THEN** the handler returns `{ status: "rejected" }` to the orchestrator
- **AND** no sub-agents are spawned

#### Scenario: Plan approval enabled — run cancelled while waiting

- **WHEN** the parent run is cancelled while waiting for plan approval
- **THEN** the pending plan is cancelled via `pending_dispatch_plans.cancel()`
- **AND** the handler returns `{ status: "aborted" }`

### Requirement: dispatch_plan results SHALL be returned as a flat map

The `dispatch_plan` tool result MUST be a flat map of task id to `{ status, summary }`. The orchestrator uses this to produce its final `end_turn` summary. `skipped` tasks MUST be included so the orchestrator can explain gaps to the user.

#### Scenario: All tasks complete

- **WHEN** all DAG nodes complete successfully
- **THEN** the tool result contains every task id with `status: "complete"` and its summary

#### Scenario: Mixed outcomes

- **WHEN** t1 completes, t2 fails, t3 is skipped (depends on t2)
- **THEN** the tool result includes all three entries with their respective statuses
- **AND** the orchestrator can describe the partial failure in its summary
