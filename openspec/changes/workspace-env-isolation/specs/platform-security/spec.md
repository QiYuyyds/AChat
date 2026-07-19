## MODIFIED Requirements

### Requirement: SDK child process environment SHALL preserve required host basics while isolating AChat runtime

SDK child process environments MUST preserve required values such as PATH and HOME/USERPROFILE while applying adapter-specific isolation. SDK child process environments MUST be constructed via `env_isolation.build_tool_env()` so that AChat's virtualenv markers (`VIRTUAL_ENV`, `PYTHONHOME`), internal secrets, and the AChat venv `PATH` segment are removed before adapter-specific variables are layered on top. The env variable blacklist and PATH cleaning logic MUST be shared between the bash tool and CLI adapters through a single `env_isolation` module.

#### Scenario: Codex runs on Windows

- **WHEN** HOME is missing and USERPROFILE exists
- **THEN** the child env receives a HOME fallback.

#### Scenario: CLI subprocess does not inherit AChat venv

- **WHEN** a Claude Code or Codex CLI subprocess is spawned
- **THEN** the child env is built via `build_tool_env()`
- **AND** `VIRTUAL_ENV` is absent
- **AND** the AChat venv `Scripts`/`bin` segment is removed from `PATH`
- **AND** adapter-specific `HOME`/`USERPROFILE` and `extra_env` are applied on top of the cleaned env.

## ADDED Requirements

### Requirement: Env variable blacklist SHALL be a shared contract

The set of environment variables that MUST be removed from tool and CLI subprocess envs SHALL be defined in a single source (`env_isolation` module) and treated as a security contract. The blacklist MUST include at minimum: `VIRTUAL_ENV`, `PYTHONHOME`, `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET`, `REDIS_URL`, `MILVUS_HOST`, `MILVUS_PORT`, `ES_HOST`, `ES_PORT`, `NEO4J_URI`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `NPM_CONFIG_PREFIX`, `M2_HOME`, `GOPATH`, `GOMODCACHE`. The env MUST preserve `HOME`, `USERPROFILE`, `SystemRoot`, `LANG`, `LC_ALL`, `TEMP`, `TMP`, `JAVA_HOME`, `ANDROID_SDK_ROOT`, and the cleaned `PATH`. Changes to the blacklist MUST be reflected in `specs/platform-security/spec.md` and `specs/11-platform.md`.

#### Scenario: Blacklisted variable is removed

- **WHEN** the AChat process env contains `DATABASE_URL=postgresql://...`
- **THEN** `build_tool_env()` produces a child env without `DATABASE_URL`.

#### Scenario: User system variable is preserved

- **WHEN** the AChat process env contains `JAVA_HOME=/usr/lib/jvm/java-17`
- **THEN** the child env retains `JAVA_HOME` so the agent can run `mvn`.

### Requirement: uvicorn reload SHALL exclude non-code directories

When AChat runs in development mode (`settings.debug=True`), `uvicorn.run` MUST set `reload_dirs` to limit filesystem watching to the application source directory (e.g., `backend/app`), and MUST set `reload_excludes` to skip `.venv`, `.agenthub-data`, `node_modules`, and `__pycache__` directories. This prevents virtualenv file changes (e.g., from `pip install` in an agent subprocess) from triggering spurious backend hot reloads. In production mode (`settings.debug=False`), reload is disabled and these options have no effect.

#### Scenario: pip install in agent workspace does not crash AChat

- **WHEN** an agent runs `pip install` that writes files into a `.venv` directory
- **THEN** uvicorn's file watcher ignores the change
- **AND** AChat does not hot-reload or crash with `ModuleNotFoundError`.

#### Scenario: Source code change still triggers reload

- **WHEN** a developer edits `backend/app/services/agent_runner.py`
- **THEN** uvicorn detects the change within `reload_dirs`
- **AND** hot reload proceeds normally.
