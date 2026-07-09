## MODIFIED Requirements

### Requirement: Child tasks SHALL respect dependency order and semantic reports

AgentRunner MUST execute dispatch tasks as a DAG and skip dependent tasks when prerequisites fail, required inputs cannot be resolved, or the child task does not report a successful semantic outcome. Each task in a wave SHALL execute in an isolated worktree, and completed tasks SHALL merge their worktree changes back to the main workspace before the next wave begins.

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

#### Scenario: Code task lacks runnable verification

- **WHEN** a code implementation child task reports `complete`
- **AND** recorded command evidence has no successful non-prepare build, compile, test, lint, or typecheck command
- **THEN** the dispatch task is treated as `failed`
- **AND** the existing retry or replan flow may remediate it.

#### Scenario: Code task lacks project output

- **WHEN** a code implementation child task reports `complete`
- **AND** no required `project` output can be created and bound from workspace file writes
- **THEN** the dispatch task is treated as `failed`
- **AND** the existing retry or replan flow may remediate it.

#### Scenario: Replan references a previous-round task

- **WHEN** a remediation plan depends on a task id from an earlier dispatch round
- **THEN** AgentRunner treats that previous task as a resolved external dependency
- **AND** validates and executes the remediation plan without requiring the previous task to be repeated in the new plan.

#### Scenario: Wave creates worktrees for parallel tasks

- **WHEN** a DAG wave has multiple ready tasks
- **THEN** each task is assigned a worktree with a unique branch before execution begins
- **AND** all tasks in the wave execute concurrently in their respective worktrees
- **AND** the worktree path is passed as the effective cwd to the child task's ToolContext

#### Scenario: Wave merges completed tasks before next wave

- **WHEN** a wave completes
- **AND** one or more tasks have status `complete`
- **THEN** each completed task's worktree changes are merged back to the main workspace
- **AND** worktrees for all tasks in the wave are cleaned up
- **AND** the next wave's worktrees are created from the updated main workspace state

#### Scenario: Harness loop retries within the same worktree

- **WHEN** a child task attempt ends without `complete` status
- **AND** the harness loop decides to retry (attempt < `MAX_CHILD_TASK_ATTEMPTS`)
- **THEN** the continuation attempt runs in the same worktree as the previous attempt
- **AND** the worktree path does not change between attempts
- **AND** files written by prior attempts are visible to the continuation attempt

#### Scenario: File conflict detection becomes advisory in worktree mode

- **WHEN** worktree isolation is active for a wave
- **THEN** `detect_wave_conflicts` does not run for that wave
- **AND** file conflicts are physically impossible because each task has its own worktree
- **WHEN** worktree creation fails and tasks fall back to shared workspace
- **THEN** `detect_wave_conflicts` runs as advisory (issues collected but not blocking)
