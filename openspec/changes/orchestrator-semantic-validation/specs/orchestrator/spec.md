## MODIFIED Requirements

### Requirement: Orchestrator SHALL plan before dispatch

The orchestration flow MUST produce a compiled and validated task plan before launching child agent runs. The Orchestrator SHALL include explicit constraints in each task description (e.g., required citations, code comments, output format) to guide sub-agent behavior without overriding its autonomy.

#### Scenario: Plan tool is called
- **WHEN** the orchestrator calls `plan_tasks`
- **THEN** AgentRunner parses, compiles, and validates task ids, agent ids, dependencies, and acyclicity.

#### Scenario: Plan text implies missing dependencies
- **WHEN** task text references earlier task outputs but `dependsOn` omits them
- **THEN** AgentRunner adds high-confidence inferred dependencies before dispatch
- **AND** publishes and executes the compiled plan.

#### Scenario: Local workspace code project is requested
- **WHEN** the conversation workspace is local
- **AND** the user asks to create, modify, initialize, debug, or build project source files
- **THEN** the plan prompt tells the orchestrator to prefer agents with file/command tools
- **AND** the plan should use `acceptanceCriteria` for local file and command outcomes instead of `expectedOutputs`.

#### Scenario: Code task contract is normalized
- **WHEN** the compiled plan contains a code implementation task
- **THEN** AgentRunner ensures the task has a required `project` expected output
- **AND** does NOT auto-append hardcoded verification criteria or evidence requirements
- **AND** the Orchestrator LLM decides whether build verification is needed based on workspace context and writes it into the task description.

#### Scenario: Task description includes explicit constraints
- **WHEN** the Orchestrator compiles a plan
- **THEN** each task description SHALL include any constraints the sub-agent must follow (e.g., cite sources, add comments, format output)
- **AND** the constraints are advisory — the sub-agent retains autonomous decision-making
- **AND** the Orchestrator does not force-rewrite the sub-agent's internal logic.

### Requirement: Child tasks SHALL respect dependency order and semantic reports

AgentRunner MUST execute dispatch tasks as a DAG and skip dependent tasks when prerequisites fail or required inputs cannot be resolved. Task completion validation SHALL be performed by the Orchestrator LLM using advisory evidence as context, not by deterministic string-matching gates.

#### Scenario: Upstream task fails
- **WHEN** a task dependency ends with status `failed`
- **THEN** dependent tasks are skipped
- **AND** dispatch events include the blocking reason.

#### Scenario: Downstream task is missing a required input artifact
- **WHEN** a downstream task declares a required input from an upstream output key
- **AND** the upstream result has no artifact bound to that key
- **THEN** the downstream task is skipped before launch
- **AND** dispatch events include the missing input reason.

#### Scenario: Child run completes without a task report
- **WHEN** a child run ends with status `complete`
- **AND** it did not call `report_task_result`
- **THEN** the dispatch task is treated as `failed`
- **AND** dependent tasks are skipped.

#### Scenario: Child task self-reports non-completion
- **WHEN** a child run calls `report_task_result`
- **AND** the report status is not `complete` or an acceptance result is `passed=false`
- **THEN** the dispatch task is treated as `failed`
- **AND** dependent tasks are skipped.

#### Scenario: Child task completion validated by Orchestrator LLM
- **WHEN** a child task reports `status="complete"` with all acceptance results `passed=true`
- **AND** the advisory evidence collector reports issues (e.g., failed commands, missing verification, criteria not matched)
- **THEN** AgentRunner calls the Orchestrator LLM with the task contract, agent report, and advisory issues
- **AND** if the LLM judges the task as passing, the dispatch task is treated as `complete`
- **AND** if the LLM judges the task as failing, the harness loop retries with the LLM's feedback as the continuation prompt.

#### Scenario: Advisory issues do not block without LLM judgment
- **WHEN** the advisory evidence collector finds issues (e.g., no whitelisted verification command, criteria not exact-matched)
- **AND** the Orchestrator LLM has not yet been called
- **THEN** the task is NOT automatically failed
- **AND** the advisory issues are passed to the LLM as context for semantic judgment.

#### Scenario: Code task lacks project output
- **WHEN** a code implementation child task reports `complete`
- **AND** no required `project` output can be created and bound from workspace file writes
- **THEN** the dispatch task is treated as `failed`
- **AND** the existing retry or replan flow may remediate it.

#### Scenario: Replan references a previous-round task
- **WHEN** a remediation plan depends on a task id from an earlier dispatch round
- **THEN** AgentRunner treats that previous task as a resolved external dependency
- **AND** validates and executes the remediation plan without requiring the previous task to be repeated in the new plan.

## ADDED Requirements

### Requirement: Review agent feedback SHALL trigger replan

When a review agent (a DAG task that depends on an implementation task) reports `status="failed"` with feedback, the Orchestrator SHALL trigger a replan round that retries the implementation task with the review feedback incorporated into the continuation prompt.

#### Scenario: Review agent finds issues and triggers replan
- **WHEN** a review task in the DAG reports `status="failed"` with feedback describing issues
- **AND** the implementation task it depends on has `status="complete"`
- **AND** the current dispatch round has not exhausted `MAX_DISPATCH_ROUNDS`
- **THEN** AgentRunner triggers a replan
- **AND** the replan prompt includes the review feedback
- **AND** the Orchestrator LLM creates a new plan that retries the implementation task with the feedback.

#### Scenario: Review agent passes and no replan needed
- **WHEN** a review task reports `status="complete"`
- **THEN** no replan is triggered for the implementation task
- **AND** the DAG continues normally.

#### Scenario: Replan rounds exhausted with review failure
- **WHEN** a review task reports `status="failed"`
- **AND** `MAX_DISPATCH_ROUNDS` has been reached
- **THEN** no further replan is triggered
- **AND** the aggregate stage runs with the review failure included in the results
- **AND** the Orchestrator informs the user that review did not pass after maximum retries.

### Requirement: Advisory evidence SHALL be collected but not block

The system SHALL collect objective evidence (bash commands, file writes, criteria coverage) as advisory issues for the Orchestrator LLM, but SHALL NOT use these issues to automatically fail a task without LLM judgment.

#### Scenario: Failed bash command collected as advisory
- **WHEN** a non-prepare bash command fails with non-zero exit code or timeout
- **AND** no later command with the same prefix succeeds
- **THEN** the failed command is added to `advisory_issues` as a string
- **AND** the task is NOT automatically failed.

#### Scenario: Missing verification command collected as advisory
- **WHEN** a code task reports `complete`
- **AND** no whitelisted build/test/lint command succeeded in the evidence
- **THEN** an advisory issue is added: "no whitelisted verification command found"
- **AND** the task is NOT automatically failed
- **AND** the Orchestrator LLM decides whether this matters.

#### Scenario: Criteria not exact-matched collected as advisory
- **WHEN** the task has declared acceptance criteria
- **AND** the agent's report does not contain exact string matches for all criteria
- **THEN** an advisory issue is added for each unmatched criterion
- **AND** the task is NOT automatically failed
- **AND** the Orchestrator LLM decides whether the report semantically satisfies the criteria.

#### Scenario: Advisory issues empty skips LLM call
- **WHEN** the advisory evidence collector finds no issues
- **AND** the agent self-reports `status="complete"` with all acceptance results `passed=true`
- **THEN** the task is treated as `complete` without calling the Orchestrator LLM
- **AND** no additional latency is incurred.
