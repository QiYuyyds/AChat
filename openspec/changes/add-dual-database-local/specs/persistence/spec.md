# Persistence

## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The database schema MUST persist all domain entities across a dual-database architecture: local SQLite for conversation hot data and personal configuration, remote PostgreSQL for user system and knowledge/RAG data.

When `DATABASE_LOCAL_URL` is set (dual-DB mode), 10 tables SHALL be stored in local SQLite: `messages`, `conversations`, `agent_runs`, `agent_run_checkpoints`, `artifacts`, `workspaces`, `attachments`, `conversation_context_summaries`, `agents`, `mcp_servers`. 12 tables SHALL be stored in remote PostgreSQL: `users`, `user_settings`, `user_preferences`, `global_settings`, `app_settings`, `rag_chunks`, `long_term_memory`, `chat_history`, `memory_nodes`, `memory_edges`, `documents`, `document_versions`.

When `DATABASE_LOCAL_URL` is not set (server deployment mode), all tables SHALL be stored in PostgreSQL. This preserves backward compatibility.

Ownership tables (`agents`, `conversations`, `mcp_servers`) in SQLite SHALL include a `user_id` column for data isolation, but the foreign key constraint to `users.id` SHALL be removed (cross-database FK is not supported). The `user_id` value MUST come from JWT authentication context, not from request bodies. All queries MUST filter by `user_id` at the application layer. Builtin agents MAY have a NULL `user_id`. The `agents` table SHALL include an `is_guide` boolean column (default `false`). The `conversations` table SHALL support `mode='guide'` as a valid string value.

SQLite-internal foreign keys (e.g., `messages.agent_id → agents.id`, `messages.conversation_id → conversations.id`, `artifacts.created_by_agent_id → agents.id`, `agent_run_checkpoints.run_id → agent_runs.id`) SHALL be preserved with `PRAGMA foreign_keys=ON` ensuring CASCADE deletes work.

Message persistence SHALL use direct writes. All StreamEvent types SHALL be persisted synchronously to the appropriate database. Events touching local tables (message.start/end, part events, tool events) SHALL write directly to SQLite. Usage events SHALL use fire-and-forget `asyncio.create_task`. Redis Stream write-behind, Redis KV cache, and Redis infrastructure integration are removed entirely.

#### Scenario: New conversation is created

- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** the conversation stores `user_id` of the creating user
- **AND** messages and runs can reference the conversation id

#### Scenario: User queries their conversations

- **WHEN** an authenticated user requests `/api/conversations`
- **THEN** only conversations where `user_id` matches the authenticated user AND `mode != 'guide'` are returned

#### Scenario: Guide agent column migration is idempotent

- **WHEN** the backend starts and the `is_guide` column already exists
- **THEN** the `ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide` statement is a no-op
- **AND** existing agents retain `is_guide=false`

#### Scenario: Guide conversation mode is persisted

- **WHEN** a conversation is created with `mode='guide'`
- **THEN** the `mode` column stores `'guide'`
- **AND** no DDL change is required because `mode` is a varchar column

#### Scenario: message.start is persisted directly to SQLite

- **WHEN** a `message.start` event is produced in dual-DB mode
- **THEN** the message row is INSERT'd directly into local SQLite
- **AND** the write completes in < 1ms
- **AND** no Redis Stream or background consumer is involved

#### Scenario: part.delta is persisted directly to SQLite

- **WHEN** a `part.delta` event is produced in dual-DB mode
- **THEN** the message parts are UPDATE'd directly in local SQLite
- **AND** the write completes in < 1ms

#### Scenario: message.end is persisted directly to SQLite

- **WHEN** a `message.end` event is produced in dual-DB mode
- **THEN** the message is UPDATE'd with final parts and `status='complete'` directly in local SQLite

#### Scenario: Usage events are fire-and-forget

