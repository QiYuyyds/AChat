## MODIFIED Requirements

### Requirement: Agent builder SHALL expose adapter-specific fields

Create/edit agent UI MUST show provider, model, tool, key, and base URL fields according to selected adapter semantics. For Codex CLI adapter, the UI shows `executable_path` (optional) and `model` (optional override) instead of provider/key/base URL.

#### Scenario: User selects Codex adapter
- **WHEN** `adapterKind='codex'`
- **THEN** provider, API key, base URL, and per-agent tool checkboxes are hidden
- **AND** an optional `executable_path` field appears for custom codex binary path
- **AND** an optional `model` field appears for codex model override
- **AND** a note explains that codex authenticates via `~/.codex/auth.json`.

#### Scenario: User selects Custom adapter
- **WHEN** `adapterKind='custom'`
- **THEN** provider, model, API key, base URL, and tool checkboxes are shown
- **AND** the form behaves as before.

#### Scenario: Claude Code option is removed
- **WHEN** the user opens the adapter kind selector
- **THEN** only `custom` and `codex` options are available
- **AND** `claude-code` is not listed.

## ADDED Requirements

### Requirement: Frontend SHALL display codex CLI run status

The frontend MUST render codex CLI tool calls (command execution, MCP tool calls) as tool call cards in the chat stream, indistinguishable from custom adapter tool calls.

#### Scenario: Codex runs a bash command
- **WHEN** the adapter yields a `ToolCallEvent` for a codex `command_execution`
- **THEN** the chat renders a tool call card showing the command
- **AND** a `ToolResultEvent` card showing the exit code and output.

#### Scenario: Codex calls write_artifact via MCP
- **WHEN** the adapter yields `ToolCallEvent` for `write_artifact` followed by `ToolResultEvent`
- **THEN** the chat renders the tool call
- **AND** `consume_stream` emits `artifact.create` which renders the artifact preview card.
