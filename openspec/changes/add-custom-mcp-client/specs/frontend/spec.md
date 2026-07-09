## ADDED Requirements

### Requirement: Sidebar SHALL include MCP server management panel

The left sidebar SHALL include a MCP entry in the icon rail and a corresponding content panel (`McpServerLibrary`), following the same pattern as `SkillLibrary` and `AgentLibrary`.

#### Scenario: User opens MCP panel
- **WHEN** the user clicks the MCP icon in the sidebar rail
- **THEN** the MCP server library panel is displayed
- **AND** it lists all registered MCP servers with name, transport label, and status

#### Scenario: User creates a new MCP server
- **WHEN** the user clicks the "Add Server" button
- **THEN** a dialog opens with fields for name, transport (stdio/sse), and transport-specific fields
- **AND** stdio fields include command, args (array), env (key-value)
- **AND** sse fields include url and headers (key-value)
- **AND** a trust level selector defaults to 'ask'
- **AND** a warning text explains that MCP servers run outside the AChat sandbox

### Requirement: MCP server cards SHALL display status and actions

Each MCP server card in the library SHALL display the server name, transport type badge, enabled/disabled toggle, and hover actions (edit, test connection, delete).

#### Scenario: Edit MCP server
- **WHEN** the user clicks the edit button on a server card
- **THEN** the edit dialog opens with the current values pre-filled
- **AND** sensitive fields (headers/env values) are masked

#### Scenario: Test MCP server connection
- **WHEN** the user clicks the "Test Connection" button
- **THEN** the backend establishes a temporary connection
- **AND** returns the list of discovered tools (name + description)
- **AND** the tool preview is displayed in the UI
- **AND** the connection is closed after preview

#### Scenario: Delete MCP server
- **WHEN** the user clicks delete and confirms
- **THEN** the server is removed from the database
- **AND** agents referencing it have the ID removed from their `mcp_server_ids`

### Requirement: MCP call approval SHALL be displayed in the chat

When an `ask`-trust MCP tool requires approval, the frontend SHALL display a pending MCP call card in the message stream, similar to pending writes and pending bash commands.

#### Scenario: Pending MCP call appears
- **WHEN** an `mcp_call.pending` SSE event arrives
- **THEN** a card is rendered showing the tool name (`mcp__server__tool`), arguments, and server trust level
- **AND** Approve and Reject buttons are displayed

#### Scenario: User approves MCP call
- **WHEN** the user clicks Approve
- **THEN** a `POST /api/pending/mcp/:id/approve` request is sent
- **AND** the card updates to show "Approved"
- **AND** the tool result appears in the message stream

#### Scenario: User rejects MCP call
- **WHEN** the user clicks Reject
- **THEN** a `POST /api/pending/mcp/:id/reject` request is sent
- **AND** the card updates to show "Rejected"
- **AND** the tool result shows an error