- **WHEN** a `run.usage` or `message.usage` event is produced
- **THEN** AgentRunner schedules a fire-and-forget `asyncio.create_task` to UPDATE the database
- **AND** if the task fails, it is logged but does not block the stream

#### Scenario: Crash recovery with SQLite WAL

- **WHEN** the backend restarts after a crash in dual-DB mode
- **AND** some messages have `status='streaming'` in SQLite
- **THEN** `recovery_scan` SHALL scan for stuck streaming messages
- **AND** SHALL mark them as `interrupted`
- **AND** SQLite WAL mode SHALL have already replayed committed transactions automatically

#### Scenario: Server deployment mode (single PG)

- **WHEN** `DATABASE_LOCAL_URL` is not set
- **THEN** all tables are stored in PostgreSQL
- **AND** `get_local_db()` falls back to the remote session factory
- **AND** `get_db` alias points to `get_remote_db`
- **AND** behavior is identical to pre-dual-DB (except Redis code is removed, writes are synchronous)

## ADDED Requirements

### Requirement: Table routing SHALL classify models to local or remote engine

A table routing module (`backend/app/db/table_routing.py`) SHALL define two sets of table names: `LOCAL_TABLES` (10 conversation/config tables) and `REMOTE_TABLES` (12 user/knowledge tables). The `get_local_table_objects()` function SHALL return SQLAlchemy `Table` objects for local tables. The `get_remote_table_objects()` function SHALL return `Table` objects for remote tables. `init_db()` SHALL use these functions to call `create_all` with the correct table subset on each engine.

#### Scenario: Local tables created on SQLite engine

- **WHEN** `init_db()` runs in dual-DB mode
- **THEN** `Base.metadata.create_all` is called on the local SQLite engine with only `LOCAL_TABLES`
- **AND** remote tables are NOT created on the SQLite engine

#### Scenario: Remote tables created on PostgreSQL engine

- **WHEN** `init_db()` runs in dual-DB mode
- **THEN** `Base.metadata.create_all` is called on the remote PostgreSQL engine with only `REMOTE_TABLES`
- **AND** local tables are NOT created on the PostgreSQL engine (except shadow tables during transition)

### Requirement: Dual database engines SHALL initialize independently

`engine.py` SHALL maintain two independent engine/session-factory pairs: `_local_engine` / `_local_session_factory` (SQLite) and `_remote_engine` / `_remote_session_factory` (PostgreSQL). The local engine SHALL be initialized only when `DATABASE_LOCAL_URL` is set. The remote engine SHALL always be initialized. `get_local_db()` SHALL yield sessions from the local factory (or fall back to remote when local is not configured). `get_remote_db()` SHALL yield sessions from the remote factory. `get_db` SHALL be an alias for `get_remote_db` for backward compatibility during transition.

#### Scenario: Dual-DB mode initialization

- **WHEN** `DATABASE_LOCAL_URL` is set to a SQLite URL
- **THEN** both `_local_session_factory` and `_remote_session_factory` are initialized
- **AND** `get_local_db()` yields SQLite sessions
- **AND** `get_remote_db()` yields PostgreSQL sessions

#### Scenario: Server mode initialization

- **WHEN** `DATABASE_LOCAL_URL` is not set
- **THEN** only `_remote_session_factory` is initialized
- **AND** `get_local_db()` falls back to `_remote_session_factory`
- **AND** `get_db` is an alias for `get_remote_db`

### Requirement: Local SQLite SHALL use WAL mode for concurrent access

