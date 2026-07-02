## MODIFIED Requirements

### Requirement: ClaudeCodeAdapter SHALL spawn Claude Code CLI as subprocess

ClaudeCodeAdapter MUST spawn the `claude` CLI binary as a subprocess via `asyncio.create_subprocess_exec`, communicate via stream-json protocol over stdin/stdout, and translate CLI output events into `StreamEvent`. The adapter SHALL NOT use the Anthropic SDK or implement its own tool loop.

#### Scenario: Claude Code CLI handles tools autonomously
- **WHEN** a Claude Code agent receives a prompt
- **THEN** the adapter starts the `claude` CLI with `--output-format stream-json --input-format stream-json --permission-mode bypassPermissions`
- **AND** the CLI internally manages its own tool execution, sandbox, and permission approval
- **AND** the adapter translates CLI events (assistant → text/thinking/tool_use, user → tool_result, result → usage+output) into `StreamEvent`
- **AND** the adapter auto-responds `allow` to `control_request` events

#### Scenario: Claude Code CLI resumes a prior session
- **WHEN** the adapter receives `AdapterInput.resume_session_id` set
- **THEN** it passes `--resume <session_id>` to the CLI
- **AND** if the resume yields a fresh (different) session id AND the run fails, the adapter reports `session_id=""` so the daemon-level retry-with-fresh-session can trigger

#### Scenario: Claude Code CLI is not installed
- **WHEN** `shutil.which("claude")` returns nothing and no `executable_path` is configured
- **THEN** the adapter raises a clear error: "Claude Code CLI not found. Install it with `npm install -g @anthropic-ai/claude-code` or configure executable_path."

#### Scenario: User cancels a running Claude Code agent
- **WHEN** `cancel_event` is set
- **THEN** the adapter closes stdin, waits up to 10s for graceful exit, then terminates and kills the process

### Requirement: CodexAdapter SHALL spawn Codex CLI as subprocess

CodexAdapter MUST spawn the `codex` CLI binary as a subprocess via `asyncio.create_subprocess_exec`, communicate via JSON-RPC 2.0 over stdin/stdout (`app-server --listen stdio://`), and translate JSON-RPC notifications into `StreamEvent`. The adapter SHALL NOT use the OpenAI Codex SDK or implement its own tool loop.

#### Scenario: Codex CLI app-server lifecycle
- **WHEN** a Codex agent receives a prompt
- **THEN** the adapter starts `codex app-server --listen stdio://`
- **AND** performs JSON-RPC handshake: `initialize` → `initialized`
- **AND** starts or resumes a thread via `thread/start` or `thread/resume`
- **AND** sends the prompt via `turn/start`
- **AND** waits for `turn/completed` notification while emitting translated events

#### Scenario: Codex turn encounters semantic inactivity
- **WHEN** the Codex CLI process is alive but emits no semantic progress within `semantic_inactivity_timeout` (default 10 minutes)
- **THEN** the adapter marks the run as `timeout` with `CodexSemanticInactivityMarker`
- **AND** on the first turn, if no progress after `first_turn_no_progress_timeout` (default 30s), times out with `CodexFirstTurnNoProgressMarker`

#### Scenario: Codex CLI is not installed
- **WHEN** `shutil.which("codex")` returns nothing and no `executable_path` is configured
- **THEN** the adapter raises a clear error with installation instructions

### Requirement: CLI adapters SHALL filter protocol-critical custom args

Each CLI adapter MUST define a set of `blocked_args` (flags hardcoded by the daemon) and strip matching entries from user-provided `custom_args`. This mirrors multica's `filterCustomArgs` pattern.

#### Scenario: User tries to override output format
- **WHEN** a Claude Code agent has `custom_args: ["--output-format", "json"]`
- **THEN** the adapter logs a warning and removes both `--output-format` and its value `json`
- **AND** the hardcoded `--output-format stream-json` remains

#### Scenario: User adds legitimate custom args
- **WHEN** a Claude Code agent has `custom_args: ["--model", "claude-haiku-4-5"]`
- **THEN** `--model` is not in `blocked_args`, so it passes through
- **AND** since `--model` is also set by AChat from `agent.model_id`, the CLI's last-wins behavior applies

### Requirement: CLI adapters SHALL isolate child environment variables

CLI adapters MUST strip internal AChat/Claude Code runtime markers from the child process environment while preserving user-facing config variables. Markers to strip include `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_SSE_PORT`, `CLAUDE_CODE_EXECPATH`, and any `CLAUDECODE_*` prefixed vars.

