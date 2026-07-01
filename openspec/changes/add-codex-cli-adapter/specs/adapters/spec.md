## REMOVED Requirements

### Requirement: ClaudeCodeAdapter SHALL bridge SDK tool approvals

**Reason**: Claude Code SDK integration is being removed entirely. The project no longer ships a ClaudeCodeAdapter.

**Migration**: Agents previously using `adapterName='claude-code'` MUST be migrated to either `codex` (CLI spawn) or `custom` (OpenAI-compatible SDK) adapter.

### Requirement: CodexAdapter SHALL use the Codex SDK

**Reason**: Codex integration is being rewritten from SDK mode (`@openai/codex-sdk` `runStreamed()`) to CLI spawn mode (`codex app-server --listen stdio://`). The new behavior is defined in the `codex-cli-adapter` capability spec.

**Migration**: Agents with `adapterName='codex'` continue to work but now spawn the codex CLI binary instead of using the SDK. Users must install `@openai/codex` CLI on PATH (npm version, not Store app).

### Requirement: Codex Base URL SHALL be Responses compatible

**Reason**: The CLI spawn adapter does not consume `apiBaseUrl` — codex CLI reads its own auth from `~/.codex/auth.json` and resolves the API endpoint internally. Base URL validation is no longer applicable.

**Migration**: Remove `apiBaseUrl` from codex agent configuration. Codex agents no longer need per-agent API keys or base URLs.

### Requirement: SDK runtime configuration SHALL be isolated

**Reason**: The SDK-specific isolation of `CODEX_HOME` and `CODEX_SQLITE_HOME` with env stripping is replaced by the CLI adapter's per-run CODEX_HOME isolation strategy (symlink auth, copy config, inject MCP block). The new behavior is defined in the `codex-cli-adapter` capability spec under "SHALL isolate CODEX_HOME per run".

**Migration**: Runtime isolation is now handled by `codex_home.py` module, not by environment variable stripping in `build_adapter_input`.

## MODIFIED Requirements

### Requirement: Adapters SHALL translate provider output to StreamEvent

Each adapter MUST expose `stream(input, signal)` and yield only AChat `StreamEvent` objects to the application layer. This requirement now applies to `CustomAgentAdapter`, `MockAdapter`, and `CodexCLIAdapter` (formerly `CodexAdapter` using SDK).

#### Scenario: Custom model emits tool calls
- **WHEN** Chat Completions streaming returns function tool call deltas
- **THEN** CustomAgentAdapter accumulates arguments
- **AND** emits AChat `tool.call` and `tool.result` events.

#### Scenario: Codex CLI emits agent message
- **WHEN** codex `app-server` emits an `agent_message` JSON-RPC event
- **THEN** CodexCLIAdapter yields `PartStartEvent` and `PartDeltaEvent` StreamEvents
- **AND** `consume_stream` persists them to the message parts list.

### Requirement: SDK adapters SHALL expose allowlisted AChat tools through MCP

Codex CLI adapter MUST expose allowlisted AChat tools through the `agenthub-codex-mcp.mjs` MCP bridge, configured via per-task `config.toml`. The `tool_names` from `AdapterInput` are passed to the MCP bridge as `AGENTHUB_ALLOWED_TOOLS` environment variable.

#### Scenario: Codex creates and deploys an artifact or workspace build
- **WHEN** Codex calls the AChat MCP `write_artifact`, `deploy_artifact`, or `deploy_workspace` tool
- **THEN** the MCP bridge forwards the call to the backend internal API
- **AND** the adapter translates the MCP result into `artifact.create` or `deploy.status` StreamEvents via `consume_stream`.

#### Scenario: Codex agent asks a structured user question
- **WHEN** Codex calls the AChat MCP `ask_user` tool
- **THEN** AChat routes it through the shared pending question flow.

#### Scenario: Codex agent reports task result in group chat
- **WHEN** Codex calls the AChat MCP `report_task_result` tool
- **THEN** the MCP bridge forwards the call to the backend
- **AND** `consume_stream` extracts the task report from the `tool.result` event.
