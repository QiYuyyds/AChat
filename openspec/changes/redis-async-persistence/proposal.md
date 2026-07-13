# Proposal: Redis Async Persistence & Caching

## Why

When PostgreSQL and infrastructure services (Milvus/ES/Neo4j) are deployed on a remote server while the backend runs locally, every DB round trip costs 30-100ms of network RTT. A single agent conversation triggers ~15 sequential metadata queries (Agent/Workspace/Conversation loaded repeatedly) plus 200+ per-token DB writes in `persist_event` — accumulating 10+ seconds of pure DB I/O before/during the LLM response. Redis deployed locally (~1ms RTT) can absorb both the redundant reads (KV cache) and the per-token writes (Stream + async flush), reducing end-to-end latency by ~90%.

## What Changes

- Add Redis as an optional infrastructure service through `infra/factory.py`, following the existing independent-degradation pattern (Redis unavailable → fall back to current synchronous behavior)
- Add a Redis KV metadata cache layer for low-churn entities (Agent, UserSettings, Workspace, UserPreference) — cache misses fall through to PostgreSQL, writes invalidate cache keys
- Add a Redis Stream-backed async DB writer: `part.delta` / `part.start` / `part.end` / `tool.call` / `tool.result` events are XADD'd to a per-run Redis Stream instead of synchronously persisted to PG; a background consumer batches and flushes to PG
- `message.start` (INSERT) and `message.end` (status + final parts flush) remain synchronous DB writes for consistency
- Add startup recovery: scan for `status="streaming"` messages older than a threshold and mark them as `interrupted`
- SSE event delivery continues through the in-process EventBus (zero-latency hot path); Redis Stream is only the persistence path
- Add `REDIS_URL` to configuration and `.env.example`; add Redis service to `docker-compose.yml`

## Capabilities

### New Capabilities

- `redis-infrastructure`: Redis integration as an optional infrastructure service — connection management through `infra/factory.py`, independent degradation, health status tracking. Covers both the Redis client lifecycle and the configuration surface (`REDIS_URL`, connection retry, graceful fallback).
- `async-persistence`: Async DB write path using Redis Streams — events are queued to a per-run Stream, a background consumer group batches and flushes to PostgreSQL. Covers the write-behind semantics, consumer lifecycle, crash recovery, and the degradation path (Redis unavailable → synchronous `persist_event`).
- `metadata-cache`: Redis KV cache for low-churn entities — Agent, UserSettings, Workspace, UserPreference. Covers cache key conventions, TTL, write-through invalidation, and degradation (cache miss → PostgreSQL).

### Modified Capabilities

- `persistence`: Adds an optional Redis-backed fast path for both reads (metadata cache) and writes (async Stream flush). The PostgreSQL schema and SQLAlchemy models are unchanged; the change is in the access layer (`get_db` queries wrapped with cache check, `persist_event` routed to Stream). Behavior with Redis unavailable is identical to current.

## Impact

- **New dependency**: `redis[hiredis]` Python package (async Redis client)
- **Config**: `REDIS_URL` in `backend/.env.example` and `backend/.env.local`; Redis service in `docker-compose.yml`
- **Infrastructure factory**: `backend/app/infra/factory.py` — add Redis client build + status; `backend/app/infra/status.py` — add `redis` field
- **Agent runner**: `backend/app/services/agent_runner.py` — `persist_event` and `consume_stream` modified to route events through Redis Stream when available; `_update_message_parts` made batchable
- **Settings service**: `backend/app/services/settings_service.py` — `get_user_settings` / `get_app_settings` wrapped with Redis cache
- **Conversation service**: `backend/app/services/conversation_service.py` — Agent/Workspace/Conversation queries wrapped with Redis cache
- **DB engine**: `backend/app/db/engine.py` — consider `pool_pre_ping` → `pool_recycle` change (separate concern, documented in design)
- **Startup**: `backend/app/main.py` — add Redis infra build + async DB writer consumer startup + recovery scan
- **CLAUDE.md**: Update §2 infrastructure table to include Redis
- **`.env.example`**: Add `REDIS_URL` with documentation
