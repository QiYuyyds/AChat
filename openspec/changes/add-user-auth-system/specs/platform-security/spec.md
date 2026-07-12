# Platform Security Delta: Authentication & Isolation

## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Bash tool blacklist SHALL remain enforced

The platform-specific bash command blacklist (POSIX and Windows) MUST continue to be enforced. The blacklist checking logic SHALL NOT be affected by the addition of user authentication. The bash tool's cwd MUST be confined to the effective workspace, which is now additionally scoped under the owning user's directory.

#### Scenario: User aborts a run
- **WHEN** the user requests run abort
- **THEN** the adapter cancels the in-flight request
- **AND** the run is marked as `aborted`.

#### Scenario: Bash command targets another user's workspace
- **WHEN** a bash command attempts to access a path outside the current user's workspace tree
- **THEN** the command is rejected before execution.
