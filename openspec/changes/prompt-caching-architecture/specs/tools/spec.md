## ADDED Requirements

### Requirement: Tool definitions SHALL support deferred loading (Phase 2 DEFERRED)

The `ToolDef` dataclass MAY include an `is_mcp: bool` field (default `False`) to distinguish built-in tools from MCP-provided tools. When deferred loading is enabled, `ToolRegistry.get_tool_defs(full=False)` MUST return stub definitions (name + description only) for MCP tools, while built-in tools always return full schemas.

**Note**: This requirement is marked as Phase 2 DEFERRED. Implementation is not required for this change to be considered complete. The spec is captured here for forward-looking design alignment.

#### Scenario: MCP tool with deferred loading
- **WHEN** `get_tool_defs(full=False)` is called and a tool has `is_mcp=True`
- **THEN** the returned definition contains `name`, `description`, and an empty `parameters` schema
- **AND** the definition includes `_defer_loading: True` marker

#### Scenario: Built-in tool always returns full schema
- **WHEN** `get_tool_defs(full=False)` is called and a tool has `is_mcp=False`
- **THEN** the returned definition contains the complete JSON schema including `parameters`

### Requirement: Agent SHALL be able to search for deferred tools (Phase 2 DEFERRED)

The system MAY provide a `tool_search` tool that allows the Agent to search for MCP tools by semantic query and receive their full schemas on demand.

**Note**: This requirement is marked as Phase 2 DEFERRED. Implementation is not required for this change to be considered complete.

#### Scenario: Agent searches for a specific tool
- **WHEN** the Agent calls `tool_search` with a query describing the desired functionality
- **THEN** the tool returns matching MCP tool definitions with full schemas
- **AND** the Agent can then call the matched tool in the same turn
