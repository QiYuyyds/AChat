# Persistence

## MODIFIED Requirements

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
