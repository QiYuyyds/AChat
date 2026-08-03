# Persistence

## Purpose

Defines PostgreSQL/SQLAlchemy physical schema and key storage. Detailed schema notes live in `specs/08-db-schema.md`.

## Requirements

### Requirement: Database schema SHALL map domain entities

The PostgreSQL schema MUST persist users, agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, global settings, and per-user settings. Ownership tables (`agents`, `conversations`, `documents`, `mcp_servers`) MUST include a `user_id` foreign key to the `users` table. Builtin agents MAY have a NULL `user_id`. The `agents` table SHALL include an `is_guide` boolean column (default `false`) to mark guide agents. The `conversations` table SHALL support `mode='guide'` as a valid string value (no DDL change needed since `mode` is a varchar column).

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

### Requirement: JSON columns SHALL store typed unions

JSON columns such as `messages.parts`, `artifacts.content`, and usage payloads MUST correspond to TypeScript union types in shared code.

#### Scenario: Message parts are loaded
- **WHEN** the UI fetches messages
- **THEN** each part can be rendered by its discriminant without ad hoc parsing.

### Requirement: Users table SHALL store identity and credentials

AChat MUST persist a `users` table with columns: `id` (UUID PK), `email` (unique, NOT NULL), `name` (NOT NULL), `password_hash` (bcrypt, NOT NULL), `avatar_url` (nullable), `token_version` (integer, default 0), `created_at`, `updated_at`.

#### Scenario: New user is created
- **WHEN** a registration succeeds
- **THEN** a row is inserted into `users` with a bcrypt-hashed password and `token_version=0`.

### Requirement: Global settings SHALL be stored separately from per-user settings

AChat MUST persist a `global_settings` single-row table (PK = `'singleton'`) for server-level configuration shared across all users: `deployment_publish_enabled`, `deployment_publish_dir`, `deployment_public_base_url`.

#### Scenario: Deployment config is read
- **WHEN** any user triggers a deployment
- **THEN** the deployment service reads from `global_settings`
- **AND** all users share the same deployment configuration.

### Requirement: API keys SHALL follow defined precedence

Runtime keys for SDK (Custom) agents MUST resolve from ModelProfile: the `modelProfileId` attached to the message is checked first, then the user's default ModelProfile. If no ModelProfile exists, the run is refused. CLI agents (Claude Code, Codex) use CLI-built-in authentication and do not use ModelProfile keys. Per-user settings keys (`user_settings`) remain for RAG, memory, and other non-agent subsystems.

#### Scenario: SDK agent run with explicit ModelProfile
- **WHEN** a Custom adapter agent runs with a `modelProfileId`
- **THEN** AgentRunner resolves `api_key` from that ModelProfile
- **AND** uses it for the Chat Completions request.

#### Scenario: SDK agent run with default ModelProfile
- **WHEN** a Custom adapter agent runs without `modelProfileId`
- **THEN** AgentRunner resolves `api_key` from the user's default ModelProfile.

### Requirement: Base URLs SHALL be adapter-specific

ModelProfile `api_base_url` MUST be interpreted according to adapter protocol: Chat Completions-compatible for Custom adapter. CLI agents (Claude Code, Codex) use their own CLI defaults and do not use ModelProfile base URLs.

#### Scenario: Custom agent base URL is set
- **WHEN** a Custom adapter agent runs with a ModelProfile that has `api_base_url`
- **THEN** it is passed to the Chat Completions SDK as `base_url`.

### Requirement: App settings SHALL be per-user storage

Per-user API keys and companion configuration MUST be stored in a `user_settings` table (PK = `user_id`) rather than a single-row `app_settings` table. Each user SHALL have their own `anthropic_api_key`, `openai_api_key`, `deepseek_api_key`, `ark_api_key`, `companion_mode`, and `mobile_device_token`.

#### Scenario: User saves OpenAI key in settings
- **WHEN** the settings API receives the key from an authenticated user
- **THEN** it normalizes empty strings to null
- **AND** stores the value in `user_settings` scoped to that user's `user_id`.

#### Scenario: User saves external deployment publishing settings
- **WHEN** the settings API receives `deployment_publish_enabled`, `deployment_publish_dir`, or `deployment_public_base_url`
- **THEN** these values are stored in `global_settings` (shared)
- **AND** normalizes empty strings to null.

### Requirement: Memory and knowledge tables SHALL isolate by user

`long_term_memory`, `memory_nodes`, `chat_history`, and `user_preferences` MUST include a `user_id` column for per-user isolation. RAG chunk tables (`rag_chunks`) MUST be isolated via their parent document's `user_id`. Milvus collection and Elasticsearch index MUST include a `user_id` metadata field for filtered search.

#### Scenario: User recalls long-term memories
- **WHEN** an authenticated user's agent recalls memories
- **THEN** only memories with matching `user_id` are returned.

#### Scenario: RAG search is performed
- **WHEN** an authenticated user triggers RAG retrieval
- **THEN** Milvus and ES queries include a `user_id` filter
- **AND** only chunks belonging to that user's documents are returned.

### Requirement: Data migration SHALL back-fill existing rows

A migration script MUST create a default user and back-fill `user_id` on all existing rows before enforcing NOT NULL constraints. The default user's email and password SHALL be configurable via environment variables.

