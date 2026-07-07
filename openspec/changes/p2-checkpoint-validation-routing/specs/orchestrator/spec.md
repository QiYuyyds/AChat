# Spec Delta: Orchestrator

## ADDED Requirements

### Requirement: Orchestrator SHALL verify task results before aggregation

After DAG execution completes and before the aggregate stage, the orchestrator MUST run a Verify stage that performs deterministic validation of task results. Verification MUST NOT call an LLM; it MUST use rule-based checks on artifacts, evidence, and expected outputs.

#### Scenario: Code task verification checks project artifact and evidence

- **WHEN** a code implementation task reports `complete`
- **THEN** the Verify stage checks that a `project` artifact exists
- **AND** checks that `required_evidence` commands (build, test, lint, typecheck) have successful records
- **AND** marks the task as `verification_passed` or `verification_failed`.

#### Scenario: Document task verification checks expected outputs

- **WHEN** a document task reports `complete`
- **THEN** the Verify stage checks that the artifact content covers all `expected_outputs` declared in the task
- **AND** marks the task as `verification_passed` or `verification_failed`.

#### Scenario: Review task verification checks upstream references

- **WHEN** a review task reports `complete`
- **THEN** the Verify stage checks that the review conclusion references artifacts from `dependsOn` tasks
- **AND** marks the task as `verification_passed` or `verification_failed`.

#### Scenario: Unknown task kind passes verification

- **WHEN** a task with an unknown or missing `taskKind` reports `complete`
- **THEN** the Verify stage skips validation
- **AND** marks the task as `verification_passed` (default).

### Requirement: Verification failure SHALL trigger replan

When the Verify stage marks a task as `verification_failed`, the orchestrator MUST include the failure reason in the replan context and trigger a remediation round (up to `MAX_DISPATCH_ROUNDS`).

#### Scenario: Verification failure triggers replan

- **WHEN** one or more tasks are marked `verification_failed`
- **THEN** `should_replan` returns True
- **AND** the replan context includes the verification failure reasons
- **AND** the orchestrator produces a remediation plan.

#### Scenario: Verification failure after max rounds

- **WHEN** tasks are marked `verification_failed` after `MAX_DISPATCH_ROUNDS` rounds
- **THEN** the orchestrator proceeds to aggregation
- **AND** the aggregate summary reports the verification failures.

### Requirement: DAG execution SHALL consider agent load when dispatching

When multiple tasks are ready in the same DAG wave, the orchestrator MUST consider the current load of each target agent before dispatching. The orchestrator SHALL limit concurrent tasks per agent to `MAX_CONCURRENT_TASKS_PER_AGENT` (default 2).

#### Scenario: Load-aware dispatch across agents

- **WHEN** a wave has 3 tasks assignable to agents A (current load 2) and B (current load 0)
- **THEN** tasks are dispatched preferentially to agent B until its load reaches the limit
- **AND** agent A does not receive more tasks until its load decreases.

#### Scenario: Single agent in wave ignores load limit

- **WHEN** all tasks in a wave must be assigned to the same agent (no alternative agent available)
- **THEN** the load limit is relaxed
- **AND** all tasks are dispatched to that agent.

#### Scenario: Load tracker degrades gracefully on restart

- **WHEN** the process restarts and `AgentLoadTracker` has no historical data
- **THEN** dispatch falls back to static priority sorting (P0 O10 behavior)
- **AND** no errors are raised.
