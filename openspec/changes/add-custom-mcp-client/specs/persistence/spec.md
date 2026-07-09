## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The database schema MUST persist agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, app settings, and MCP server configurations.

#### Scenario: New conversation is created
- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** messages and runs can reference the conversation id.

#### Scenario: MCP server is created
- **WHEN** a user registers a new MCP server via the API
- **THEN** a row is inserted into the `mcp_servers` table
- **AND** agents can reference it via `mcp_server_ids`

## ADDED Requirements

### Requirement: mcp_servers table SHALL store global MCP server definitions

A new `mcp_servers` table SHALL store MCP server configurations with the following columns: `id` (PK), `name` (unique, `[a-z0-9_]`), `transport` ('stdio' | 'sse'), `command`, `args` (JSON array), `env` (JSON object), `url`, `headers` (JSON object), `trust` ('always' | 'ask'), `enabled` (boolean), `created_at` (integer).

#### Scenario: stdio MCP server registration
- **WHEN** a user registers a stdio MCP server named `filesystem`
- **THEN** a row is inserted with `transport='stdio'`, `command='npx'`, `args=['-y', '@modelcontextprotocol/server-filesystem', '/tmp']`
- **AND** `trust` defaults to `'ask'`

#### Scenario: SSE MCP server registration
- **WHEN** a user registers an SSE MCP server named `remote-api`
- **THEN** a row is inserted with `transport='sse'`, `url='https://example.com/mcp'`, `headers={"Authorization": "Bearer ${API_KEY}"}`

### Requirement: agents table SHALL include mcp_server_ids column

The `agents` table SHALL include a `mcp_server_ids` column (JSON array, default `[]`) listing the MCP server IDs this agent has enabled. This field is only meaningful for Custom adapter agents.

#### Scenario: Agent enables MCP servers
- **WHEN** an agent is created or updated with `mcp_server_ids=["mcp_fs", "mcp_github"]`
- **THEN** the `mcp_server_ids` column stores the array
- **AND** at run time, AgentRunner resolves and connects to those servers

#### Scenario: CLI agent ignores mcp_server_ids
- **WHEN** a Claude Code or Codex agent has `mcp_server_ids` set
- **THEN** the field is ignored (CLI adapters manage their own MCP connections)
