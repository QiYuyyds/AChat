## MODIFIED Requirements

### Requirement: Orchestrator SHALL plan before dispatch

The orchestration flow MUST produce a compiled and validated task plan before launching child agent runs.

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

#### Scenario: Code task contract is normalized with build toolchain
- **WHEN** the compiled plan contains a code implementation task
- **AND** the workspace contains a build toolchain manifest (`package.json`, `pom.xml`, `build.gradle`, `Cargo.toml`, `go.mod`, `pyproject.toml`, `setup.py`, `Makefile`, or `CMakeLists.txt`)
- **THEN** AgentRunner ensures the task has a required `project` expected output
- **AND** ensures the task has acceptance/evidence requirements for a successful runnable verification command.

#### Scenario: Code task contract is normalized without build toolchain
- **WHEN** the compiled plan contains a code implementation task
- **AND** the workspace does NOT contain any build toolchain manifest
- **THEN** AgentRunner ensures the task has a required `project` expected output
- **AND** does NOT auto-append the runnable verification acceptance criterion or required evidence
- **AND** file-write evidence and deploy success are sufficient for completion.

### Requirement: Child tasks SHALL respect dependency order and semantic reports

AgentRunner MUST execute dispatch tasks as a DAG and skip dependent tasks when prerequisites fail, required inputs cannot be resolved, or the child task does not report a successful semantic outcome.

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

#### Scenario: Child task reports failed acceptance
- **WHEN** a child run calls `report_task_result`
- **AND** the report status is not `complete` or an acceptance result is missing/failed
- **THEN** the dispatch task is treated as `failed`
- **AND** dependent tasks are skipped.

#### Scenario: Code task lacks runnable verification in workspace with build toolchain
- **WHEN** a code implementation child task reports `complete`
- **AND** the workspace contains a build toolchain manifest
- **AND** recorded command evidence has no successful non-prepare build, compile, test, lint, or typecheck command
- **THEN** the dispatch task is treated as `failed`
- **AND** the existing retry or replan flow may remediate it.

#### Scenario: Code task in workspace without build toolchain passes on file evidence
- **WHEN** a code implementation child task reports `complete`
- **AND** the workspace does NOT contain any build toolchain manifest
- **AND** the task has file-write evidence or deploy success evidence
- **THEN** the dispatch task is treated as `complete`
- **AND** the runnable verification command gate is skipped.

#### Scenario: Failed command recovered by prefix match
- **WHEN** a non-prepare bash command fails with non-zero exit code or timeout
- **AND** a later non-prepare command shares the same command prefix (first one or two tokens)
- **AND** the later command succeeds with `exitCode=0`
- **THEN** the earlier failed command does NOT block task completion.

#### Scenario: Failed command not recovered when no later prefix match exists
- **WHEN** a non-prepare bash command fails
- **AND** no later command with the same prefix succeeds
- **THEN** the failed command blocks task completion
- **AND** the dispatch task is treated as `failed`.

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

### Requirement: Child task report SHALL be a stream-terminal event

When a child task run is configured with `require_task_report=True`, AgentRunner MUST treat the `report_task_result` tool call as a terminal event that stops stream consumption, identical to how `plan_tasks` terminates the plan stage.

#### Scenario: Agent calls report_task_result and stream stops
- **WHEN** a child run's adapter stream emits a `tool.call` for `report_task_result`
- **AND** the run was started with `require_task_report=True`
- **THEN** AgentRunner emits the `tool.result` and `message.end` events
- **AND** stops consuming further stream events from that adapter
- **AND** the `task_report` is captured from the tool result.

#### Scenario: Non-child run is unaffected
- **WHEN** a run is started with `require_task_report=False` (e.g., a normal single-chat agent run)
- **AND** the agent happens to call `report_task_result`
- **THEN** the stream continues normally
- **AND** no terminal behavior is applied.

### Requirement: Harness loop retries SHALL be visible to the user

When the harness loop retries a child task attempt (attempt 2 or later), AgentRunner MUST publish a `dispatch.retry` event before launching the continuation run, so the frontend can display retry status.

#### Scenario: Retry event published on continuation
- **WHEN** a child task attempt ends without `complete` status
- **AND** the harness loop decides to retry (attempt < `MAX_CHILD_TASK_ATTEMPTS`)
- **AND** the failure is not `aborted` or `blocked`
- **THEN** AgentRunner publishes a `dispatch.retry` event
- **AND** the event includes `attempt`, `max_attempts`, and the evaluation `error`
- **AND** then launches the next attempt with the continuation prompt.

#### Scenario: No retry event on first attempt
- **WHEN** a child task is starting its first attempt
- **THEN** no `dispatch.retry` event is published
- **AND** only `dispatch.start` is emitted as before.

#### Scenario: No retry event when attempts exhausted
- **WHEN** a child task has exhausted all `MAX_CHILD_TASK_ATTEMPTS` attempts
- **THEN** no `dispatch.retry` event is published
- **AND** `dispatch.end` with `status=failed` is emitted as before.
