## ADDED Requirements

### Requirement: Redis Infrastructure Service

The system SHALL integrate Redis as an optional infrastructure service through `infra/factory.py`, following the same independent-degradation pattern as Milvus, Elasticsearch, and Neo4j.

When `REDIS_URL` is configured and Redis is reachable, the system SHALL build an async Redis client and expose it via `Infrastructure.redis_client`. When Redis is unavailable or unconfigured, `Infrastructure.redis_client` SHALL be `None` and all Redis-dependent features SHALL degrade to their non-Redis fallback behavior.

#### Scenario: Redis configured and available

- **WHEN** `REDIS_URL` is set to a valid Redis connection string (e.g., `redis://localhost:6379/0`) and Redis is reachable
- **THEN** `build_infrastructure` SHALL create an async Redis client and set `infra.redis_client` to it
- **AND** `infra.status.redis` SHALL be `"connected"`
- **AND** the startup dashboard SHALL log "Redis connected: `<url>`"

#### Scenario: Redis not configured

- **WHEN** `REDIS_URL` is empty or not set
- **THEN** `build_infrastructure` SHALL skip Redis client creation
- **AND** `infra.redis_client` SHALL be `None`
- **AND** `infra.status.redis` SHALL be `"disconnected"`
- **AND** the startup log SHALL say "Redis not configured (REDIS_URL is empty)"

#### Scenario: Redis configured but unreachable

- **WHEN** `REDIS_URL` is set but Redis is not reachable (connection refused, timeout, DNS failure)
- **THEN** `build_infrastructure` SHALL catch the exception and set `infra.redis_client = None`
- **AND** `infra.status.redis` SHALL be `"disconnected"`
- **AND** the startup log SHALL warn "Redis unavailable: `<error>`"
- **AND** the system SHALL continue starting (PostgreSQL is the only hard dependency)

#### Scenario: Graceful degradation in application code

- **WHEN** `infra.redis_client` is `None` (Redis unavailable)
- **THEN** all cache lookups SHALL fall through to PostgreSQL queries
- **AND** all event persistence SHALL use synchronous `persist_event` DB writes
- **AND** SSE delivery SHALL be unaffected (EventBus is in-process, never depends on Redis)

### Requirement: Redis Configuration Surface

The system SHALL expose `REDIS_URL` as a configuration field in `Settings`, with an empty string default. The field SHALL be loadable from `.env` / `.env.local` files via pydantic-settings.

#### Scenario: Configuration default

- **WHEN** no `REDIS_URL` is set in any environment source
- **THEN** `Settings.redis_url` SHALL be an empty string
- **AND** Redis features SHALL be disabled (degradation to synchronous behavior)

#### Scenario: Configuration from environment

- **WHEN** `REDIS_URL=redis://localhost:6379/0` is set in `.env.local`
- **THEN** `Settings.redis_url` SHALL be `"redis://localhost:6379/0"`
- **AND** the system SHALL attempt to connect to Redis on startup

### Requirement: Redis Health Status

The system SHALL include Redis in the `InfrastructureStatus` dashboard, with a `redis` field alongside the existing `postgres`, `milvus`, `elasticsearch`, `neo4j`, and `kafka` fields.

#### Scenario: Status dashboard includes Redis

- **WHEN** the startup dashboard is logged
- **THEN** the output SHALL include a `redis` line showing `"connected"` or `"disconnected"`
- **AND** `GET /api/infra/status` SHALL include `redis` in its JSON response
