# Design: Redis Cache Coverage Fixes

## Context

The `redis-async-persistence` change introduced `MetadataCache` and `get_*_cached()` helpers (`cache_helpers.py`). A code audit revealed that while the hot path (`execute_run` → `build_adapter_input`) was migrated to use cached lookups, many secondary call sites still query PostgreSQL directly. Additionally, the synchronous fallback path (`_update_message_parts`) uses an inefficient SELECT-then-ORM-assign pattern, and one cache invalidation site (MCP server deletion) was missed entirely.

This change is purely about **closing coverage gaps** — no new caching concepts, no new cached entities, no architectural changes.

## Goals / Non-Goals

**Goals:**
- All Workspace lookups in tool execution paths use `get_workspace_cached()` instead of direct DB queries
- All single-Agent lookups in service paths use `get_agent_cached()` instead of direct DB queries
- Synchronous `_update_message_parts` uses direct UPDATE (1 RTT) instead of SELECT + ORM assign (2 RTTs)
- MCP server deletion invalidates all affected Agent cache entries
- `consume_stream` deduplicates inline XADD logic by reusing `_persist_or_stream()`

**Non-Goals:**
- Caching Conversation or Message entities (still too volatile — design decision from redis-async-persistence stands)
- Batch Agent cache lookups (MGET) — complexity not justified for current group-chat scale
- Caching `agent_ids_list` separately (deferred — see redis-async-persistence design.md Open Questions)
- Changing cache TTLs or key conventions
- Any new infrastructure or dependencies

## Decisions

### D1: Tool execution paths use `get_workspace_cached()`

**Affected files**: `tools/bash.py`, `tools/deploy_workspace.py`, `tools/fs_write.py`, `tools/fs_edit.py`, `services/hooks/tool_approval.py`, `services/fs_service.py`, `services/deploy_command_service.py`, `services/attachment_service.py`

Each of these currently does:
```python
async with get_db() as db:
    result = await db.execute(
        select(Workspace).where(Workspace.conversation_id == conversation_id)
    )
    workspace = result.scalar_one_or_none()
```

Replace with:
```python
from app.infra.cache_helpers import get_workspace_cached
workspace = await get_workspace_cached(conversation_id)
```

The cached Workspace is a detached ORM instance (expunged by the loader), so it's safe to use outside a DB session. Tool execution only reads Workspace fields (`mode`, `bound_path`, `root_path`) — no writes.

**Note**: `fs_write.py` and `fs_edit.py` also query `Conversation` for `fs_write_approval_mode`. Conversation is not cached (too volatile). These Conversation queries remain as-is.

### D2: Agent lookups use `get_agent_cached()` in service paths

**Affected files**: `agent_runner.py` (`_get_agent_model_limit`), `conversation_service.py` (`maybe_generate_summary`), `conversation_context.py` (profile lookup), `context_compaction_service.py` (system-prompt lookup), `hooks/checkpoint.py`

Replace `db.get(Agent, agent_id)` or `select(Agent).where(Agent.id == ...)` with `await get_agent_cached(agent_id)`.

**Not changed**: `agent_loop.py:374` uses `select(Agent).where(Agent.id.in_(agent_ids))` — a batch query for coordinated-mode roster. The cache only supports single-ID lookups; converting this to N sequential cache lookups is possible but not clearly better than 1 DB query for small N. Deferred.

**Not changed**: `conversation_service.py:366` (`create_conversation`) and `send_message:831` — these query multiple Agents by ID list for validation, not for their data. Cache doesn't help with existence checks (a cache miss returns None, same as "not found", but can't distinguish "doesn't exist" from "not cached yet").

### D3: `_update_message_parts` — direct UPDATE instead of SELECT + ORM assign

**Current** (2 RTTs):
```python
async with get_db() as db:
    msg = (await db.execute(select(Message).where(Message.id == message_id))).scalar_one_or_none()
    if msg is not None:
        msg.parts_list = parts  # ORM dirty tracking → UPDATE on flush
```

**After** (1 RTT):
```python
from sqlalchemy import update

async with get_db() as db:
    await db.execute(
        update(Message).where(Message.id == message_id).values(parts_list=parts)
    )
```

This function is called ~200 times per agent reply (once per `part.delta`) when Redis is unavailable. With remote PG, this saves 200 × 30-100ms = 6-20 seconds per reply.

The same pattern applies to `persist_event`'s `message.end`, `run.usage`, and `message.usage` paths — all switch to direct UPDATE.

### D4: MCP server deletion — cache invalidation

**Current** (`api/mcp.py:216`): iterates all Agents, removes the deleted server_id from `mcp_server_ids_list`, commits. No cache invalidation.

**After**: after modifying each Agent, call `invalidate_agent_cache(agent.id)`:
```python
from app.infra.cache_helpers import invalidate_agent_cache

for agent in all_agents:
    ids = agent.mcp_server_ids_list
    if server_id in ids:
        ids.remove(server_id)
        agent.mcp_server_ids_list = ids
        await invalidate_agent_cache(agent.id)
```

### D5: `consume_stream` — deduplicate XADD logic

**Current**: `artifact.create` (lines 1840-1848) and `deploy.status` (lines 1870-1878) each inline:
```python
redis_client = _get_redis_client()
if redis_client is not None:
    try:
        from app.services.async_db_writer import xadd_event
        await xadd_event(redis_client, run_id, event.model_dump_json(by_alias=True))
    except Exception:
        await _update_message_parts(message_id, parts)
else:
    await _update_message_parts(message_id, parts)
```

**After**: both call the existing helper:
```python
await _persist_or_stream(redis_client, run_id, event, parts, use_stream)
```

Where `redis_client = _get_redis_client()` and `use_stream = redis_client is not None` are already computed at the top of `consume_stream`.

## Risks / Trade-offs

- **[Detached ORM instance mutation]** `get_workspace_cached()` returns a detached Agent/Workspace object. If any call site mutates it and expects the change to persist, this breaks. **Mitigation**: all affected call sites are read-only (tool execution reads Workspace fields, never writes them). Verified by audit.

- **[Direct UPDATE skips ORM events]** SQLAlchemy ORM `before_update`/`after_update` events won't fire for the direct UPDATE in `_update_message_parts`. **Mitigation**: `Message` model has no ORM event hooks. The `parts_list` is a plain JSON column with no side effects.

- **[Cache invalidation under MCP batch delete]** Invalidating N agents in a loop is N Redis DEL calls. For typical N (< 20 agents), this is < 20ms total. Acceptable.

- **[`_persist_or_stream` signature mismatch]** The existing `_persist_or_stream` takes `(redis_client, run_id, event, parts, use_stream)`. The inline code in `consume_stream` for `artifact.create`/`deploy.status` adds a part to `parts_buffer` before persisting — this is already done before the deduplicated call, so the helper receives the updated `parts` list. No mismatch.
