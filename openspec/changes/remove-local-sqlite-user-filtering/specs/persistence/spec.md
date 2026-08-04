# Spec Delta: persistence

## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The PostgreSQL schema MUST persist users, agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, global settings, and per-user settings. Remote ownership tables (`documents`) MUST include a `user_id` foreign key to the `users` table. Local tables (`agents`, `conversations`, `mcp_servers`, `model_profiles`, `workspaces`, etc.) MUST NOT include a `user_id` column — they are single-user tables in dual-DB mode. Builtin agents are identified by `is_builtin = true` (no `user_id IS NULL` check needed). The `agents` table SHALL include an `is_guide` boolean column (default `false`) to mark guide agents. The `conversations` table SHALL support `mode='guide'` as a valid string value.

#### Scenario: New conversation is created
- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** the conversation does NOT store `user_id` (column does not exist)
- **AND** messages and runs can reference the conversation id.

#### Scenario: User queries their conversations
- **WHEN** an authenticated user requests `/api/conversations`
- **THEN** all conversations where `mode != 'guide'` are returned (no `user_id` filter).

#### Scenario: Guide agent column migration is idempotent
- **WHEN** the backend starts and the `is_guide` column already exists
- **THEN** the `ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide` statement is a no-op
- **AND** existing agents retain `is_guide=false`.

#### Scenario: Guide conversation mode is persisted
- **WHEN** a conversation is created with `mode='guide'`
- **THEN** the `mode` column stores `'guide'`
- **AND** no DDL change is required because `mode` is a varchar column.

### Requirement: Table routing SHALL direct queries to the correct engine

The system MUST define `LOCAL_TABLES` (10 tables: messages, conversations, agent_runs, agent_run_checkpoints, artifacts, workspaces, attachments, conversation_context_summaries, agents, mcp_servers, model_profiles) and `REMOTE_TABLES` (12 tables: users, user_settings, user_preferences, global_settings, app_settings, rag_chunks, long_term_memory, chat_history, memory_nodes, memory_edges, documents, document_versions) as constants in `backend/app/db/table_routing.py`. `create_all` MUST only create each engine's corresponding tables. Local tables MUST NOT have `user_id` columns.

#### Scenario: Service queries a local table
- **WHEN** a service calls `get_local_db()` to read `messages`
- **THEN** the query hits the local SQLite engine
- **AND** no `user_id` filter is applied (single-user table).

#### Scenario: Service queries a remote table
- **WHEN** a service calls `get_remote_db()` to read `user_settings`
- **THEN** the query hits the remote PostgreSQL engine
- **AND** `user_id` filtering is applied where appropriate.

### Requirement: Cross-database foreign keys SHALL be removed

Local tables (`conversations`, `agents`, `mcp_servers`, `model_profiles`) MUST NOT have `user_id` columns or `ForeignKey("users.id")` constraints. SQLite-internal foreign keys (e.g., `messages.conversation_id → conversations.id`) MUST remain intact. Remote tables (`documents`, `rag_chunks`, etc.) MUST retain `user_id` for multi-user isolation on the PG side.

#### Scenario: Local table has no user_id column
- **WHEN** a conversation is created
- **THEN** no `user_id` is stored in the local SQLite database
- **AND** no database-level FK constraint to `users` exists.

#### Scenario: Remote table retains user_id
- **WHEN** a document is created
- **THEN** the `user_id` from the JWT context is stored in the remote PostgreSQL database
- **AND** data isolation is enforced at the application layer via JWT context.

### Requirement: ModelProfile SHALL be a local persistent entity without user scoping

A `model_profiles` table MUST persist ModelProfile records with columns: `id` (PK), `name`, `provider`, `model_id`, `api_key`, `api_base_url`, `is_default`, `supports_vision`, `last_test_status`, `last_tested_at`, `created_at`, `updated_at`. The table is a local table (SQLite in dual-DB mode) and MUST NOT have a `user_id` column. Only one profile MAY have `is_default = true` at a time (enforced by a partial unique index `WHERE is_default = true`). A one-time migration (`_migrate_agent_model_profiles`) SHALL copy baked-in model config from pre-migration `agents` rows into `model_profiles`, deduplicating by `(provider, model_id)`.

#### Scenario: ModelProfile is created
- **WHEN** a user creates a ModelProfile via the API
- **THEN** a row is inserted into `model_profiles` without `user_id`
- **AND** `is_default` is set to true if no other profile has `is_default = true`.

#### Scenario: ModelProfile default resolution
- **WHEN** `build_adapter_input` resolves the default ModelProfile
- **THEN** it queries `WHERE is_default = true` without `user_id` filtering
- **AND** returns the single default profile.

#### Scenario: Old agent model config is migrated
- **WHEN** the backend starts and `agents.model_provider` column still exists
- **THEN** the migration scans agents with non-null model config
- **AND** creates deduplicated ModelProfile records
- **AND** marks the earliest-created profile as default.

## REMOVED Requirements

### Requirement: Data migration SHALL back-fill existing rows

**Reason**: Local tables no longer have `user_id` columns, so back-filling is meaningless. Remote-table back-fill (documents, rag_chunks, etc.) is handled by the migration script separately.

**Migration**: The `migrate_to_multi_user.py` script is updated to only back-fill remote tables. Local-table back-fill code is removed. The `ALTER TABLE ... DROP COLUMN user_id` migration in `engine.py` handles existing databases.

### Requirement: Dual database architecture SHALL support local SQLite + remote PostgreSQL

**Reason**: The "single DB mode (backward compat)" scenario is no longer supported — dual-DB is the only deployment mode. The requirement is replaced with a simplified version.

**Migration**: `get_local_db()` always returns a SQLite session; the fallback to remote session factory is removed. `DATABASE_LOCAL_URL` is always required.
