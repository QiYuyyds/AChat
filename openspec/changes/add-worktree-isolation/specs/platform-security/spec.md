## ADDED Requirements

### Requirement: Worktree paths SHALL be validated for safety

Worktree creation paths MUST be validated to ensure they fall within the designated `.agenthub-data/worktrees/` directory and do not escape via symlinks or path traversal.

#### Scenario: Worktree path is within designated directory

- **WHEN** a worktree is created for task `t1` in conversation `conv_xxx`
- **THEN** the worktree path is `.agenthub-data/worktrees/conv_xxx/t1/`
- **AND** the path is validated to be within the `.agenthub-data/worktrees/` subtree

#### Scenario: Symlink escape attempt

- **WHEN** a symlink in the workspace points outside the designated worktree directory
- **THEN** the symlink is not followed during worktree creation
- **AND** a security warning is logged

### Requirement: Sandbox workspace SHALL auto-initialize git

Sandbox-mode workspaces MUST be initialized as git repositories at creation time to enable true git worktree isolation.

#### Scenario: Sandbox workspace created

- **WHEN** a new sandbox-mode conversation workspace is created
- **THEN** `git init` is executed in the workspace root path
- **AND** an initial empty commit is created to establish a valid HEAD
- **AND** a `.gitignore` is created to exclude `.agenthub-data/` internal files

#### Scenario: Git init fails

- **WHEN** `git init` fails in a sandbox workspace (e.g., git not installed)
- **THEN** the workspace is marked as non-git
- **AND** worktree isolation falls back to directory copy mode for that workspace
- **AND** a warning is logged

### Requirement: Worktree cleanup SHALL be safe and idempotent

Worktree cleanup MUST be safe to call multiple times and MUST NOT delete files outside the worktree directory.

#### Scenario: Cleanup called twice

- **WHEN** `cleanup_worktree` is called for a worktree that has already been cleaned up
- **THEN** the call returns success without error
- **AND** no filesystem operations are performed

#### Scenario: Cleanup does not affect main workspace

- **WHEN** a worktree at `.agenthub-data/worktrees/conv_xxx/t1/` is cleaned up
- **THEN** only files within that specific worktree directory are deleted
- **AND** the main workspace at `.agenthub-data/workspaces/conv_xxx/` is unaffected
- **AND** sibling worktrees at `.agenthub-data/worktrees/conv_xxx/t2/` are unaffected

### Requirement: Orphan worktree cleanup SHALL run on startup

The backend MUST scan for and clean up orphaned worktree directories on startup to recover from crashes.

#### Scenario: Startup with orphaned worktrees

- **WHEN** the backend starts up
- **AND** `.agenthub-data/worktrees/` contains directories with no corresponding active run
- **THEN** each orphaned worktree directory is removed
- **AND** stale `agent/*` branches are pruned via `git worktree prune`
- **AND** a summary of cleaned orphans is logged

#### Scenario: Startup with no orphans

- **WHEN** the backend starts up
- **AND** no orphaned worktree directories exist
- **THEN** no cleanup operations are performed
- **AND** startup proceeds normally
