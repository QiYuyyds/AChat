## MODIFIED Requirements

### Requirement: Tool definitions SHALL be registered centrally

AChat-managed tools MUST be registered through `toolRegistry` with name, description, JSON schema, and handler. External MCP tools are NOT registered in `toolRegistry`; they are discovered at run time via MCP `listTools()` and injected directly into the LLM tool declarations.

#### Scenario: Custom agent enables a tool
- **WHEN** an agent's `toolNames` includes `fs_read`
- **THEN** CustomAgentAdapter resolves the tool definition from `toolRegistry`.

#### Scenario: Custom agent enables an MCP server
- **WHEN** an agent's `mcp_server_ids` includes `mcp_filesystem`
- **THEN** AgentRunner connects to the MCP server and calls `listTools()`
- **AND** the discovered tools are named `mcp__filesystem__<toolName>`
- **AND** they are NOT registered in `toolRegistry`
- **AND** they are merged into the LLM tool declarations at run time

### Requirement: Tool execution SHALL route by name prefix

The ReAct loop MUST route tool execution by tool name: names starting with `mcp__` SHALL be dispatched to the MCP client manager; all other names SHALL be dispatched to `tool_registry.execute_with_hooks()`.

#### Scenario: Built-in tool execution
- **WHEN** the ReAct loop receives a tool call with name `fs_write`
- **THEN** it dispatches to `tool_registry.execute_with_hooks("fs_write", args, ctx)`
- **AND** the result is yielded as a `tool.result` event

#### Scenario: MCP tool execution
- **WHEN** the ReAct loop receives a tool call with name `mcp__github__create_issue`
- **THEN** it dispatches to `mcp_manager.call_tool("mcp__github__create_issue", args)`
- **AND** the MCP client calls `callTool("create_issue", args)` on the `github` server
- **AND** the result is yielded as a `tool.result` event with `toolName="mcp__github__create_issue"`
