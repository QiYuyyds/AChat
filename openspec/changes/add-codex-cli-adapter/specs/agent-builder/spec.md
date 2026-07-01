## MODIFIED Requirements

### Requirement: User-created agents SHALL default to Custom adapter

New agents MUST default to `adapterName='custom'` unless the user selects the Codex CLI adapter. The Claude Code adapter option is no longer available.

#### Scenario: User opens create dialog
- **WHEN** no existing agent is being edited
- **THEN** adapter kind defaults to Custom
- **AND** provider defaults to DeepSeek.

#### Scenario: User switches to Codex adapter
- **WHEN** the user selects `adapterKind='codex'`
- **THEN** the form shows codex-specific fields (optional `executable_path`, optional `model` override)
- **AND** hides provider, API key, base URL, and per-agent tool checkboxes.

### Requirement: Custom agents SHALL require provider and model

Custom agents MUST have `modelProvider` and a non-empty `modelId`; Codex CLI agents MAY optionally specify a `model` override but do not require `modelProvider`.

#### Scenario: User clears custom model id
- **WHEN** adapter kind is Custom
- **THEN** form submission is rejected.

#### Scenario: Codex agent without model override
- **WHEN** adapter kind is Codex and `modelId` is empty
- **THEN** form submission is accepted
- **AND** codex CLI uses its default model.

### Requirement: Codex agents SHALL use built-in tool sets via MCP

Codex CLI agents MUST persist `toolNames` as the list of AChat tools to expose through the MCP bridge (via `AGENTHUB_ALLOWED_TOOLS`). The tools are not consumed via AChat `toolRegistry` function calling — they are injected as MCP servers in the per-task `config.toml`.

#### Scenario: User switches from Custom to Codex
- **WHEN** the form is submitted with `adapterKind='codex'`
- **THEN** the saved agent's `toolNames` list is passed to the MCP bridge as `AGENTHUB_ALLOWED_TOOLS`
- **AND** codex can call those tools via MCP protocol.

#### Scenario: Codex agent in Orchestrator sub-task
- **WHEN** the Orchestrator dispatches a sub-task requiring `report_task_result`
- **THEN** `AgentRunner` injects `report_task_result` into `toolNames`
- **AND** the MCP bridge registers it for codex to call.

## REMOVED Requirements

### Requirement: Codex agent configuration SHALL reject unsupported base URLs

**Reason**: Codex CLI adapter does not consume `apiBaseUrl`. Codex CLI resolves its API endpoint internally via `~/.codex/auth.json` and environment variables. The base URL validation (`codex-compat.ts`) is no longer applicable.

**Migration**: Remove `apiBaseUrl` field from codex agent configuration UI. Delete `src/shared/codex-compat.ts`.

### Requirement: API key hints SHALL match adapter fallback

**Reason**: Codex CLI adapter does not consume `apiKey` from the agent or app settings. Codex CLI authenticates via `~/.codex/auth.json` (symlinked into per-task CODEX_HOME). API key hint logic for codex is no longer needed.

**Migration**: Remove codex-specific key resolution from `_pick_settings_key` in `agent_runner.py`. Remove codex key fallback hint from the agent builder UI. Custom adapter key resolution is unchanged.
