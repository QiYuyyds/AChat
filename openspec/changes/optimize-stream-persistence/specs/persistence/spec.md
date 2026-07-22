# Persistence

## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The PostgreSQL schema MUST persist users, agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, global settings, and per-user settings. Ownership tables (`agents`, `conversations`, `documents`, `mcp_servers`) MUST include a `user_id` foreign key to the `users` table. Builtin agents MAY have a NULL `user_id`. The `agents` table SHALL include an `is_guide` boolean column (default `false`) to mark guide agents. The `conversations` table SHALL support `mode='guide'` as a valid string value (no DDL change needed since `mode` is a varchar column).

Message persistence SHALL follow the write-behind pattern: all StreamEvent types (including `message.start`, `message.end`, `run.usage`, `message.usage`) SHALL be persisted asynchronously. When Redis is available, deferrable events (`message.start`, `message.end`, part events, tool events) SHALL be XADD'd to a per-run Redis Stream and flushed to PostgreSQL by a background consumer. Usage events SHALL be persisted via fire-and-forget `asyncio.create_task`. When Redis is unavailable, all events SHALL fall back to synchronous PostgreSQL writes.

The DBWriterConsumer SHALL support both INSERT (for `message.start` events) and UPDATE (for part/tool events and `message.end`) operations. INSERT operations SHALL use `ON CONFLICT DO NOTHING` to ensure idempotency. UPDATE operations SHALL write the latest parts buffer state for each message.

#### Scenario: New conversation is created
- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** the conversation stores `user_id` of the creating user
- **AND** messages and runs can reference the conversation id.

#### Scenario: User queries their conversations
- **WHEN** an authenticated user requests `/api/conversations`
- **THEN** only conversations where `user_id` matches the authenticated user AND `mode != 'guide'` are returned.

#### Scenario: Guide agent column migration is idempotent
- **WHEN** the backend starts and the `is_guide` column already exists
- **THEN** the `ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide` statement is a no-op
- **AND** existing agents retain `is_guide=false`.

#### Scenario: Guide conversation mode is persisted
- **WHEN** a conversation is created with `mode='guide'`
- **THEN** the `mode` column stores `'guide'`
- **AND** no DDL change is required because `mode` is a varchar column.

#### Scenario: message.start is persisted asynchronously
- **WHEN** a `message.start` event is produced
- **AND** Redis is available
- **THEN** the event is XADD'd to the per-run Redis Stream
- **AND** the DBWriterConsumer INSERTs the message row with `ON CONFLICT DO NOTHING`
- **AND** if the consumer has not flushed yet, the message row may not exist in PG

#### Scenario: message.end is persisted asynchronously
- **WHEN** a `message.end` event is produced
- **AND** Redis is available
- **THEN** the event is XADD'd to the per-run Redis Stream with the latest parts buffer
- **AND** the DBWriterConsumer UPDATEs the message row with final parts and `status='complete'`

#### Scenario: Usage events are fire-and-forget
- **WHEN** a `run.usage` or `message.usage` event is produced
- **THEN** AgentRunner schedules a fire-and-forget `asyncio.create_task` to UPDATE the database
- **AND** if the task fails, it is logged but does not block the stream

#### Scenario: Crash recovery with async message.start
- **WHEN** the backend restarts after a crash
- **AND** a message exists in Redis Stream but not in PostgreSQL (INSERT not yet flushed)
- **THEN** `recovery_scan` SHALL detect the `message.start` event during Stream replay
- **AND** SHALL INSERT the message row before replaying parts
- **AND** SHALL mark the message as `complete` or `interrupted` based on stream completeness

#### Scenario: Redis unavailable — synchronous fallback
- **WHEN** Redis is not configured or connection fails
- **THEN** all events fall back to synchronous PostgreSQL writes
- **AND** `message.start` INSERTs synchronously
- **AND** `message.end` UPDATEs synchronously
- **AND** usage events are awaited synchronously
