## ADDED Requirements

### Requirement: Async Event Persistence via Redis Stream

The system SHALL provide an alternative persistence path for stream events that uses Redis Stream as a write-behind buffer, deferring PostgreSQL writes to a background consumer.

When Redis is available, `persist_event` SHALL route deferrable event types to a per-run Redis Stream (`achat:run:{run_id}`) instead of synchronously writing to PostgreSQL. A background consumer group (`db_writer`) SHALL read events in batches, aggregate by `message_id`, and flush the latest `parts` state to PostgreSQL in a single `UPDATE` per message.

#### Scenario: Deferrable events go to Redis Stream

- **WHEN** Redis is available and `persist_event` receives a `part.start`, `part.delta`, `part.end`, `tool.call`, `tool.result`, `message.usage`, `run.usage`, or `deploy.status` event
- **THEN** the system SHALL `XADD` the event to `achat:run:{run_id}` instead of calling `_update_message_parts` or opening a DB session
- **AND** the `parts_buffer` (in-memory dict) SHALL still be updated synchronously (it tracks the latest parts state)
- **AND** the event SHALL be published to EventBus for SSE delivery (unchanged)

#### Scenario: Non-deferrable events write synchronously

- **WHEN** `persist_event` receives a `message.start` event
- **THEN** the system SHALL synchronously INSERT the Message row to PostgreSQL (the row must exist for run references and REST queries)
- **AND** the system SHALL NOT defer this write to the background consumer

- **WHEN** `persist_event` receives a `message.end` event
- **THEN** the system SHALL synchronously flush the final `parts_buffer` state to PostgreSQL and update `status="complete"`
- **AND** the system SHALL delete the Redis Stream `achat:run:{run_id}` after flushing

#### Scenario: Redis unavailable — synchronous fallback

- **WHEN** `infra.redis_client` is `None` (Redis not configured or unreachable)
- **THEN** `persist_event` SHALL call `_update_message_parts` synchronously for every event (identical to current behavior)
- **AND** no Redis Stream operations SHALL be attempted

#### Scenario: Background consumer batches writes

- **WHEN** the DB writer consumer reads a batch of events from the Stream (up to 50 events or 1 second block)
- **THEN** it SHALL group events by `message_id`
- **AND** for each `message_id`, it SHALL perform a single `UPDATE` of `parts_list` using the latest state from `parts_buffer`
- **AND** it SHALL `XACK` all processed events
- **AND** it SHALL NOT issue one DB query per event (batch deduplication)

### Requirement: DB Writer Consumer Lifecycle

The system SHALL start a background asyncio task for the DB writer consumer at application startup (within the `lifespan` context manager). The consumer SHALL run for the lifetime of the application and shut down gracefully on application shutdown.

#### Scenario: Consumer starts with application

- **WHEN** the FastAPI application starts and Redis is available
- **THEN** a background asyncio task SHALL be started that reads from Redis Streams
- **AND** the task SHALL use a consumer group named `db_writer`
- **AND** if Redis is unavailable, the consumer task SHALL NOT be started

#### Scenario: Consumer shuts down on application stop

- **WHEN** the FastAPI application shuts down
- **THEN** the DB writer consumer task SHALL be cancelled
- **AND** any remaining events in Streams SHALL be left for the next startup recovery scan

#### Scenario: Consumer error handling

- **WHEN** the consumer encounters an error (Redis disconnect, PG write failure)
- **THEN** it SHALL log the error and continue processing (not crash)
- **AND** unacknowledged events SHALL remain in the Stream for retry on the next read cycle

### Requirement: Crash Recovery Scan

The system SHALL perform a recovery scan on startup that finds Message rows stuck in `status="streaming"` from a previous crash.

#### Scenario: Recovery finds interrupted messages

- **WHEN** the application starts up
- **THEN** the system SHALL query for `Message` rows with `status="streaming"` and `created_at < now - 5 minutes`
- **AND** for each such message, if the corresponding Redis Stream exists, the system SHALL attempt to replay remaining events and mark as `complete`
- **AND** if the Stream does not exist, the system SHALL mark the message as `interrupted`

#### Scenario: No stuck messages

- **WHEN** the application starts up and there are no messages with `status="streaming"`
- **THEN** the recovery scan SHALL do nothing and log "No interrupted messages found"

### Requirement: Stream Lifecycle Management

The system SHALL manage Redis Stream lifecycle to prevent unbounded memory growth.

#### Scenario: Stream created on run start

- **WHEN** an agent run begins and Redis is available
- **THEN** a Redis Stream `achat:run:{run_id}` SHALL be used for event persistence
- **AND** the Stream SHALL be created implicitly on first `XADD`

#### Scenario: Stream deleted on run finalization

- **WHEN** `message.end` is processed (synchronous flush complete)
- **THEN** the system SHALL `DEL` the Redis Stream `achat:run:{run_id}`
- **AND** pending events in the Stream SHALL have been flushed to PostgreSQL before deletion

#### Scenario: Stream capped by MAXLEN

- **WHEN** a Stream exceeds 10,000 entries
- **THEN** the oldest entries SHALL be trimmed automatically (via `XADD ... MAXLEN ~ 10000`)
- **AND** trimmed entries that were already acknowledged by the consumer SHALL not cause data loss (they were flushed to PG)
