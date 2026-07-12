# Persistence

## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The database schema MUST persist agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, and app settings. The `messages` table SHALL include a `hidden` boolean column to distinguish clone-subagent messages from normal conversation messages.

#### Scenario: New conversation is created
- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** messages and runs can reference the conversation id.

#### Scenario: Clone-subagent message is persisted
- **WHEN** a clone-subagent run persists a message via `persist_event`
- **AND** the run's `dispatch_visibility` is `'hidden'`
- **THEN** the message is stored with `hidden=true`

#### Scenario: Normal message is persisted
- **WHEN** a top-level run or visible group-member dispatch persists a message
- **THEN** the message is stored with `hidden=false`

#### Scenario: Database migration for hidden column
- **WHEN** the backend starts and the `hidden` column does not exist on `messages`
- **THEN** `engine.py` executes `ALTER TABLE messages ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE`
- **AND** existing messages default to `hidden=false`
