## MODIFIED Requirements

### Requirement: Bash SHALL enforce platform blacklist

The bash tool MUST reject commands that match the platform-specific banned pattern list before execution. The bash tool MUST construct the child process environment via `env_isolation.build_tool_env()` instead of `os.environ.copy()`, ensuring the AChat virtualenv and internal secrets do not leak to the subprocess. The bash tool MUST detect a project-level virtualenv at `<effective_cwd>/.venv` and prepend its `Scripts` (Windows) or `bin` (POSIX) directory to the cleaned `PATH`. The bash tool MUST append a non-blocking `[AChat]` advisory to stdout when `pip install` is detected, no project venv exists, and `Workspace.env_preference` is not `'system_python'`.

#### Scenario: POSIX destructive command is requested

- **WHEN** the command matches `rm -rf /`
- **THEN** the tool refuses to run it.

#### Scenario: POSIX background process inherits stdio

- **WHEN** a bash command starts a background process and the shell script exits
- **THEN** the bash tool MUST NOT wait forever on inherited stdout or stderr
- **AND** it SHOULD clean up the command process group before returning.

#### Scenario: Bash env is isolated from AChat runtime

- **WHEN** the bash tool spawns a subprocess
- **THEN** the child env is built via `build_tool_env()`
- **AND** `VIRTUAL_ENV` and AChat internal secrets are absent
- **AND** the AChat venv `PATH` segment is removed.

#### Scenario: Project venv is prepended to PATH

- **WHEN** the bash tool runs in a workspace with `<effective_cwd>/.venv` present
- **THEN** the child `PATH` has the project venv `Scripts`/`bin` directory prepended
- **AND** `python`/`pip` resolve to the project venv.

#### Scenario: pip install advisory is appended

- **WHEN** the command contains `pip install` and no project venv exists and `env_preference != 'system_python'`
- **THEN** an `[AChat]` advisory is appended to stdout recommending venv creation
- **AND** the command executes without blocking.
