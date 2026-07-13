# Design: Redis Async Persistence & Caching

## Context

AChat's backend runs locally while PostgreSQL, Milvus, ES, and Neo4j are deployed on a remote server. Every DB round trip costs 30-100ms of network RTT. Profiling a single agent conversation reveals two latency hotspots:

1. **Redundant metadata reads**: `execute_run` → `execute_simple_run` → `build_adapter_input` re-query Agent (3×), Conversation (3×), Workspace (2×) within a single run — ~12 sequential DB queries before the LLM call.
2. **Per-token DB writes**: `persist_event` calls `_update_message_parts` (SELECT + UPDATE) for every `part.delta` event — ~200 times per response, each a full DB round trip.

Meanwhile, the EventBus→SSE path (in-process `asyncio.Queue.put_nowait`) is zero-latency and needs no change. Redis deployed locally (~1ms RTT) can absorb both the redundant reads and the per-token writes.

The project already has an independent-degradation pattern for optional infrastructure (Milvus/ES/Neo4j/Kafka), each built through `infra/factory.py` with try/except and graceful fallback. Redis follows the same pattern.

## Goals / Non-Goals

**Goals:**
- Reduce per-conversation DB I/O by ~90% when Redis is available locally
- Keep the SSE hot path untouched (EventBus → SSE, zero added latency)
- Graceful degradation: Redis unavailable → identical to current behavior
- No PostgreSQL schema changes, no StreamEvent protocol changes
- Crash-safe: Redis Stream persists events; backend restart can recover

