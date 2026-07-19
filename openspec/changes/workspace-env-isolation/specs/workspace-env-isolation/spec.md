## ADDED Requirements

### Requirement: Bash subprocess environment SHALL be isolated from AChat runtime

The bash tool MUST construct the child process environment via `build_tool_env()` instead of `os.environ.copy()`. The constructed env MUST NOT contain AChat's virtualenv markers (`VIRTUAL_ENV`, `PYTHONHOME`), AChat internal secrets (`DATABASE_URL`, `SECRET_KEY`, `REDIS_URL`, `MILVUS_HOST`, `MILVUS_PORT`, `ES_HOST`, `NEO4J_URI`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`), or global package-manager pollution variables (`NPM_CONFIG_PREFIX`, `M2_HOME`, `GOPATH`, `GOMODCACHE`). The child `PATH` MUST NOT include the AChat virtualenv `Scripts` (Windows) or `bin` (POSIX) directory segment. The env MUST preserve `HOME`/`USERPROFILE`, `SystemRoot`, `LANG`, `TEMP`/`TMP`, and the cleaned `PATH`.

#### Scenario: Agent runs pip install in a user project

- **WHEN** an agent executes `pip install requests` via the bash tool in a bound local workspace
- **THEN** the child process `PATH` does not contain the AChat virtualenv directory
- **AND** `VIRTUAL_ENV` is absent from the child env
- **AND** `pip` resolves to the project venv (if present) or the system pip, never the AChat venv pip
- **AND** the package is installed outside AChat's `site-packages`.

#### Scenario: AChat runs without an activated virtualenv

- **WHEN** AChat is started via a system Python (no `VIRTUAL_ENV` in the process env)
- **THEN** `build_tool_env()` still removes blacklisted internal secrets from the child env
- **AND** `PATH` cleaning is a no-op when no AChat venv segment is detected.

#### Scenario: Child process preserves user system variables

- **WHEN** the host has `JAVA_HOME` or `ANDROID_SDK_ROOT` set
- **THEN** the bash child env retains those variables
- **AND** the agent can run `mvn` or `gradle` commands that depend on them.

### Requirement: AChat virtualenv path SHALL be detected from sys.executable

The env cleaning logic MUST derive the AChat virtualenv root from `sys.executable` (Windows: `<venv>/Scripts/python.exe`, POSIX: `<venv>/bin/python`), NOT from a hardcoded path or the `VIRTUAL_ENV` env variable. When `sys.executable` does not indicate a virtualenv, the detection MUST return `None` and PATH cleaning MUST skip venv removal while still applying the variable blacklist.

#### Scenario: AChat runs from a named .venv

- **WHEN** AChat is started via `d:/project/.venv/Scripts/python.exe`
- **THEN** `_detect_achat_venv()` returns `d:/project/.venv`
- **AND** the child `PATH` has the `d:/project/.venv/Scripts` segment removed.

#### Scenario: AChat runs from system Python

- **WHEN** AChat is started via `/usr/bin/python3`
- **THEN** `_detect_achat_venv()` returns `None`
- **AND** PATH cleaning only removes blacklisted variables, leaving `PATH` segments intact.

### Requirement: Bash SHALL prepend project virtualenv to PATH when present

When the bash tool executes a command in a local workspace, it MUST detect a project-level virtualenv at `<effective_cwd>/.venv` (Windows: `Scripts/python.exe`, POSIX: `bin/python`). If present, the tool MUST prepend the venv `Scripts` (Windows) or `bin` (POSIX) directory to the cleaned `PATH` so that `python` and `pip` resolve to the project venv. Detection MUST happen on every bash invocation (no caching).

#### Scenario: Project has a .venv

- **WHEN** the agent runs `python -c "import sys; print(sys.executable)"` in a workspace where `<cwd>/.venv/bin/python` exists
- **THEN** the output points to `<cwd>/.venv/bin/python`
- **AND** `pip install` installs into `<cwd>/.venv`.

#### Scenario: Project has no .venv

- **WHEN** the workspace has no `<cwd>/.venv` directory
- **THEN** the bash tool does not prepend any venv to `PATH`
- **AND** `python`/`pip` resolve to whatever the cleaned `PATH` yields (typically system Python).

#### Scenario: User creates a venv mid-conversation

- **WHEN** the user creates `<cwd>/.venv` after a prior bash call that had no venv
- **AND** the agent issues another bash command
- **THEN** the new bash call detects the venv and prepends it to `PATH`.

### Requirement: Workspace SHALL detect project language and environment status

When a workspace is bound to a local directory, `WorkspaceEnvService.detect_project_env()` MUST asynchronously detect the project language by looking for marker files: Python (`requirements.txt` / `pyproject.toml` / `setup.py`), Node.js (`package.json`), Java (`pom.xml` / `build.gradle`), Go (`go.mod`). For Python projects, it MUST also detect whether a `.venv` exists. Detection MUST NOT block workspace creation or conversation startup; failures MUST be logged and degraded silently.

#### Scenario: Python project with requirements.txt but no venv

- **WHEN** a workspace is bound to a directory containing `requirements.txt` and no `.venv`
- **THEN** `detect_project_env()` returns `language='python'` and `venv_present=False`
- **AND** the service emits a `WorkspaceEnvHintEvent` to prompt the user.

