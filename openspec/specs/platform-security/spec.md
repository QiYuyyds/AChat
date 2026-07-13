# Platform Security

## Purpose

Defines cross-platform shell, path, sandbox, process safety, and authentication rules. Detailed platform notes live in `specs/11-platform.md`.

## Requirements

### Requirement: All API endpoints SHALL enforce authentication

Every HTTP endpoint except `/api/auth/*` and `/health` MUST require a valid JWT. The `get_current_user` FastAPI dependency MUST be applied to all routers. Requests without a valid token MUST receive HTTP 401.

#### Scenario: Unauthenticated request is rejected
- **WHEN** a request to any protected endpoint lacks a valid JWT
- **THEN** AChat returns HTTP 401 before any business logic executes.

### Requirement: JWT secret SHALL be configured via environment

AChat MUST read the JWT signing secret from the `JWT_SECRET` environment variable. If unset, AChat MUST refuse to start (except in test mode). The secret MUST be at least 32 characters.

#### Scenario: JWT secret is missing on startup
- **WHEN** AChat starts without `JWT_SECRET` set
- **THEN** it logs an error and exits with a non-zero status.

### Requirement: Platform detection SHALL drive shell behavior

AChat MUST select shell command conventions and tool descriptions based on the current host platform.

#### Scenario: Host is POSIX
- **WHEN** AChat executes an AChat-managed bash command
- **THEN** it SHOULD use the user's login zsh/bash shell when available
- **AND** it MUST fall back to a POSIX-compatible shell invocation when the user shell cannot be resolved safely.

#### Scenario: Host is Windows
- **WHEN** bash-like tool descriptions are built
- **THEN** they use PowerShell-oriented examples and Windows blacklist language.

### Requirement: Path safety SHALL be platform-aware

Workspace path checks MUST handle case sensitivity, path separators, drive roots, and sensitive directories according to host platform.

#### Scenario: Windows path case differs
- **WHEN** a path differs only by drive-letter case
- **THEN** containment checks still evaluate correctly.

### Requirement: Command blacklist SHALL be shared

POSIX and Windows banned command patterns MUST be defined in one shared server security module and used by both tools and SDK approval bridges where applicable. The blacklist checking logic SHALL NOT be affected by the addition of user authentication. The bash tool's cwd MUST be confined to the effective workspace, which is now additionally scoped under the owning user's directory.

#### Scenario: Claude Code asks to run a banned command
- **WHEN** the Bash tool approval includes a matching command
- **THEN** the adapter denies the tool use.

#### Scenario: Bash command targets another user's workspace
- **WHEN** a bash command attempts to access a path outside the current user's workspace tree
- **THEN** the command is rejected before execution.

### Requirement: Child processes SHALL be cleaned up

Long-running child processes spawned by tool or SDK boundaries MUST be aborted or terminated when the owning run or app shuts down.

#### Scenario: User aborts a run
- **WHEN** the run AbortSignal fires
- **THEN** active tool or SDK work receives cancellation.

### Requirement: SDK child process environment SHALL preserve required host basics

SDK child process environments MUST preserve required values such as PATH and HOME/USERPROFILE while applying adapter-specific isolation.

#### Scenario: Codex runs on Windows
- **WHEN** HOME is missing and USERPROFILE exists
- **THEN** the child env receives a HOME fallback.

### Requirement: CLI agent subprocess environments SHALL be isolated per user

When spawning Claude Code or Codex CLI subprocesses, AChat MUST set `HOME` (POSIX) or `USERPROFILE` (Windows) to the owning user's directory so that CLI-managed credentials and config are isolated.

#### Scenario: Two users run Claude Code agents
- **WHEN** user A and user B each trigger a Claude Code agent run
- **THEN** the CLI subprocess for user A uses user A's home directory
- **AND** the CLI subprocess for user B uses user B's home directory
- **AND** neither user's CLI credentials leak to the other's subprocess.

### Requirement: Workspace paths SHALL be isolated per user

Workspace root paths MUST include a `users/{user_id}/` segment. Filesystem tools MUST reject any path that resolves outside the current user's directory tree.

#### Scenario: Path traversal across users is blocked
- **WHEN** a tool receives a relative path that resolves to another user's workspace
- **THEN** the path is rejected with a security error.
