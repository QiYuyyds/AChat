# Stream Events — Delta: DAG Dispatch Plan

## ADDED Requirements

### Requirement: DAG dispatch SHALL emit dispatch plan and per-node events

The `dispatch_plan` tool handler MUST emit `dispatch.plan` before executing the DAG (carrying the validated plan items), and `dispatch.start` / `dispatch.end` per node as sub-agents begin and finish. These events reuse the existing event types and keep the UI live during multi-wave execution.

#### Scenario: DAG execution starts

- **WHEN** the `dispatch_plan` handler finishes validation (and optional approval) and begins DAG execution
- **THEN** it emits `dispatch.plan` with the validated plan items and the orchestrator run id
- **AND** frontend reducers can render the plan structure

#### Scenario: DAG node starts

- **WHEN** a DAG node's sub-agent run begins (acquires a wave slot)
- **THEN** the handler emits `dispatch.start` with `parentRunId` (orchestrator run), `childRunId` (sub-agent run), `taskId`, and `agentId`

#### Scenario: DAG node ends

- **WHEN** a DAG node's sub-agent run finishes (complete, failed, or aborted)
- **THEN** the handler emits `dispatch.end` with `parentRunId`, `childRunId`, `taskId`, and `status`

#### Scenario: Skipped node emits dispatch.end without dispatch.start

- **WHEN** a task is skipped because an upstream dependency failed
- **THEN** the handler emits `dispatch.end` with `taskId`, `status: "skipped"`, and no `childRunId`
- **AND** no `dispatch.start` is emitted for that task

### Requirement: Plan approval SHALL reuse dispatch.plan.pending and dispatch.plan.resolved

When plan approval is enabled, the `dispatch_plan` handler MUST emit `dispatch.plan.pending` (carrying the `PendingDispatchPlan`) and, upon user decision, `dispatch.plan.resolved` (carrying approval outcome). These events and the `pending_dispatch_plans` store already exist; the handler reuses them without modification.

#### Scenario: Plan pending approval

- **WHEN** the `dispatch_plan` handler registers a pending plan via `pending_dispatch_plans.register()`
- **THEN** `dispatch.plan.pending` is published with the pending plan
- **AND** frontend reducers show the plan review card

#### Scenario: Plan resolved

- **WHEN** the user approves, rejects, or the run is cancelled
- **THEN** `dispatch.plan.resolved` is published with `pendingId`, `runId`, `approved`, and optional `revising`
- **AND** frontend reducers clear the plan review card