#### Scenario: Migration runs on existing single-user database
- **WHEN** the migration script executes
- **THEN** a default user is created
- **AND** all existing agents, conversations, documents, and settings rows are assigned `user_id = default_user.id`
- **AND** the script prints the default user's credentials to stdout.

### Requirement: Dual database architecture SHALL support local SQLite + remote PostgreSQL

When `DATABASE_LOCAL_URL` is set, the system MUST initialize two database engines: a local SQLite engine (WAL mode, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`) for 10 conversation/config tables, and a remote PostgreSQL engine for 12 user-system/RAG tables. When unset, the system MUST fall back to a single PostgreSQL engine (backward compatibility). Redis is fully removed; caching is replaced by process-internal dict TTL (5min) or direct SQLite reads.

#### Scenario: Dual DB mode is enabled
- **WHEN** `DATABASE_LOCAL_URL` is set to a SQLite path
- **THEN** the local engine creates 10 local tables and the remote engine creates 12 remote tables
- **AND** `get_local_db()` returns a SQLite session, `get_remote_db()` returns a PostgreSQL session
- **AND** `get_db` is an alias for `get_remote_db` (transitional compatibility)

#### Scenario: Single DB mode (backward compat)
- **WHEN** `DATABASE_LOCAL_URL` is not set
- **THEN** `get_local_db()` falls back to the remote PostgreSQL session
- **AND** all tables exist in the single PostgreSQL database
- **AND** behavior is identical to the pre-dual-DB system

### Requirement: Table routing SHALL direct queries to the correct engine

The system MUST define `LOCAL_TABLES` (10 tables: messages, conversations, agent_runs, agent_run_checkpoints, artifacts, workspaces, attachments, conversation_context_summaries, agents, mcp_servers) and `REMOTE_TABLES` (12 tables: users, user_settings, user_preferences, global_settings, app_settings, rag_chunks, long_term_memory, chat_history, memory_nodes, memory_edges, documents, document_versions) as constants in `backend/app/db/table_routing.py`. `create_all` MUST only create each engine's corresponding tables.

#### Scenario: Service queries a local table
- **WHEN** a service calls `get_local_db()` to read `messages`
- **THEN** the query hits the local SQLite engine (or PostgreSQL in single-DB mode)

#### Scenario: Service queries a remote table
- **WHEN** a service calls `get_remote_db()` to read `user_settings`
- **THEN** the query hits the remote PostgreSQL engine

### Requirement: Cross-database foreign keys SHALL be removed

`conversations.user_id`, `agents.user_id`, and `mcp_servers.user_id` MUST be plain String columns without `ForeignKey("users.id")` constraints, since they reference a table in a different database. Data isolation MUST be enforced at the application layer via JWT context. SQLite-internal foreign keys (e.g., `messages.agent_id → agents.id`) MUST remain intact.

#### Scenario: Local table references remote user
- **WHEN** a conversation is created with `user_id` from the JWT context
- **THEN** the `user_id` is stored as a plain string in the local SQLite database
- **AND** no database-level FK constraint is enforced across databases

### Requirement: Process-internal cache SHALL replace Redis

Redis KV cache and Stream write-behind MUST be fully removed. Caching for remote-table entities (`UserSettings`, `GlobalSettings`, `UserPreference`) MUST use a process-internal dict with 5-minute TTL. Local-table entities (`Agent`, `Workspace`) MUST be read directly from SQLite (0.1ms, no cache needed). `LongTermMemory` recall MUST use the in-memory `items` list. `persist_event` MUST write directly to SQLite (no Redis Stream buffer).

#### Scenario: UserSettings is cached in-process
- **WHEN** `build_adapter_input` reads `UserSettings` for a run
- **THEN** the process-internal dict cache is checked first (0ms)
- **AND** on miss, the remote PostgreSQL is queried and the result is cached for 5 minutes

#### Scenario: Agent is read directly from SQLite
- **WHEN** `get_agent_cached` is called
- **THEN** the local SQLite is read directly (0.1ms)
- **AND** no Redis or external cache is involved

### Requirement: ModelProfile SHALL be a user-scoped persistent entity

A `model_profiles` table MUST persist ModelProfile records with columns: `id` (PK), `user_id` (FK to users, CASCADE), `name`, `provider`, `model_id`, `api_key`, `api_base_url`, `is_default`, `supports_vision`, `last_test_status`, `last_tested_at`, `created_at`, `updated_at`. The table is a local table (SQLite in dual-DB mode). A one-time migration (`_migrate_agent_model_profiles`) SHALL copy baked-in model config from pre-migration `agents` rows into `model_profiles`, deduplicating by `(user_id, provider, model_id)`.

#### Scenario: ModelProfile is created
- **WHEN** a user creates a ModelProfile via the API
- **THEN** a row is inserted into `model_profiles` with the user's `user_id`
- **AND** `is_default` is set to true if it is the user's first profile.

#### Scenario: Old agent model config is migrated
- **WHEN** the backend starts and `agents.model_provider` column still exists
- **THEN** the migration scans agents with non-null model config
- **AND** creates deduplicated ModelProfile records per user
- **AND** marks the earliest-created profile as default
- **AND** skips builtin agents (user_id IS NULL).
