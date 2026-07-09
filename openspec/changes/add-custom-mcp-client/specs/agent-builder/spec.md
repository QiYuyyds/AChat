## MODIFIED Requirements

### Requirement: User-created agents SHALL default to Custom adapter

New agents MUST default to `adapterName='custom'` unless the user selects Claude Code or Codex SDK adapter. Custom agents with MCP servers enabled SHALL connect to those servers at run time.

#### Scenario: User opens create dialog
- **WHEN** no existing agent is being edited
- **THEN** adapter kind defaults to Custom
- **AND** provider defaults to DeepSeek.

#### Scenario: Custom agent with MCP servers
- **WHEN** a Custom agent is created with `mcp_server_ids` referencing enabled servers
- **THEN** the agent builder saves the selection
- **AND** at run time, the agent connects to those MCP servers

## ADDED Requirements

### Requirement: Agent builder SHALL provide MCP server selection for Custom agents

The agent create/edit dialog SHALL include a MCP server multi-select section, visible only when `adapterName='custom'`. The section SHALL list all enabled MCP servers with checkboxes, mirroring the `toolNames` selection pattern.

#### Scenario: Custom agent MCP server selection
- **WHEN** the user is creating or editing a Custom agent
- **THEN** a MCP server multi-select section is displayed
- **AND** each enabled MCP server appears as a checkbox with its name and transport label
- **AND** checking a server adds its ID to `mcp_server_ids`

#### Scenario: CLI agent hides MCP server selection
- **WHEN** the user selects Claude Code or Codex adapter
- **THEN** the MCP server selection section is hidden
- **AND** `mcp_server_ids` is set to `[]`

#### Scenario: No MCP servers configured
- **WHEN** no MCP servers exist in the database
- **THEN** the MCP server section displays an empty state with a link to the MCP management panel
