# Orchestrator (Modified)

## Delta spec for existing `openspec/specs/orchestrator/spec.md`

This spec MODIFIES the orchestrator capability for the unified agent loop paradigm.

## Requirements

### Requirement: Orchestrator SHALL run as a normal agent with TaskDispatch tool

The orchestrator SHALL be a normal agent that runs through `run_agent_loop` with `mode='coordinated'`. Its tool list includes all standard tools PLUS `TaskDispatch`.

#### Scenario: Group conversation starts
- **WHEN** a user sends a message in a conversation with `dispatch_mode = 'orchestrated'`
- **THEN** the orchestrator agent enters a coordinated agent loop
- **AND** the loop continues until `end_turn`.

**REMOVED Requirements from `openspec/specs/orchestrator/spec.md`:**

- ~~Child run completes without a task report~~ — **Reason**: `report_task_result` tool is fully removed. Migration: standalone `end_turn` is the only completion signal.
- ~~Child task reports failed acceptance~~ — **Reason**: No structured acceptance report exists. Migration: worker's text output is the report; if orchestrator disagrees, it re-dispatches in a new task.
- ~~Code task lacks runnable verification~~ — **Reason**: Verification gate is removed. Migration: worker is encouraged via system prompt to self-verify, but no hard gate.
- ~~Code task lacks project output~~ — **Reason**: Project output binding is removed as a hard gate. Migration: workspace file list is the UI source of truth.

### Requirement: Orchestrator MAY dispatch sub-agents via TaskDispatch

The orchestrator SHALL be allowed (but not required) to use `TaskDispatch`. It MAY also perform work itself using standard tools like `fs_write` and `bash`.

#### Scenario: Orchestrator handles a simple task alone
- **WHEN** the orchestrator receives a task it can handle itself
- **THEN** it MAY use `fs_write`, `bash`, etc. directly without dispatching.

#### Scenario: Orchestrator dispatches a specialized task
- **WHEN** the orchestrator needs a capability that another agent has
- **THEN** it calls `TaskDispatch(agent_id=..., task_description=...)`.

### Requirement: Orchestrator loop SHALL stop at `end_turn`

The orchestrator's coordinated loop SHALL stop when:
1. The orchestrator model emits `end_turn` (normal completion), OR
2. The cancel event is set (user abort), OR
3. Wall-clock timeout is hit (safety bound).

There SHALL NOT be:
- A `report_task_result` gate
- A 4-attempt retry harness
- A separate LLM judge evaluation

#### Scenario: Sub-agent finishes and returns
- **WHEN** a `TaskDispatch` call returns
- **THEN** the orchestrator's loop continues; the model sees the sub-agent text return.
#### Scenario: Sub-agent produces incorrect work
- **WHEN** a sub-agent's output is unsatisfactory
- **THEN** the orchestrator MAY re-dispatch with a corrective task description in a new loop iteration.

### Requirement: Task plan replaces DAG execution

The current DAG-based multi-wave dispatch is replaced by sequential `TaskDispatch` calls within the orchestrator loop. The orchestrator decides the order (parallelism within a single tool_calls batch is allowed by the underlying LLM API).

#### Scenario: Multiple independent tasks
- **WHEN** the orchestrator needs to run independent tasks in parallel
- **THEN** it may emit multiple `TaskDispatch` tool_calls in a single turn
- **AND** the runtime executes them concurrently
- **AND** all results are fed back together.

#### Scenario: Sequential dependent tasks
- **WHEN** task B depends on task A's output
- **THEN** the orchestrator emits TaskDispatch for A first, waits for result, then emits TaskDispatch for B.