**Non-Goals:**
- Multi-instance backend support (Redis Stream consumer groups enable this, but it's not a current requirement)
- Replacing EventBus with Redis for SSE delivery (adds latency to hot path)
- Caching Message history or Conversation (too volatile, consistency risk)
- Caching RAG chunks or LTM embeddings (separate infrastructure concern)

## Decisions

### D1: Redis deployment — local only

Redis MUST be deployed on the same machine as the backend (docker container or local process). If `REDIS_URL` is empty or Redis is unreachable, the system falls back to current synchronous behavior.

**Alternatives considered:**
- Redis on the remote server alongside PG/Milvus — no benefit, same RTT as PG
- Redis Cluster / Sentinel — overkill for single-instance local deployment

### D2: SSE stays on EventBus, Redis Stream only for persistence

Each event goes to two paths:
- `publish(event)` → EventBus → SSE (in-process, ~0ms) — unchanged
- `xadd(event)` → Redis Stream → async DB writer (background) — new

**Rationale**: SSE is real-time UX; adding a Redis hop (even ~1ms) to every `part.delta` would accumulate. Redis Stream's value is decoupling persistence from streaming.

**Alternatives considered:**
- Replace EventBus with Redis Stream for SSE — unified pipeline, SSE replay on reconnect, but adds per-event latency and couples SSE availability to Redis

### D3: Cache strategy — read-through with write-invalidation

Cache reads: check Redis first; on miss, query PG and backfill Redis with TTL.
Cache writes: after PG write, `DEL` the corresponding Redis key.

Cached entities and TTLs:
- `agent:{agent_id}` — TTL 300s, invalidated on agent create/update/delete
- `user_settings:{user_id}` — TTL 300s, invalidated on settings update
- `workspace:{conversation_id}` — TTL 300s, set on conversation creation (rarely changes)
- `user_prefs:{user_id}` — TTL 120s, invalidated on preference save/consolidation
- `global_settings` — TTL 300s, replaces existing in-memory `_global_cache` when Redis available

**Not cached**: Message, Conversation, ContextSummary, AgentRun, RagChunk — all too volatile during a conversation.

### D4: Async DB writes — per-run Redis Stream + consumer group

**Stream key**: `achat:run:{run_id}` — one Stream per agent run.

**Event routing in `persist_event`**:
- `message.start` → synchronous DB INSERT (Message row must exist immediately)
- `message.end` → synchronous DB UPDATE (status="complete" + final parts flush)
- `run.usage` / `message.usage` → XADD to Stream (deferred)
- `part.start` / `part.delta` / `part.end` → XADD to Stream (deferred)
- `tool.call` / `tool.result` → XADD to Stream (deferred)
- `artifact.create` → no DB write (list append only, unchanged)

**Consumer**: A background asyncio task started at application startup. Uses `XREADGROUP` with `COUNT 50` and `BLOCK 1000`. For each batch:
1. Group events by `message_id`
2. For each message, take the latest `parts` from `parts_buffer` (which is already in-memory)
3. Single `UPDATE` per message (not per event)
4. `XACK` all processed events

**Stream trimming**: `MAXLEN ~ 10000` per stream; stream is deleted on run finalization.

**Deduplication**: The consumer only needs the final state of `parts` per message, not every intermediate delta. The `parts_buffer` dict in `consume_stream` already accumulates this — the consumer reads from it directly rather than replaying every XADD'd event.

### D5: Degradation — Redis unavailable → synchronous mode

`infra/factory.py` builds Redis client in try/except. If `REDIS_URL` is empty or connection fails:
- `infra.redis_client = None`
- `persist_event` checks: if Redis available → XADD; else → call `_update_message_parts` directly (current behavior)
- Cache layer: if Redis available → check cache; else → query PG directly
- Startup log: "Redis not configured (REDIS_URL is empty)" — identical to Milvus/ES/Neo4j pattern

### D6: Crash recovery — streaming-orphan scan

On startup, scan for `Message` rows with `status="streaming"` and `created_at < now - 5min`:
- If Redis Stream `achat:run:{run_id}` exists → replay remaining events, then mark as `complete`
- If Stream doesn't exist (Redis was also lost, or run predates Redis) → mark as `interrupted`

### D7: Connection pool — `pool_pre_ping` → `pool_recycle`

Separately from Redis, change the SQLAlchemy engine config:
- `pool_pre_ping=True` → `pool_recycle=3600` (1 hour)
- Rationale: `pool_pre_ping` sends `SELECT 1` on every connection checkout — with remote PG this adds a full RTT per query. `pool_recycle` achieves the same goal (drop stale connections) without per-checkout overhead.

## Risks / Trade-offs

- **[Redis + PG inconsistency window]** Parts in Redis Stream may lag PG by up to ~1s → Mitigation: `message.end` does a synchronous final flush; REST API reads from PG, and the frontend gets real-time updates via SSE (which is always current). If user refreshes mid-stream, they see the last flushed state; SSE reconnect fills the gap.

- **[Backend crash mid-stream]** Redis Stream has events, PG has stale parts → Mitigation: startup recovery scan (D6) replays or marks interrupted. Redis persistence (AOF/RDB) should be enabled for crash safety.

- **[Redis memory growth from unconsumed streams]** If DB writer is slow/stuck, Streams grow → Mitigation: `MAXLEN ~ 10000` cap per stream; consumer reads in batches; streams are deleted on run finalization.

- **[Concurrent runs writing to same message]** Two runs writing to the same `message_id` → Mitigation: `parts_buffer` is per-run (local to `consume_stream`), and `message_id` is unique per run. No concurrent writes to the same message in normal operation.

- **[Cache invalidation races]** Agent updated on one path while another path reads stale cache → Mitigation: short TTL (300s max), write-then-delete pattern. Worst case: 5min of stale data, which is acceptable for Agent metadata.

## Migration Plan

1. Add `redis[hiredis]` to `requirements.txt`
2. Add `REDIS_URL` to `.env.example` (empty by default — opt-in)
3. Add Redis service to `docker-compose.yml`
4. Implement `infra/factory.py` Redis build (graceful fallback)
5. Implement metadata cache wrappers (Agent/Settings/Workspace/Preference)
6. Implement async DB writer (Stream consumer)
7. Modify `persist_event` to route through Stream when available
8. Add startup recovery scan
9. Change `pool_pre_ping` → `pool_recycle` in `engine.py`
10. Test with Redis available and unavailable (degradation paths)

**Rollback**: Set `REDIS_URL=` (empty) — all code paths fall back to current synchronous behavior. No DB migration needed.

## Open Questions

- Should the DB writer consumer be a single asyncio task or a small worker pool? (Single task is simpler and sufficient for the volume; pool can be added later if needed.)
- Should we cache `ContextSummary` with a very short TTL (30s)? It changes during auto-compaction, but within a single run it's stable. (Deferred — not in scope for this change.)
