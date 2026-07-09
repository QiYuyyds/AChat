## ADDED Requirements

### Requirement: Worktree SHALL isolate parallel dispatch tasks

When the Orchestrator executes a DAG wave with multiple ready tasks, each task SHALL receive an isolated git worktree (or directory copy fallback) so that concurrent file operations do not interfere.

#### Scenario: Two tasks in the same wave write the same file

- **WHEN** task `t1` and `t2` are in the same wave and both write `src/config.ts`
- **THEN** each task operates in its own worktree at a distinct path
- **AND** neither task's file write is visible to the other during execution
- **AND** after wave completion, both tasks' changes are merged back to the main workspace independently

#### Scenario: Single task in a wave

- **WHEN** only one task is ready in a wave
- **THEN** the task still receives its own worktree
- **AND** behavior is identical to multi-task waves

#### Scenario: Worktree creation fails

- **WHEN** worktree creation fails (e.g., disk full, git error)
- **THEN** the task falls back to running in the main workspace without worktree isolation
- **AND** a warning is logged
- **AND** `detect_wave_conflicts` runs as advisory for that wave

### Requirement: Harness loop attempts SHALL share the same worktree

When a child task is retried via the harness loop (attempt 2+), the continuation attempt MUST run in the same worktree as the previous attempt so the agent can inspect and fix files from prior attempts.

#### Scenario: Second attempt sees files from first attempt

- **WHEN** attempt 1 writes `src/app.tsx` with a bug and fails verification
- **AND** attempt 2 is launched with a continuation prompt
- **THEN** attempt 2 runs in the same worktree as attempt 1
- **AND** `src/app.tsx` from attempt 1 is visible and readable
- **AND** the continuation prompt instructs the agent to fix, not restart

#### Scenario: Worktree path is stable across attempts

- **WHEN** a task has `MAX_CHILD_TASK_ATTEMPTS` set to 4
- **AND** the task fails attempts 1 through 3 before succeeding on attempt 4
- **THEN** all 4 attempts execute with the same worktree path as effective cwd
- **AND** the worktree is only cleaned up after the task reaches a terminal status

### Requirement: Worktree SHALL merge back to main workspace on task completion

When a dispatch task completes successfully, its worktree changes SHALL be merged back to the main workspace before the next wave begins, so downstream tasks can see upstream outputs.

#### Scenario: Completed task merges back

- **WHEN** task `t1` in worktree `agent/x/t1` completes with status `complete`
- **THEN** the worktree's changes are committed to branch `agent/x/t1`
- **AND** the branch is merged into the main workspace's current branch
- **AND** the worktree directory is cleaned up
- **AND** downstream tasks in the next wave can see `t1`'s file changes

#### Scenario: Failed task does not merge back

- **WHEN** task `t1` exhausts all harness loop attempts and ends with status `failed`
- **THEN** the worktree's changes are NOT merged to the main workspace
- **AND** the worktree directory is cleaned up
- **AND** the failed task's file changes are discarded

#### Scenario: Merge conflict

- **WHEN** merging a completed task's worktree branch back to the main workspace produces a git merge conflict
- **THEN** the task is marked with `merge_conflict` status
- **AND** the conflict file list is recorded
- **AND** the conflict information is injected into the aggregate prompt for the Orchestrator to report to the user
- **AND** the worktree is cleaned up after conflict metadata is captured

### Requirement: Non-git workspace SHALL fall back to directory copy

When the workspace is not a git repository and cannot be initialized as one, worktree isolation SHALL fall back to directory copy semantics.

#### Scenario: Non-git directory fallback

- **WHEN** the workspace is not a git repository
- **AND** git init fails or is disabled
- **THEN** each task receives a copy of the main workspace directory as its "worktree"
- **AND** on task completion, changed files are copied back to the main workspace (overwrite strategy)
- **AND** `detect_wave_conflicts` runs as advisory for that wave

#### Scenario: Sandbox workspace auto-initializes git

- **WHEN** a sandbox-mode workspace is created
- **THEN** `git init` and an initial commit are performed in the workspace root path
- **AND** the workspace is ready for true git worktree isolation

### Requirement: Worktree SHALL use deterministic branch naming

Worktree branches SHALL follow the `agent/{sanitized-agent-name}/{short-task-id}` naming convention for consistent GC and debugging.

#### Scenario: Branch name format

- **WHEN** task `t1` is assigned to agent named "Code Writer"
- **THEN** the worktree branch is named `agent/code-writer/{short-task-id}`
- **AND** the agent name is sanitized to lowercase kebab-case with special characters removed

#### Scenario: Branch name uniqueness

- **WHEN** two tasks are assigned to the same agent in the same wave
- **THEN** each task gets a distinct branch name because task IDs are unique
- **AND** no branch name collision occurs

### Requirement: Worktree SHALL be cleaned up on abort or crash

When a dispatch run is aborted or the process crashes, orphaned worktrees SHALL be detected and cleaned up on next startup.

#### Scenario: Run aborted mid-wave

- **WHEN** the Orchestrator run is aborted while a wave is in progress
- **THEN** all active worktrees for that dispatch round are cleaned up
- **AND** their branches are deleted from the main repository

#### Scenario: Orphan worktree cleanup on startup

- **WHEN** the backend starts up
- **THEN** the system scans `.agenthub-data/worktrees/` for orphaned worktree directories
- **AND** removes any that have no corresponding active run
- **AND** prunes stale `agent/*` branches via `git worktree prune` and `git branch -D`

### Requirement: Worktree events SHALL be published to the frontend

The system SHALL publish SSE events when worktrees are created, merged, or cleaned up, so the frontend can display isolation status.

#### Scenario: Worktree created event

- **WHEN** a worktree is created for task `t1`
- **THEN** a `worktree.created` event is published with `taskId`, `branchName`, and `path` fields

#### Scenario: Worktree merged event

- **WHEN** a worktree is successfully merged back to the main workspace
- **THEN** a `worktree.merged` event is published with `taskId` and `mergeStatus` fields

#### Scenario: Worktree cleaned up event

- **WHEN** a worktree directory is cleaned up
- **THEN** a `worktree.cleaned` event is published with `taskId`
