# Tools

## ADDED Requirements

### Requirement: Management tools SHALL be registered and guide-agent-only

AChat SHALL register 7 management tools in the tool registry: `manage_agents`, `manage_skills`, `manage_mcp`, `manage_documents`, `manage_memory`, `manage_profile`, `manage_conversations`. Each tool SHALL accept an `action` parameter to dispatch to the appropriate sub-operation. Management tools SHALL only be injected into guide agents (`is_guide=true`); AgentRunner SHALL filter them out for non-guide agents even if mistakenly listed in `tool_names`. All management tool handlers SHALL use `ToolContext.user_id` for data isolation.

#### Scenario: manage_agents creates a custom agent
- **WHEN** the guide agent calls `manage_agents(action=create, name="Python 程序员", adapter_name="custom", model_provider="deepseek", model_id="deepseek-v4-flash", tool_names=[...])`
- **THEN** the tool handler creates a new custom agent owned by `ToolContext.user_id`
- **AND** returns the serialized agent row.

#### Scenario: manage_memory lists long-term memories
- **WHEN** the guide agent calls `manage_memory(action=list, memory_type="long_term")`
- **THEN** the tool handler returns all long-term memories for `ToolContext.user_id`
- **AND** excludes other users' memories.

#### Scenario: manage_memory optimizes memories with a user-confirmed plan
- **WHEN** the guide agent calls `manage_memory(action=optimize, plan={delete_ids: [...], merge_groups: [...], update_ids: [...]})`
- **THEN** the tool handler deletes the specified memories, creates merged memories with embeddings, and updates attributes
- **AND** returns a summary of the operations performed.

#### Scenario: manage_conversations searches messages
- **WHEN** the guide agent calls `manage_conversations(action=search, query="worktree")`
- **THEN** the tool handler calls `search_service.search_messages` scoped to `ToolContext.user_id`
- **AND** returns matching messages with conversation title, role, time, and snippet.

#### Scenario: Non-guide agent attempts to use a management tool
- **WHEN** a non-guide agent's `tool_names` includes `manage_agents`
- **THEN** AgentRunner filters `manage_agents` out during tool injection
- **AND** the tool is not available at runtime.

### Requirement: Management tools SHALL enforce confirm parameter for destructive actions

Management tool handlers SHALL require a `confirm=true` parameter for `delete` actions and batch operations. If `confirm` is not `true`, the handler SHALL return an error instructing the LLM to confirm via `ask_user` first. This is a hard fallback to the system prompt's soft requirement.

#### Scenario: Delete without confirm
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=false)`
- **THEN** the tool handler returns an error message
- **AND** the deletion does NOT occur.

#### Scenario: Delete with confirm
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=true)`
- **AND** agent X is a non-builtin agent owned by the current user
- **THEN** the tool handler deletes agent X
- **AND** returns a success summary.

### Requirement: Management tools SHALL emit guide_side_effect events on success

When a management tool successfully executes a create/update/delete/refresh operation, the tool handler SHALL emit a `guide_side_effect` SSE event with `target` and `action` fields so the frontend can refresh the corresponding panel.

#### Scenario: Agent created successfully
- **WHEN** `manage_agents(action=create)` succeeds
- **THEN** the tool handler emits `guide_side_effect` with `target='agents'`, `action='create'`.

#### Scenario: Document refreshed successfully
- **WHEN** `manage_documents(action=refresh, document_id=X)` succeeds
- **THEN** the tool handler emits `guide_side_effect` with `target='documents'`, `action='refresh'`.
