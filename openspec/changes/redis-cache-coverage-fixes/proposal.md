# Proposal: Redis Cache Coverage Fixes

## Why

The `redis-async-persistence` change introduced `MetadataCache` and `get_*_cached()` helpers, but a code audit revealed that many call sites still query PostgreSQL directly instead of using the cached helpers. The highest-impact gaps are: (1) tool execution paths (`bash`, `fs_write`, `fs_edit`, `deploy_workspace`) query Workspace from DB on every tool call — adding 30-100ms RTT per invocation; (2) the synchronous fallback `_update_message_parts` does SELECT + UPDATE (2 RTTs) when a direct UPDATE (1 RTT) suffices, doubling DB I/O when Redis is unavailable; (3) MCP server deletion modifies Agents without invalidating their cache entries — a correctness bug.

## What Changes

- Replace direct `select(Workspace).where(...)` calls with `get_workspace_cached()` in 6 tool/service call sites: `bash.py`, `deploy_workspace.py`, `tool_approval.py`, `fs_service.py`, `deploy_command_service.py`, `attachment_service.py`
- Replace direct `select(Agent)` / `db.get(Agent)` calls with `get_agent_cached()` in 5 service call sites: `_get_agent_model_limit()`, `maybe_generate_summary()`, `conversation_context.py` profile lookup, `context_compaction_service.py` system-prompt lookup, `hooks/checkpoint.py`
- Change `_update_message_parts()` from SELECT-then-ORM-assign to a direct `UPDATE Message SET parts_list = ... WHERE id = ...`, halving RTTs in the synchronous fallback path (~200 calls per agent reply)
- Apply the same direct-UPDATE optimization to `message.end` final flush, `run.usage`, and `message.usage` persistence paths in `persist_event()`
- Add `invalidate_agent_cache()` calls in `api/mcp.py` delete-server handler — after modifying each Agent's `mcp_server_ids_list`, the corresponding cache entry must be invalidated
- Deduplicate `consume_stream()` inline XADD+fallback logic for `artifact.create` and `deploy.status` events — replace with the existing `_persist_or_stream()` helper

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `metadata-cache`: Extends cache coverage requirements to tool execution paths and additional service call sites. Adds a cache invalidation requirement for MCP server deletion (Agent mutation side-effect). No new cached entities or TTL changes.

## Impact

- **Tool execution**: `backend/app/tools/bash.py`, `backend/app/tools/deploy_workspace.py`, `backend/app/tools/fs_write.py`, `backend/app/tools/fs_edit.py` — Workspace lookups switched to cached
- **Services**: `backend/app/services/fs_service.py`, `backend/app/services/deploy_command_service.py`, `backend/app/services/attachment_service.py`, `backend/app/services/hooks/tool_approval.py`, `backend/app/services/hooks/checkpoint.py`, `backend/app/services/conversation_context.py`, `backend/app/services/context_compaction_service.py`, `backend/app/services/conversation_service.py` (maybe_generate_summary)
- **Agent runner**: `backend/app/services/agent_runner.py` — `_update_message_parts` refactored to direct UPDATE; `persist_event` usage/end paths optimized; `consume_stream` deduplicated
- **API**: `backend/app/api/mcp.py` — cache invalidation on MCP server delete
- **No new dependencies, no schema changes, no protocol changes**
