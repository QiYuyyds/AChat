## ADDED Requirements

### Requirement: Cache Coverage for Tool Execution Paths

All tool execution and service call sites that look up Workspace by `conversation_id` SHALL use `get_workspace_cached()` instead of direct PostgreSQL queries, ensuring cache hits avoid a full DB round trip.

The following call sites SHALL be covered:
- `tools/bash.py` — bash command execution
- `tools/deploy_workspace.py` — workspace deployment
- `tools/fs_write.py` — file write tool
- `tools/fs_edit.py` — file edit tool
- `services/hooks/tool_approval.py` — tool approval hook
- `services/fs_service.py` — `get_workspace_for_conversation()`
- `services/deploy_command_service.py` — deploy command handler
- `services/attachment_service.py` — attachment upload/retrieval

#### Scenario: bash tool uses cached Workspace

- **WHEN** the `bash` tool is invoked and Redis is available with `workspace:{conversation_id}` in cache
- **THEN** the tool SHALL receive the Workspace from Redis cache without querying PostgreSQL
- **AND** the cache lookup latency SHALL be ~1ms

#### Scenario: bash tool degrades gracefully

- **WHEN** the `bash` tool is invoked and Redis is unavailable
- **THEN** `get_workspace_cached()` SHALL query PostgreSQL directly and return the Workspace
- **AND** no Redis operations SHALL be attempted

#### Scenario: fs_write tool uses cached Workspace

- **WHEN** the `fs_write` tool is invoked and Redis is available
- **THEN** the Workspace lookup SHALL use the Redis cache
- **AND** the `Conversation` lookup for `fs_write_approval_mode` SHALL remain a direct DB query (Conversation is not cached)

### Requirement: Cache Coverage for Agent Lookups in Service Paths

Single-Agent lookups in service paths SHALL use `get_agent_cached()` instead of direct PostgreSQL queries. The following call sites SHALL be covered:
- `agent_runner.py` — `_get_agent_model_limit()` for auto-compact context window lookup
- `conversation_service.py` — `maybe_generate_summary()` agent model config lookup
- `conversation_context.py` — agent profile lookup in `build_history_for()`
- `context_compaction_service.py` — agent system_prompt lookup
- `hooks/checkpoint.py` — `checkpoint_enabled` flag lookup

Batch Agent queries (`Agent.id.in_(...)`) are excluded — the cache supports single-ID lookups only.

#### Scenario: _get_agent_model_limit uses cached Agent

- **WHEN** `_get_agent_model_limit(agent_id)` is called and Redis is available
- **THEN** the Agent SHALL be loaded from Redis cache if present
- **AND** the model limits SHALL be derived from the cached Agent's `model_provider` and `model_id`

#### Scenario: maybe_generate_summary uses cached Agent

- **WHEN** `maybe_generate_summary()` looks up the agent for model config and Redis is available
- **THEN** the Agent SHALL be loaded from Redis cache if present
- **AND** the `model_provider`, `model_id`, `api_key`, and `api_base_url` SHALL be read from the cached instance

### Requirement: Direct UPDATE for Synchronous Parts Persistence

The `_update_message_parts()` function and the synchronous persistence paths in `persist_event()` (`message.end`, `run.usage`, `message.usage`) SHALL use a direct SQL `UPDATE` statement instead of SELECT-then-ORM-assign, reducing each call from 2 DB round trips to 1.

#### Scenario: _update_message_parts uses direct UPDATE

- **WHEN** `_update_message_parts(message_id, parts)` is called (synchronous fallback path)
- **THEN** the system SHALL execute `UPDATE message SET parts_list = :parts WHERE id = :message_id`
- **AND** no prior `SELECT` query SHALL be issued
- **AND** exactly 1 DB round trip SHALL occur

#### Scenario: message.end final flush uses direct UPDATE

- **WHEN** `persist_event()` processes a `message.end` event
- **THEN** the final parts flush SHALL use a direct `UPDATE Message SET status = 'complete', parts_list = :parts WHERE id = :message_id`
- **AND** no prior `SELECT` query SHALL be issued

#### Scenario: run.usage persistence uses direct UPDATE

- **WHEN** `persist_event()` processes a `run.usage` event
- **THEN** the usage update SHALL use a direct `UPDATE agent_runs SET usage = :usage WHERE id = :run_id`
- **AND** no prior `SELECT` query SHALL be issued

### Requirement: Cache Invalidation on MCP Server Deletion

When an MCP server is deleted, the system SHALL invalidate the Agent cache for every Agent whose `mcp_server_ids_list` was modified during the deletion cleanup.

#### Scenario: MCP server delete invalidates affected Agents

- **WHEN** an MCP server is deleted and Agent rows are updated to remove the server_id from their `mcp_server_ids_list`
- **THEN** for each modified Agent, the system SHALL call `invalidate_agent_cache(agent.id)` to `DEL agent:{agent_id}` from Redis
- **AND** the next lookup of that Agent SHALL miss the cache and re-fetch from PostgreSQL

#### Scenario: MCP server delete with Redis unavailable

- **WHEN** an MCP server is deleted and Redis is unavailable
- **THEN** the `invalidate_agent_cache()` call SHALL be a no-op
- **AND** the PostgreSQL UPDATE SHALL still execute correctly

### Requirement: consume_stream Deduplication

The `consume_stream()` function SHALL route `artifact.create` and `deploy.status` part-persistence through the existing `_persist_or_stream()` helper instead of inline XADD+fallback logic.

#### Scenario: artifact.create uses _persist_or_stream

- **WHEN** an `artifact.create` event adds an `artifact_ref` part to `parts_buffer` and needs to persist it
- **THEN** the persistence SHALL be routed through `_persist_or_stream(redis_client, run_id, event, parts, use_stream)`
- **AND** no inline XADD or `_update_message_parts` calls SHALL appear in the `artifact.create` handling block

#### Scenario: deploy.status uses _persist_or_stream

- **WHEN** a `deploy.status` event adds a `deploy_status` part to `parts_buffer` and needs to persist it
- **THEN** the persistence SHALL be routed through `_persist_or_stream(redis_client, run_id, event, parts, use_stream)`
- **AND** no inline XADD or `_update_message_parts` calls SHALL appear in the `deploy.status` handling block
