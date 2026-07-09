## ADDED Requirements

### Requirement: Custom agent SHALL connect to external MCP servers as a client

When a Custom agent has `mcp_server_ids` referencing enabled MCP servers, AgentRunner SHALL establish MCP client connections (stdio or SSE) at run start, discover available tools via `listTools()`, and inject them into the ReAct loop alongside built-in tools.

#### Scenario: Agent with MCP servers starts a run

- **WHEN** a Custom agent with `mcp_server_ids=["mcp_fs", "mcp_github"]` starts a run
- **THEN** AgentRunner connects to both MCP servers
- **AND** discovers their tools via `listTools()`
- **AND** injects the tools as OpenAI function-calling declarations with namespaced names
- **AND** the LLM can choose between built-in tools and MCP tools

#### Scenario: MCP server connection fails

- **WHEN** one MCP server fails to connect (e.g., command not found, network error)
- **THEN** that server's tools are marked unavailable
- **AND** a warning is logged
- **AND** other MCP servers and built-in tools remain available
- **AND** the run continues without crashing

### Requirement: MCP tool calls SHALL be routed by namespaced prefix

The ReAct loop MUST route tool execution by tool name prefix: tools named `mcp__<serverName>__<toolName>` SHALL be dispatched to the corresponding MCP client's `callTool()` method; all other tool names SHALL be dispatched to the built-in `tool_registry`.

#### Scenario: LLM calls an MCP tool

- **WHEN** the LLM returns a tool call with name `mcp__filesystem__read_file`
- **THEN** the ReAct loop routes the call to the MCP client for server `filesystem`
- **AND** the client calls `callTool("read_file", args)`
- **AND** the result is yielded as a `tool.result` StreamEvent

#### Scenario: LLM calls a built-in tool

- **WHEN** the LLM returns a tool call with name `fs_read`
- **THEN** the ReAct loop routes the call to `tool_registry.execute_with_hooks()`
- **AND** behavior is identical to runs without MCP

### Requirement: MCP client lifecycle SHALL be per-run

MCP client connections SHALL be established at the start of `execute_simple_run()` and torn down (all connections closed, stdio subprocesses killed) in the `finally` block when the run ends or aborts.

#### Scenario: Run completes normally

- **WHEN** the ReAct loop finishes (LLM stops calling tools or max turns reached)
- **THEN** all MCP client connections are closed
- **AND** stdio MCP server subprocesses are terminated and cleaned up

#### Scenario: Run is aborted mid-execution

- **WHEN** the run's `cancel_event` is set (user aborts)
- **THEN** all MCP client connections are closed in the `finally` block
- **AND** stdio MCP server subprocesses are killed (process tree)

### Requirement: MCP server configuration SHALL be globally defined and per-agent opted-in

MCP servers SHALL be defined once in the `mcp_servers` table and referenced by agents via `mcp_server_ids`. This mirrors the `tool_names` pattern: define-once, reuse across agents, secrets managed centrally.

#### Scenario: Two agents share the same MCP server

- **WHEN** agent A and agent B both reference `mcp_server_ids=["mcp_github"]`
- **THEN** both agents connect to the same MCP server configuration
- **AND** the GitHub API key (in headers/env) is stored once in the `mcp_servers` row

### Requirement: MCP tools SHALL use namespaced naming

External MCP tools SHALL be named `mcp__<serverName>__<toolName>` to avoid conflicts with built-in tools and ensure global uniqueness across MCP servers.

#### Scenario: Two MCP servers expose tools with the same name

- **WHEN** server `github` and server `gitlab` both expose a tool named `create_issue`
- **THEN** the tools are named `mcp__github__create_issue` and `mcp__gitlab__create_issue`
- **AND** no name collision occurs

### Requirement: ask-trust MCP tools SHALL require per-tool-per-conversation approval

When a MCP server has `trust='ask'`, its tools SHALL require user approval on first call within a conversation. After approval, the same tool is exempt for the remainder of that conversation. Rejected calls return `isError=true`.

#### Scenario: First call to an ask-trust MCP tool

- **WHEN** the LLM calls `mcp__filesystem__write_file` for the first time in conversation C
- **AND** the `filesystem` server has `trust='ask'`
- **THEN** a pending MCP call is registered
- **AND** an `mcp_call.pending` SSE event is published
- **AND** the tool execution waits for user approval

#### Scenario: User approves the MCP tool call

- **WHEN** the user approves the pending MCP call
- **THEN** the tool is executed via `callTool()`
- **AND** subsequent calls to `mcp__filesystem__write_file` in the same conversation proceed without approval

#### Scenario: User rejects the MCP tool call

- **WHEN** the user rejects the pending MCP call
- **THEN** the tool result is `{"error": "User denied MCP tool call"}`
- **AND** `isError=true` is set on the `tool.result` event
- **AND** subsequent calls to the same tool in the same conversation are auto-rejected

### Requirement: always-trust MCP tools SHALL execute without approval

When a MCP server has `trust='always'`, its tools SHALL execute directly without any approval gate.

#### Scenario: always-trust tool call

- **WHEN** the LLM calls a tool from a server with `trust='always'`
- **THEN** the tool is executed immediately via `callTool()`
- **AND** no pending MCP call is registered

### Requirement: MCP server test connection SHALL preview tools

The API SHALL provide a test connection endpoint that establishes a temporary MCP connection, calls `listTools()`, returns the tool preview, and closes the connection.

#### Scenario: User tests a stdio MCP server

- **WHEN** the user clicks "Test Connection" for a stdio MCP server
- **THEN** the backend spawns the MCP server subprocess
- **AND** calls `listTools()` and returns the tool names and descriptions
- **AND** closes the connection and kills the subprocess

### Requirement: MCP secrets SHALL be stored in DB and masked in API responses

MCP server `headers` and `env` values containing secrets SHALL be stored in the database (no keychain). API list responses SHALL mask sensitive values (show last 4 characters). Values SHALL support `${ENV_NAME}` placeholder syntax to reference environment variables without storing plaintext.

#### Scenario: API returns MCP server list

- **WHEN** the frontend fetches `GET /api/mcp/servers`
- **THEN** `headers` and `env` values matching known secret patterns (length > 20, not `${...}`) are masked as `****<last4>`

#### Scenario: MCP server uses env placeholder for API key

- **WHEN** a MCP server header is `{"Authorization": "Bearer ${GITHUB_TOKEN}"}`
- **THEN** at connection time, `${GITHUB_TOKEN}` is replaced with the value from `os.environ`
- **AND** the plaintext token is never stored in the database