The local SQLite engine SHALL configure `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, and `PRAGMA busy_timeout=5000` on every connection. WAL mode enables concurrent read during write. `busy_timeout=5000` ensures write lock contention waits up to 5 seconds instead of immediately erroring.

#### Scenario: Concurrent read during write

- **WHEN** a write transaction is in progress on SQLite
- **AND** another connection attempts a SELECT
- **THEN** the SELECT succeeds without blocking (WAL read snapshot)

#### Scenario: Write lock contention in parallel subagent dispatch

- **WHEN** multiple subagent runs attempt concurrent INSERT/UPDATE on SQLite
- **THEN** writes are serialized by SQLite
- **AND** each write waits up to 5 seconds for the lock (`busy_timeout=5000`)
- **AND** single-user single-run scenarios never hit contention

### Requirement: Cross-database foreign keys SHALL be removed

The `user_id` columns on `conversations`, `agents`, and `mcp_servers` SHALL be plain `String` columns without `ForeignKey("users.id")` constraints. This is required because these tables reside in local SQLite while `users` resides in remote PostgreSQL. Data integrity SHALL be enforced at the application layer via JWT-derived `user_id` and `WHERE user_id = ?` query filtering. SQLite-internal foreign keys (e.g., `messages.agent_id → agents.id`) SHALL be preserved.

#### Scenario: Conversation created with user_id

- **WHEN** a conversation is created
- **THEN** the `user_id` column stores the authenticated user's ID as a plain string
- **AND** no database-level FK constraint validates the reference to `users.id`

#### Scenario: Agent references user_id

- **WHEN** a user-created agent is stored
- **THEN** the `user_id` column stores the creating user's ID
- **AND** builtin agents have `user_id IS NULL`

### Requirement: Process-internal cache SHALL replace Redis KV cache

Entity caching for remote cold data SHALL use a process-internal `dict` with TTL expiration, replacing the removed Redis KV cache. `UserSettings`, `GlobalSettings`, and `UserPreference` SHALL be cached in a module-level `dict` with 5-minute TTL. Cache invalidation SHALL clear the `dict` entry on write operations. `Agent` and `Workspace` SHALL be read directly from local SQLite (0.1ms) without caching. `LongTermMemory` recall path SHALL continue using `self.items` in-process memory; the management panel endpoint SHALL read from `self.items` instead of querying PostgreSQL directly.

#### Scenario: UserSettings cached in process memory

- **WHEN** `get_user_settings_cached(user_id)` is called
- **AND** a valid cache entry exists (not expired)
- **THEN** the cached value is returned without a database query

#### Scenario: UserSettings cache miss

- **WHEN** `get_user_settings_cached(user_id)` is called
- **AND** no cache entry exists or it has expired
- **THEN** the value is loaded from remote PostgreSQL
- **AND** the cache is populated with a 5-minute TTL

#### Scenario: UserSettings cache invalidation on write

- **WHEN** user settings are updated via the settings API
- **THEN** the process-internal cache entry for that `user_id` is cleared

#### Scenario: Agent read directly from SQLite

- **WHEN** `get_agent_cached(agent_id)` is called in dual-DB mode
- **THEN** the Agent is read directly from local SQLite
- **AND** no cache layer (Redis or process-internal) is involved

### Requirement: Column migration SHALL split by database dialect

The `_migrate_columns` function SHALL be split into `_migrate_columns_pg` (PostgreSQL-specific syntax using `ADD COLUMN IF NOT EXISTS` and `::jsonb` casts) and `_migrate_columns_sqlite` (SQLite-compatible syntax using try/except-wrapped `ALTER TABLE`). `init_db()` SHALL execute `_migrate_columns_pg` on the remote engine and `_migrate_columns_sqlite` on the local engine (when present). `create_all` handles new tables; migration statements handle new columns on existing tables.

#### Scenario: PG-specific migration runs on remote engine only

- **WHEN** `init_db()` runs in dual-DB mode
- **THEN** `_migrate_columns_pg` executes on the PostgreSQL engine
- **AND** does NOT execute on the SQLite engine

#### Scenario: SQLite migration handles unsupported syntax

- **WHEN** `_migrate_columns_sqlite` executes on the local engine
- **AND** a statement uses PG-specific syntax (e.g., `::jsonb`)
- **THEN** the statement fails and is silently swallowed (try/except)
- **AND** new columns on new tables are already created by `create_all`
