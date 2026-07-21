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

Runtime keys MUST resolve in this order: per-agent key, per-user settings key, environment key, and provider-specific SDK fallback where documented.

#### Scenario: Agent has a custom key
- **WHEN** `agents.api_key` is non-empty
- **THEN** AgentRunner uses it instead of user settings or environment variables.

#### Scenario: User has a key in settings
- **WHEN** `agents.api_key` is empty
- **AND** `user_settings.openai_api_key` is non-empty for the authenticated user
- **THEN** AgentRunner uses the user's settings key.

### Requirement: Base URLs SHALL be adapter-specific

`agents.api_base_url` MUST be interpreted according to adapter protocol: Anthropic-compatible for Claude Code and Codex/Responses-compatible for Codex.

#### Scenario: Codex base URL is set
- **WHEN** a Codex agent has `api_base_url`
- **THEN** it is passed to Codex SDK as `baseUrl`
- **AND** it must not be sourced from global CC Switch config.

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