#### Scenario: Parent runs inside Claude Code
- **WHEN** AChat is itself running inside Claude Code (`CLAUDECODE=1`)
- **THEN** the child `claude` process does NOT inherit `CLAUDECODE=1`
- **AND** user-facing vars like `CLAUDE_CODE_GIT_BASH_PATH` ARE inherited

### Requirement: SDK adapters SHALL remain unchanged

CustomAgentAdapter MUST continue using the OpenAI SDK (`openai` package) with its own tool loop over the Chat Completions API. MockAdapter MUST remain unchanged. No behavior, interface, or data model change SHALL affect either adapter.

#### Scenario: Custom agent still works after migration
- **WHEN** a `custom` agent receives a prompt
- **THEN** CustomAdapter calls the OpenAI-compatible Chat Completions API
- **AND** runs the tool loop via `tool_registry.execute()`
- **AND** behavior is identical to before the migration

## ADDED Requirements

### Requirement: Agent model SHALL support CLI configuration fields

The `Agent` model SHALL include optional fields `executable_path` (str|null) and `protocol_family` (str|null) for CLI-based agents. SDK-based agents (custom, mock) SHALL leave these null.

#### Scenario: Claude Code agent with custom executable path
- **WHEN** an agent has `adapter_name="claude-code"` and `executable_path="/opt/claude-nightly/bin/claude"`
- **THEN** the adapter spawns `/opt/claude-nightly/bin/claude` instead of searching PATH

#### Scenario: Custom agent has no CLI config
- **WHEN** an agent has `adapter_name="custom"`
- **THEN** `executable_path` and `protocol_family` are null and ignored

### Requirement: AdapterInput SHALL carry CLI-specific fields

`AdapterInput` SHALL include optional fields `executable_path`, `extra_env`, `custom_args`, `resume_session_id`, and `mcp_config` for CLI-based adapters. SDK adapters SHALL ignore these fields.

#### Scenario: CLI adapter receives executable path override
- **WHEN** `AdapterInput.executable_path` is set
- **THEN** the CLI adapter uses it as the binary path
- **AND** falls back to PATH lookup when it is None or empty

### Requirement: AgentRunner SHALL branch SDK vs CLI paths

`AgentRunner.build_adapter_input` SHALL distinguish CLI-based agents (`claude-code`, `codex`) from SDK-based agents (`custom`) and skip API key resolution, tool injection, and history building for CLI agents. CLI agents use the CLI's own authentication, tools, and session resume.

#### Scenario: API key not resolved for CLI agent
- **WHEN** a `claude-code` agent runs and `agent.api_key` is null
- **THEN** `build_adapter_input` does NOT query `app_settings` for `anthropic_api_key`
- **AND** the CLI uses its own authentication (claude login / env var)

#### Scenario: Tools not injected for CLI agent
- **WHEN** a `claude-code` agent runs
- **THEN** `memory_recall`, `load_skill`, and RAG tools are NOT implicitly added to `base_tool_names`
- **AND** AChat tools (write_artifact, etc.) are exposed to CLI agents only through MCP Bridge

## REMOVED Requirements

### Requirement: ClaudeCodeAdapter SHALL bridge SDK tool approvals

~~ClaudeCodeAdapter MUST use `@anthropic-ai/claude-agent-sdk` and route supported tool approvals through AChat path checks, pending writes, and command blacklist policy.~~

**Removed**: Replaced by CLI subprocess mode. The `claude` CLI with `--permission-mode bypassPermissions` handles tool approval internally. AChat's `canUseTool` bridge is no longer applicable.

### Requirement: CodexAdapter SHALL use the Codex SDK

~~CodexAdapter MUST use `@openai/codex-sdk` `runStreamed()` rather than treating CLI spawn as the primary integration path.~~

**Removed**: Replaced by CLI subprocess mode. The `codex app-server --listen stdio://` JSON-RPC path is the primary integration.

### Requirement: SDK runtime configuration SHALL be isolated

~~CodexAdapter MUST set `CODEX_HOME` and `CODEX_SQLITE_HOME` to AChat-managed data paths and strip unrelated external `CODEX_*` variables except certificate configuration.~~

**Removed**: With CLI mode, Codex manages its own home directory. AChat only strips internal runtime markers from the child environment; user-level Codex configuration is preserved.

### Requirement: Codex Base URL SHALL be Responses compatible

~~CodexAdapter MUST only accept Codex/Responses-compatible endpoints for `apiBaseUrl`; Chat Completions-only providers such as DeepSeek MUST be rejected.~~

**Removed**: With CLI mode, base URL configuration is handled by the user's Codex CLI configuration. AChat does not validate or route it.
