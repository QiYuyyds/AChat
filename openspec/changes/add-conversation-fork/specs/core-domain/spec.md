## MODIFIED Requirements

### Requirement: Conversation entity SHALL support fork relationships

The `Conversation` entity MUST include two optional fields for tracking fork origin: `parentConversationId` (the source conversation this one was forked from) and `forkPointMessageId` (the message ID in the source conversation at which the fork occurred). These fields are nullable — conversations created normally have both set to `null`. Forked conversations have both set.

#### Scenario: Normal conversation has no fork parent

- **WHEN** a conversation is created via `create_conversation`
- **THEN** `parentConversationId` is `null`
- **AND** `forkPointMessageId` is `null`

#### Scenario: Forked conversation has fork parent

- **WHEN** a conversation is created via `fork_conversation`
- **THEN** `parentConversationId` is set to the source conversation ID
- **AND** `forkPointMessageId` is set to the fork-point message ID
- **AND** the conversation inherits `agentIds`, `mode`, `dispatchMode`, and `fsWriteApprovalMode` from the source

#### Scenario: Forked conversation is independent

- **WHEN** a forked conversation exists
- **THEN** it has its own `Workspace` (1:1)
- **AND** it has its own `Messages` (deep-copied from source up to fork point)
- **AND** it has its own `Artifacts` (deep-copied from source up to fork point)
- **AND** it has its own `AgentRuns` (empty at creation, new runs add to it)
- **AND** it has its own `pinnedMessageIds` (empty at creation)
- **AND** it has its own `bookmarkedMessageIds` (empty at creation)
