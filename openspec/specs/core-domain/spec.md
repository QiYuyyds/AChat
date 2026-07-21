# Core Domain

## Purpose

Defines AChat's durable entities and ownership boundaries. Detailed field notes live in `specs/01-core-entities.md`.

## Requirements

### Requirement: User SHALL be a core entity

AChat SHALL model `User` as a core domain entity representing an authenticated person who owns agents, conversations, documents, and settings. Each User SHALL have a unique email, a display name, a bcrypt password hash, a token version for session invalidation, and timestamps.

#### Scenario: User owns a conversation
- **WHEN** a user creates a conversation
- **THEN** the conversation record stores `user_id` referencing the creating user
- **AND** only that user can read or modify the conversation.

#### Scenario: User owns a custom agent
- **WHEN** a user creates a custom agent
- **THEN** the agent record stores `user_id` referencing the creating user
- **AND** only that user can see or modify the agent.

### Requirement: Core entities SHALL remain normalized

AChat SHALL model User, Agent, Conversation, Message, Artifact, Workspace, Tool, and AgentRun as separate domain concepts with explicit references instead of embedding unrelated lifecycle data into a single record. User SHALL be the ownership root for Agent, Conversation, and Document entities.

#### Scenario: Message references an artifact
- **WHEN** an agent creates an artifact during a run
- **THEN** the message contains an `artifact_ref` part
- **AND** the artifact content and version metadata remain in the artifacts table.

#### Scenario: Conversation is owned by a user
- **WHEN** a conversation is created
- **THEN** it stores a non-null `user_id` foreign key to the users table
- **AND** all child records (messages, artifacts, workspaces, runs) inherit ownership through the conversation.

### Requirement: Agents SHALL route through an adapter name

Every runnable agent MUST declare an `adapterName` of `custom`, `claude-code`, `codex`, or `mock`, and AgentRunner SHALL use that value to resolve the adapter. Builtin agents (`is_builtin=true`) SHALL have a NULL `user_id` and be shared across all users. Custom agents SHALL have a non-null `user_id` and be visible only to their owner. A guide agent (`is_guide=true`) SHALL be a builtin management-only agent that skips baseline tool merging and owns only management tools.

#### Scenario: Codex agent is configured
- **WHEN** an agent has `adapterName='codex'`
- **THEN** `modelProvider` is ignored
- **AND** `toolNames` is forced to an empty list because Codex uses SDK-provided tools.

#### Scenario: Builtin agent is shared
- **WHEN** a user lists agents
- **THEN** the response includes builtin agents (`user_id IS NULL`) and the user's custom agents
- **AND** excludes other users' custom agents.

#### Scenario: Guide agent is builtin and management-only
- **WHEN** an agent has `is_guide=true`
- **THEN** it MUST also have `is_builtin=true` and `user_id=NULL`
- **AND** AgentRunner skips baseline tool merging for this agent
- **AND** the agent's effective tools are only the 7 management tools plus `ask_user`.

### Requirement: Conversations SHALL own workspace policy

Each conversation SHALL have exactly one workspace record that determines effective cwd, filesystem approval mode, pinned message ids, and local vs sandbox workspace semantics. The workspace root path SHALL be scoped under the owning user's directory. A guide conversation (`mode='guide'`) SHALL have an empty sandbox workspace and SHALL NOT appear in conversation lists.

#### Scenario: Local workspace conversation runs a tool
- **WHEN** a tool receives a relative path
- **THEN** the path is resolved under the conversation's effective cwd
- **AND** writes outside that tree are rejected.

#### Scenario: Workspace path is user-scoped
- **WHEN** a workspace is created for a conversation
- **THEN** its root path includes the owning user's id segment
- **AND** filesystem access is confined to that user's directory tree.

#### Scenario: Guide conversation has empty sandbox workspace
- **WHEN** a conversation is created with `mode='guide'`
- **THEN** its workspace mode is `sandbox` with no meaningful bound path content
- **AND** the guide agent's tools do not depend on the workspace path
- **AND** the conversation is excluded from `list_conversations` results.

### Requirement: Agent runs SHALL be auditable

Each agent execution MUST create an AgentRun record with trigger message, parent run if any, status, timestamps, and usage when reported.

#### Scenario: Adapter throws
- **WHEN** an adapter stream fails
- **THEN** the AgentRun status becomes `failed`
- **AND** the user sees an error message in the conversation.
