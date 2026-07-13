## 1. Workspace Cache Coverage — Tool Execution Paths

- [x] 1.1 `tools/bash.py` — replace `select(Workspace).where(...)` with `await get_workspace_cached(ctx.conversation_id)`
- [x] 1.2 `tools/deploy_workspace.py` — replace `select(Workspace).where(...)` with `await get_workspace_cached(conversation_id)`
- [x] 1.3 `tools/fs_write.py` — replace Workspace lookup with `await get_workspace_cached(ctx.conversation_id)` (keep Conversation query as-is)
- [x] 1.4 `tools/fs_edit.py` — replace Workspace lookup with `await get_workspace_cached(ctx.conversation_id)` (keep Conversation query as-is)
- [x] 1.5 `services/hooks/tool_approval.py` — replace `select(Workspace).where(...)` with `await get_workspace_cached(ctx.conversation_id)`
- [x] 1.6 `services/fs_service.py` — `get_workspace_for_conversation()` use `await get_workspace_cached(conversation_id)`
- [x] 1.7 `services/deploy_command_service.py` — replace `select(Workspace).where(...)` with `await get_workspace_cached(conversation_id)`
- [x] 1.8 `services/attachment_service.py` — replace both Workspace lookups (upload + retrieval paths) with `await get_workspace_cached(...)`

## 2. Agent Cache Coverage — Service Paths

- [x] 2.1 `agent_runner.py` `_get_agent_model_limit()` — replace `db.get(Agent, agent_id)` with `await get_agent_cached(agent_id)`
- [x] 2.2 `conversation_service.py` `maybe_generate_summary()` — replace `db.get(Agent, agent_id)` with `await get_agent_cached(agent_id)`
- [x] 2.3 `conversation_context.py` profile lookup — replace `select(Agent).where(...)` with `await get_agent_cached(agent_id)`
- [x] 2.4 `context_compaction_service.py` system-prompt lookup — replace `db.get(Agent, agent_id)` with `await get_agent_cached(agent_id)`
- [x] 2.5 `hooks/checkpoint.py` — replace `db.get(Agent, ctx.agent_id)` with `await get_agent_cached(ctx.agent_id)`

## 3. Direct UPDATE Optimization

- [x] 3.1 `agent_runner.py` `_update_message_parts()` — change from SELECT+ORM-assign to `update(Message).where(...).values(parts=parts)`
- [x] 3.2 `agent_runner.py` `persist_event()` `message.end` path — change from SELECT+ORM-assign to direct `UPDATE Message SET status='complete', parts=:parts WHERE id=:message_id`
- [x] 3.3 `agent_runner.py` `persist_event()` `run.usage` path — change from SELECT+ORM-assign to direct `UPDATE agent_runs SET usage=:usage WHERE id=:run_id`
- [x] 3.4 `agent_runner.py` `persist_event()` `message.usage` path — change from SELECT+ORM-assign to direct `UPDATE message SET usage=:usage WHERE id=:message_id`

## 4. MCP Cache Invalidation Fix

- [x] 4.1 `api/mcp.py` delete-server handler — add `invalidate_agent_cache(agent.id)` call after each Agent's `mcp_server_ids_list` is modified

## 5. consume_stream Deduplication

- [x] 5.1 `agent_runner.py` `consume_stream()` — replace inline XADD+fallback in `artifact.create` block with `await _persist_or_stream(redis_client, run_id, event, parts, use_stream)`
- [x] 5.2 `agent_runner.py` `consume_stream()` — replace inline XADD+fallback in `deploy.status` block with `await _persist_or_stream(redis_client, run_id, event, parts, use_stream)`

## 6. Testing & Validation

- [x] 6.1 Run `ruff check .` and fix any lint errors
- [x] 6.2 Run `pytest` and ensure all existing tests pass
- [x] 6.3 Verify no remaining `select(Workspace).where(Workspace.conversation_id` in tool/service paths (grep audit)
- [x] 6.4 Verify no remaining `db.get(Agent,` in service paths listed in task group 2 (grep audit)
