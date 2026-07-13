## 1. Infrastructure Setup

- [x] 1.1 Add `redis[hiredis]` to `backend/requirements.txt`
- [x] 1.2 Add `REDIS_URL=` to `backend/.env.example` with documentation comment explaining local Redis deployment
- [x] 1.3 Add `redis_url: str = ""` field to `Settings` class in `backend/app/config.py`
- [x] 1.4 Add `redis: str = "disconnected"` field to `InfrastructureStatus` in `backend/app/infra/status.py`
- [x] 1.5 Add Redis client build logic to `build_infrastructure()` in `backend/app/infra/factory.py` — create `redis.asyncio.Redis` client from `REDIS_URL`, wrapped in try/except, set `infra.redis_client` and `infra.status.redis`
- [x] 1.6 Add Redis cleanup to `close_infrastructure()` in `backend/app/infra/factory.py`
- [x] 1.7 Add Redis service to `docker-compose.yml` (or `docker-compose.dev.yml`)
- [x] 1.8 Verify startup dashboard logs Redis status (connected / not configured / unavailable)

## 2. Metadata Cache Layer

- [x] 2.1 Create `backend/app/infra/cache.py` with a `MetadataCache` class wrapping Redis KV operations: `get(key)`, `set(key, value, ttl)`, `delete(key)`, `get_or_load(key, ttl, loader_fn)` — all methods return `None`/no-op when Redis client is `None`
- [x] 2.2 Implement `get_or_load` pattern: check Redis → on miss call `loader_fn` → backfill Redis with TTL → return value. On Redis error, fall through to `loader_fn` directly.
- [x] 2.3 Add `agent:{agent_id}` cache to Agent queries — wrap `select(Agent).where(Agent.id == ...)` with `cache.get_or_load("agent:{id}", 300, lambda: db_query + serialize)`, with invalidation on agent create/update/delete
- [x] 2.4 Add `user_settings:{user_id}` cache to `get_user_settings()` in `backend/app/services/settings_service.py` — TTL 300s, invalidate on `update_user_settings()`
- [x] 2.5 Add `workspace:{conversation_id}` cache to Workspace queries — TTL 300s, set on conversation creation
- [x] 2.6 Add `user_prefs:{user_id}` cache to `Preference` lookups in `backend/app/memory/preference.py` — TTL 120s, invalidate on `save_batch()` and consolidation delete
- [x] 2.7 Migrate `global_settings_service._global_cache` to use Redis when available (fall back to in-memory dict when Redis is `None`)
- [x] 2.8 Write tests: cache hit, cache miss, invalidation, Redis unavailable degradation

## 3. Async DB Writer (Redis Stream)

- [x] 3.1 Create `backend/app/services/async_db_writer.py` with a `DBWriterConsumer` class: initializes consumer group `db_writer` on per-run Streams, reads batches via `XREADGROUP`, flushes to PG, `XACK`s
- [x] 3.2 Implement `XADD` helper: `xadd_event(redis_client, run_id, event_json)` — serializes StreamEvent to JSON, `XADD achat:run:{run_id} MAXLEN ~ 10000 * {json}`
- [x] 3.3 Implement batch flush logic: group events by `message_id`, take latest `parts_buffer` state (passed via shared dict or callable), single `UPDATE` per message, `XACK` all
- [x] 3.4 Implement consumer lifecycle: `start()` creates asyncio task, `stop()` cancels it, error handling (log + continue, don't crash)
- [x] 3.5 Start consumer in `lifespan()` in `backend/app/main.py` — only if Redis is available
- [x] 3.6 Stop consumer on application shutdown
- [x] 3.7 Write tests: batch flush, consumer error handling, graceful shutdown

## 4. Event Persistence Routing

- [x] 4.1 Modify `persist_event()` in `backend/app/services/agent_runner.py` — add Redis availability check; for deferrable event types, `XADD` to Stream instead of calling `_update_message_parts`
- [x] 4.2 Keep `message.start` synchronous (INSERT Message) regardless of Redis availability
- [x] 4.3 Keep `message.end` synchronous — flush final `parts_buffer` to PG, update `status="complete"`, then `DEL` the Stream
- [x] 4.4 Keep `run.usage` and `message.usage` synchronous (simple UPDATE, low frequency)
- [x] 4.5 Ensure `parts_buffer` is always updated synchronously in memory before any Stream XADD or DB write
- [x] 4.6 Ensure `publish()` to EventBus still happens for SSE (unchanged)
- [x] 4.7 Write tests: deferrable event goes to Stream when Redis available, goes to DB when Redis unavailable, `message.start`/`message.end` always synchronous

## 5. Crash Recovery

- [x] 5.1 Create `backend/app/services/recovery_scan.py` with `async def scan_interrupted_messages()` — query `Message` where `status="streaming"` and `created_at < now - 5min`
- [x] 5.2 For each interrupted message: if Redis Stream `achat:run:{run_id}` exists, attempt to replay remaining events and flush final parts; mark as `complete`
- [x] 5.3 If Stream does not exist, mark message as `interrupted`
- [x] 5.4 Call `scan_interrupted_messages()` in `lifespan()` startup, after `init_db()` and after infrastructure build
- [x] 5.5 Write tests: recovery with Stream present, recovery without Stream, no stuck messages

## 6. Connection Pool Optimization

- [x] 6.1 In `backend/app/db/engine.py`, change `pool_pre_ping=True` to `pool_pre_ping=False` and add `pool_recycle=3600`
- [x] 6.2 Verify no "connection already closed" errors with long-idle connections (manual test or integration test)

## 7. Documentation & Cleanup

- [x] 7.1 Update `CLAUDE.md` §2 infrastructure table — add Redis row
- [x] 7.2 Update `backend/.env.example` — add `REDIS_URL` with documentation
- [x] 7.3 Update `backend/.env.local` — add `REDIS_URL=redis://localhost:6379/0`
- [x] 7.4 Run `ruff check .` and fix any lint errors
- [x] 7.5 Run `pytest` and ensure all existing tests pass (Redis unavailable path = current behavior)
- [x] 7.6 Add integration test: start with Redis available, send a message, verify parts are eventually in PG, verify SSE is real-time
