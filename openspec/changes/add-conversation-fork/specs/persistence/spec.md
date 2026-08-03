## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities

The database schema MUST persist agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, and app settings. The `conversations` table SHALL include `parent_conversation_id` and `fork_point_message_id` nullable columns to support conversation fork relationships. The `messages` table SHALL include a `hidden` boolean column to distinguish clone-subagent messages from normal conversation messages.

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

#### Scenario: Database migration for fork columns

- **WHEN** the backend starts and the `parent_conversation_id` or `fork_point_message_id` columns do not exist on `conversations`
- **THEN** `engine.py` executes `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS parent_conversation_id TEXT` and `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS fork_point_message_id TEXT`
- **AND** existing conversations default to `parent_conversation_id = NULL` and `fork_point_message_id = NULL`

#### Scenario: Forked conversation is persisted

- **WHEN** `fork_conversation` creates a new conversation
- **THEN** the `conversations` row includes `parent_conversation_id` set to the source conversation ID
- **AND** `fork_point_message_id` set to the fork-point message ID from the source conversation
- **AND** a `workspaces` row is created for the new conversation with `mode = 'sandbox'` and `bound_path = NULL`
