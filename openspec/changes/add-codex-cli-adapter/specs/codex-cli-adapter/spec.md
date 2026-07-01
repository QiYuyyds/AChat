## ADDED Requirements

### Requirement: CodexCLIAdapter SHALL spawn codex CLI as subprocess

The adapter MUST use `asyncio.create_subprocess_exec` to spawn `codex app-server --listen stdio://` and communicate via JSON-RPC 2.0 over stdin/stdout. The adapter SHALL NOT use any SDK or direct API calls.

#### Scenario: Adapter starts a run

- **WHEN** `stream(input, cancel_event)` is called with a valid prompt
- **THEN** the adapter spawns `codex app-server --listen stdio://` as a subprocess
- **AND** sends `thread/start` followed by `thread/run` JSON-RPC requests via stdin
- **AND** begins reading events from stdout.

#### Scenario: codex executable not found

- **WHEN** the resolved codex executable path does not exist or is not executable
- **THEN** the adapter yields a `MessageEndEvent` with an error
- **AND** logs a clear message instructing the user to install `@openai/codex` CLI.

### Requirement: CodexCLIAdapter SHALL resolve executable path with priority chain

The adapter MUST resolve the codex binary path in this order: `agent.executable_path` field → `CODEX_EXECUTABLE` environment variable → `codex` on PATH.

#### Scenario: User specifies custom executable path

- **WHEN** the agent has `executable_path` set to a valid codex binary
- **THEN** the adapter uses that path for subprocess spawn
- **AND** does not search PATH.

#### Scenario: No executable path and codex on PATH

- **WHEN** neither `executable_path` nor `CODEX_EXECUTABLE` is set
- **AND** `codex` is available on PATH
- **THEN** the adapter spawns `codex` from PATH.

### Requirement: CodexCLIAdapter SHALL isolate CODEX_HOME per run

The adapter MUST create a per-run `CODEX_HOME` directory at `<dataDir>/codex-home/<run_id>/` and set it as the `CODEX_HOME` environment variable for the spawned process.

#### Scenario: Run creates isolated CODEX_HOME

- **WHEN** a codex run starts
- **THEN** a directory `<dataDir>/codex-home/<run_id>/` is created
- **AND** `auth.json` is symlinked from `~/.codex/auth.json`
- **AND** `sessions/` directory is symlinked from `~/.codex/sessions/`
- **AND** `config.toml` is copied from `~/.codex/config.toml` (if it exists).

#### Scenario: Shared auth.json token refresh

- **WHEN** codex refreshes its auth token during a run
- **THEN** the refreshed token is written to the symlinked `auth.json`
- **AND** subsequent runs see the updated token without restart.

### Requirement: CodexCLIAdapter SHALL inject MCP server config into config.toml

The adapter MUST write a `[mcp_servers.agenthub]` TOML block into the per-task `config.toml` pointing to `scripts/agenthub-codex-mcp.mjs`, with environment variables for backend URL, auth token, conversation/agent/run IDs, and allowed tools.

#### Scenario: MCP config block is written

- **WHEN** the per-task CODEX_HOME is prepared
- **THEN** `config.toml` contains a `[mcp_servers.agenthub]` table
- **AND** the table has `command="node"`, `args=["<path>/agenthub-codex-mcp.mjs"]`
- **AND** `env` includes `AGENTHUB_INTERNAL_BASE_URL`, `AGENTHUB_INTERNAL_TOOL_TOKEN`, `AGENTHUB_CONVERSATION_ID`, `AGENTHUB_AGENT_ID`, `AGENTHUB_RUN_ID`, `AGENTHUB_ALLOWED_TOOLS`
- **AND** the file mode is 0o600.

#### Scenario: Allowed tools filter MCP registration

- **WHEN** `AGENTHUB_ALLOWED_TOOLS` is set to `write_artifact,report_task_result`
- **THEN** the MCP bridge only registers those two tools
- **AND** codex cannot call tools not in the list.

### Requirement: CodexCLIAdapter SHALL translate codex events to StreamEvent

The adapter MUST translate JSON-RPC events from codex into AChat `StreamEvent` objects and yield them from the `stream()` async generator.

#### Scenario: Agent message text delta

- **WHEN** codex emits an `agent_message` event with text content delta
- **THEN** the adapter yields `PartStartEvent(type="text")` on the first delta
- **AND** yields `PartDeltaEvent(type="text.append")` on subsequent deltas.

#### Scenario: Reasoning delta

- **WHEN** codex emits a `reasoning` event with thinking content delta
- **THEN** the adapter yields `PartStartEvent(type="thinking")` on the first delta
- **AND** yields `PartDeltaEvent(type="thinking.append")` on subsequent deltas.

#### Scenario: Command execution

- **WHEN** codex emits a `command_execution` event
- **THEN** the adapter yields `ToolCallEvent` with the command details
- **AND** yields `ToolResultEvent` with the execution outcome.

#### Scenario: MCP tool call

- **WHEN** codex emits an `mcp_tool_call` event for an AgentHub MCP tool
- **THEN** the adapter yields `ToolCallEvent` with the tool name and args
- **AND** yields `ToolResultEvent` with the MCP response
- **AND** if the tool is `write_artifact`, the result triggers `artifact.create` via `consume_stream`.

#### Scenario: Turn completed

- **WHEN** codex emits a `turn.completed` event
- **THEN** the adapter yields `MessageEndEvent`
- **AND** yields `RunUsageEvent` with token usage from the event.

### Requirement: CodexCLIAdapter SHALL support cancellation

The adapter MUST monitor `cancel_event` and terminate the codex subprocess when it is set.

#### Scenario: User aborts a run

- **WHEN** `cancel_event.is_set()` becomes true during a run
- **THEN** the adapter calls `proc.terminate()`
- **AND** waits up to 5 seconds for the process to exit
- **AND** calls `proc.kill()` if it does not exit
- **AND** yields `MessageEndEvent` for any open message.

### Requirement: CodexCLIAdapter SHALL support single chat and group chat

The adapter MUST work transparently in both direct user conversations and Orchestrator-dispatched sub-tasks, because `AgentRunner.execute_simple_run` calls `adapter.stream()` the same way in both paths.

#### Scenario: Direct user conversation

- **WHEN** a user creates a conversation with a codex agent and sends a message
- **THEN** `AgentRunnerImpl.run()` → `execute_simple_run()` → `CodexCLIAdapter.stream()` is invoked
- **AND** StreamEvents flow through `consume_stream` to SSE to the frontend.

#### Scenario: Orchestrator dispatches sub-task to codex agent

- **WHEN** the Orchestrator's dispatch plan assigns a task to a codex agent
- **THEN** `run_with_args(RunArgs(agent_id=codex_agent_id, override_prompt=..., require_task_report=True))` is called
- **AND** `execute_simple_run()` → `CodexCLIAdapter.stream()` is invoked with the override prompt
- **AND** the MCP bridge exposes `report_task_result` so codex can report completion.

### Requirement: CodexCLIAdapter SHALL set cwd to workspace path

The adapter MUST set the subprocess working directory to `input.workspace_path` so that codex's built-in file and bash tools operate on the user's real project files.

#### Scenario: Local workspace mode

- **WHEN** the workspace mode is `local` and `workspace_path` is `/home/user/myproject`
- **THEN** the codex subprocess is spawned with `cwd=/home/user/myproject`
- **AND** codex's file operations apply to real files in that directory.
