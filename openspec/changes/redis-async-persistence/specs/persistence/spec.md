## MODIFIED Requirements

### Requirement: Event Persistence

The system SHALL persist stream events to PostgreSQL with an optional Redis-backed async write path. When Redis is available, deferrable event types (`part.start`, `part.delta`, `part.end`, `tool.call`, `tool.result`, `message.usage`, `run.usage`, `deploy.status`) SHALL be queued to a Redis Stream and flushed to PostgreSQL in batches by a background consumer. Non-deferrable event types (`message.start` for INSERT, `message.end` for status UPDATE) SHALL remain synchronous.

When Redis is unavailable, all event persistence SHALL be synchronous (current behavior — `persist_event` calls `_update_message_parts` / direct DB writes for every event).

The `parts_buffer` in-memory dict SHALL always be updated synchronously regardless of persistence path, ensuring the live state is available for SSE and for the background consumer's batch flush.

#### Scenario: Synchronous persistence with Redis unavailable

- **WHEN** Redis is unavailable and `persist_event` receives any event type
- **THEN** the system SHALL write to PostgreSQL synchronously within the event processing loop
- **AND** the behavior SHALL be identical to the pre-Redis implementation

#### Scenario: Async persistence with Redis available

- **WHEN** Redis is available and `persist_event` receives a `part.delta` event
- **THEN** the system SHALL update `parts_buffer[message_id]` in memory
- **AND** it SHALL `XADD` the event to `achat:run:{run_id}` Redis Stream
- **AND** it SHALL NOT open a DB session or call `_update_message_parts`
- **AND** the background consumer SHALL eventually flush the latest `parts_buffer` state to PostgreSQL

#### Scenario: Final flush on message.end

- **WHEN** `persist_event` receives a `message.end` event (with Redis available)
- **THEN** the system SHALL synchronously call `_update_message_parts` with the final `parts_buffer` state
- **AND** it SHALL update `Message.status = "complete"` synchronously
- **AND** it SHALL delete the Redis Stream `achat:run:{run_id}` after flushing

### Requirement: Database Connection Pool Configuration

The SQLAlchemy async engine SHALL use `pool_recycle` instead of `pool_pre_ping` for remote PostgreSQL deployments to avoid per-checkout ping overhead. `pool_pre_ping` sends a `SELECT 1` on every connection checkout, adding a full network RTT; `pool_recycle` achieves stale-connection handling by recycling connections older than the configured age without per-checkout overhead.

#### Scenario: Engine configuration

- **WHEN** the database engine is initialized in `init_db()`
- **THEN** `pool_pre_ping` SHALL be removed (or set to `False`)
- **AND** `pool_recycle` SHALL be set to `3600` (1 hour)
- **AND** `pool_size` and `max_overflow` SHALL remain at their current values (10 and 20)

#### Scenario: Stale connection handling

- **WHEN** a connection in the pool is older than 3600 seconds and is checked out
- **THEN** SQLAlchemy SHALL dispose the stale connection and create a fresh one
- **AND** the application SHALL NOT encounter "connection already closed" errors from long-idle connections
