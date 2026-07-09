## ADDED Requirements

### Requirement: External MCP servers SHALL run outside the workspace sandbox

External MCP servers (stdio and SSE) execute arbitrary code or access external networks. They are NOT subject to AChat's workspace path isolation, Bash command blacklist, or sandbox quota. Users MUST be explicitly warned of this trust boundary.

#### Scenario: User registers a stdio MCP server
- **WHEN** the user registers a stdio MCP server in the management panel
- **THEN** the UI displays a warning: "MCP servers run outside the AChat sandbox with application-level permissions"
- **AND** the user must confirm "I trust this server" before saving

#### Scenario: stdio MCP server spawns subprocess
- **WHEN** AgentRunner starts a run with a stdio MCP server enabled
- **THEN** the MCP server subprocess is spawned with the configured command/args/env
- **AND** the subprocess is NOT subject to Bash command blacklist
- **AND** the subprocess runs with the application process's permissions

### Requirement: stdio MCP server subprocesses SHALL be cleaned up on run end

All stdio MCP server subprocesses MUST be terminated when the run ends (normally or aborted). Process tree cleanup SHALL follow the same pattern as CLI adapter subprocess management.

#### Scenario: Run ends normally
- **WHEN** the ReAct loop completes and `execute_simple_run()` returns
- **THEN** all MCP client connections are closed
- **AND** stdio subprocesses are terminated via process tree kill

#### Scenario: Run is aborted
- **WHEN** `cancel_event` is set during the run
- **THEN** the `finally` block closes all MCP connections
- **AND** stdio subprocesses are killed (not just terminated — full process tree kill)

### Requirement: ask-trust MCP tools SHALL gate execution through pending approval

MCP servers with `trust='ask'` SHALL require user approval on first tool call per conversation. The approval mechanism SHALL reuse the existing `await_pending_decision` infrastructure.

#### Scenario: First MCP tool call in a conversation
- **WHEN** an `ask`-trust MCP tool is called for the first time in conversation C
- **THEN** a pending MCP call is registered in the `pending_mcp_calls` store
- **AND** an `mcp_call.pending` SSE event is published
- **AND** the tool execution is suspended until the user approves or rejects

#### Scenario: Subsequent calls to the same tool in the same conversation
- **WHEN** the user has already approved `mcp__server__tool` in conversation C
- **AND** the LLM calls the same tool again
- **THEN** the call proceeds without a new approval prompt

#### Scenario: Run is aborted while waiting for MCP approval
- **WHEN** `cancel_event` is set while a MCP tool call is pending approval
- **THEN** the pending entry is cleaned up
- **AND** the `await_pending_decision` returns the cancelled value
