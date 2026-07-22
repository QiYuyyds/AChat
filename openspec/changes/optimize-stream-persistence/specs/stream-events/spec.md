# Stream Events

## MODIFIED Requirements

### Requirement: StreamEvent SHALL be the only live update protocol

All agent output, tool activity, artifact creation, pending approvals, dispatch state, and usage updates SHALL flow through `StreamEvent` before reaching the UI. Each event SHALL carry the owning `user_id` so the EventBus can filter delivery to the correct SSE subscribers.

AgentRunner SHALL publish each StreamEvent to the EventBus BEFORE persisting it to the database. For deferrable events (part.start/delta/end, tool.call/result, message.start/end), publish SHALL happen immediately, with database persistence deferred to the Redis Stream write-behind consumer. For usage events (run.usage, message.usage), publish SHALL happen before the fire-and-forget database update task is scheduled. This ordering ensures SSE delivery is never blocked by remote database write latency.

When Redis is unavailable, AgentRunner SHALL fall back to synchronous database writes, and publish SHALL still occur after persistence completes in this degraded mode (preserving the previous behavior to avoid data loss when the write-behind buffer is absent).

#### Scenario: Adapter emits text
- **WHEN** an adapter starts a text part
- **THEN** AgentRunner publishes the event to SSE subscribers via EventBus immediately
- **AND** AgentRunner XADDs the event to Redis Stream for async DB persistence
- **AND** the DBWriterConsumer later flushes the parts to PostgreSQL

#### Scenario: Adapter emits message.start
- **WHEN** an adapter emits a `message.start` event
- **THEN** AgentRunner publishes the event to SSE subscribers immediately
- **AND** AgentRunner XADDs the event to Redis Stream
- **AND** the DBWriterConsumer later INSERTs the message row into PostgreSQL (ON CONFLICT DO NOTHING)

#### Scenario: Adapter emits message.end
- **WHEN** an adapter emits a `message.end` event
- **THEN** AgentRunner publishes the event to SSE subscribers immediately
- **AND** AgentRunner XADDs the event to Redis Stream with the latest parts buffer state
- **AND** the DBWriterConsumer later UPDATEs the message row with final parts and status='complete'

#### Scenario: Redis unavailable — synchronous fallback
- **WHEN** Redis client is None or XADD fails
- **AND** an adapter emits any StreamEvent
- **THEN** AgentRunner writes to PostgreSQL synchronously (current behavior)
- **AND** publishes the event to SSE after the DB write completes
- **AND** the stream is slower but correctness is preserved

### Requirement: Usage events SHALL update durable accounting

Adapters SHALL emit `message.usage` and `run.usage` when provider usage data is available. AgentRunner SHALL publish these events to SSE immediately and persist them via fire-and-forget `asyncio.create_task`, decoupled from the main event loop. Usage persistence failures SHALL be logged but SHALL NOT block the stream or affect the conversation content.

#### Scenario: Codex reports turn usage
- **WHEN** Codex emits `turn.completed.usage`
- **THEN** the adapter emits `message.usage` and `run.usage`
- **AND** AgentRunner publishes both events to SSE immediately
- **AND** AgentRunner schedules fire-and-forget DB UPDATE tasks for both
- **AND** if the DB UPDATE fails, it is logged but the stream continues

### Requirement: Message streaming SHALL be bracketed

Each agent message MUST begin with `message.start` and finish with `message.end`, with part and tool events associated to the message id between those boundaries. Both `message.start` and `message.end` SHALL be persisted via Redis Stream write-behind, not synchronously.

#### Scenario: Run completes normally
- **WHEN** the final adapter event has been consumed
- **THEN** the message status is eventually updated to `complete` by the DBWriterConsumer
- **AND** the run ends with status `complete`
- **AND** if the consumer has not flushed yet, recovery_scan ensures finalization on next startup