#### Scenario: Python project already has a .venv

- **WHEN** a workspace is bound to a directory containing `pyproject.toml` and `.venv/bin/python`
- **THEN** `detect_project_env()` returns `language='python'` and `venv_present=True`
- **AND** no `WorkspaceEnvHintEvent` is emitted.

#### Scenario: Non-Python project

- **WHEN** a workspace is bound to a directory containing only `package.json`
- **THEN** `detect_project_env()` returns `language='nodejs'`
- **AND** no venv hint is emitted (Python-specific guidance is skipped).

#### Scenario: Detection fails

- **WHEN** the bound directory is inaccessible or the detection raises an exception
- **THEN** the error is logged at warning level
- **AND** workspace creation proceeds without env hint
- **AND** conversation startup is not blocked.

### Requirement: Python venv creation SHALL require explicit user choice

When a Python project without a venv is detected, AChat MUST NOT automatically create a venv. AChat MUST emit a `WorkspaceEnvHintEvent` offering the user three choices: create a `.venv`, skip, or use system Python. The user's choice MUST be persisted to `Workspace.env_preference` (`'venv_created'` / `'skip'` / `'system_python'`) so the hint is not shown again for the same workspace. Venv creation MUST only proceed after the user explicitly chooses "create".

#### Scenario: User chooses to create a venv

- **WHEN** the frontend sends `POST /api/workspaces/{conversation_id}/create-venv` after the user clicks "Create .venv"
- **THEN** AChat runs `python -m venv --upgrade-deps .venv` in the workspace bound path
- **AND** emits `WorkspaceEnvStatusEvent` with `status='creating'` then `status='ready'` on success
- **AND** sets `Workspace.env_preference='venv_created'`
- **AND** subsequent bash calls prepend the new venv to `PATH`.

#### Scenario: User chooses to skip

- **WHEN** the user clicks "Skip" on the env hint card
- **THEN** AChat sets `Workspace.env_preference='skip'`
- **AND** the hint is not shown again
- **AND** bash calls use the cleaned system `PATH` without venv prepend.

#### Scenario: User chooses system Python

- **WHEN** the user clicks "Use system Python"
- **THEN** AChat sets `Workspace.env_preference='system_python'`
- **AND** the hint is not shown again
- **AND** bash calls suppress the pip install advisory (user accepted system Python).

#### Scenario: Venv creation fails

- **WHEN** `python -m venv` exits non-zero (e.g., disk full, permission denied)
- **THEN** AChat emits `WorkspaceEnvStatusEvent` with `status='failed'` and the error message
- **AND** `Workspace.env_preference` remains `null`
- **AND** the hint card allows the user to retry.

#### Scenario: User ignores the hint

- **WHEN** the user does not interact with the hint card and starts chatting
- **THEN** the agent runs normally with env cleaning applied (no pollution risk)
- **AND** the hint remains available for later interaction
- **AND** no venv is created automatically.

### Requirement: Bash SHALL emit pip install advisory when no venv is present

When the bash tool detects a `pip install` command in the user's input, the workspace is a Python project, no project `.venv` exists, and `Workspace.env_preference` is not `'system_python'`, the tool MUST append an advisory notice to the command output. The advisory MUST be prefixed with `[AChat]` and clearly separated from the command's actual stdout. The advisory MUST NOT block or alter command execution; it is a user/agent experience hint only.

#### Scenario: pip install without venv and user has not chosen system Python

- **WHEN** the agent runs `pip install flask` in a Python project with no `.venv` and `env_preference` is `null` or `'skip'`
- **THEN** the bash output includes an `[AChat]` advisory recommending venv creation
- **AND** the command still executes (package installs to system Python, not AChat venv)
- **AND** AChat's own virtualenv is not polluted.

#### Scenario: pip install after user chose system Python

- **WHEN** `Workspace.env_preference='system_python'`
- **THEN** no advisory is appended for `pip install` commands.

#### Scenario: pip install with project venv present

- **WHEN** the project has a `.venv`
- **THEN** no advisory is appended because `pip install` targets the project venv.

### Requirement: CLI adapters SHALL reuse env cleaning logic

Claude Code and Codex CLI adapters MUST construct their child process environment by calling `env_isolation.build_tool_env(cwd, project_venv_path)` first, then layering adapter-specific variables (`HOME`/`USERPROFILE` user isolation, `extra_env`) on top. CLI adapters MUST NOT inherit `VIRTUAL_ENV` or the AChat venv `PATH` segment from the backend process.

#### Scenario: Claude Code subprocess env

- **WHEN** AChat spawns a Claude Code CLI subprocess
- **THEN** the child env has `VIRTUAL_ENV` removed and the AChat venv `PATH` segment stripped
- **AND** `HOME`/`USERPROFILE` is set to the owning user's directory
- **AND** `extra_env` from agent config is applied on top of the cleaned env.

#### Scenario: Codex subprocess env

- **WHEN** AChat spawns a Codex CLI subprocess
- **THEN** the same env cleaning as Claude Code applies
- **AND** no AChat internal secrets leak to the Codex subprocess.
