## MODIFIED Requirements

### Requirement: Core entities SHALL remain normalized
AgentHub SHALL model Agent, Conversation, Message, Artifact, Workspace, Tool, and AgentRun as separate domain concepts with explicit references. The domain SHALL be extended with LongTermMemory, UserPreference, RagChunk, ChatHistory, MemoryNode, and MemoryEdge entities to support the memory and RAG subsystems.

#### Scenario: Message references an artifact
- **WHEN** an agent creates an artifact during a run
- **THEN** the message contains an `artifact_ref` part
- **AND** the artifact content and version metadata remain in the artifacts table.

#### Scenario: Memory entities are independent from conversation entities
- **WHEN** a LongTermMemory record is created
- **THEN** it is stored in its own table without embedding conversation-specific foreign keys
- **AND** it can be recalled across any conversation

### Requirement: Agent runs SHALL be auditable
Each agent execution MUST create an AgentRun record with trigger message, parent run if any, status, timestamps, and usage when reported. AgentRun SHALL additionally support memory write hooks that execute as background tasks after run finalization.

#### Scenario: Adapter throws
- **WHEN** an adapter stream fails
- **THEN** the AgentRun status becomes `failed`
- **AND** the user sees an error message in the conversation.

#### Scenario: Memory hook runs after successful agent run
- **WHEN** an agent run completes successfully
- **THEN** a background asyncio.Task extracts facts and writes to LongTermMemory
- **AND** hook failures are logged but do not affect the run status

## ADDED Requirements

### Requirement: Memory and RAG entities SHALL be queryable across conversations
LongTermMemory, UserPreference, and RagChunk entities SHALL be global (not scoped to a single conversation). This enables cross-conversation memory recall and knowledge base search.

#### Scenario: Memory from one conversation is recalled in another
- **WHEN** a fact is stored during conversation A
- **THEN** it can be recalled during conversation B via memory_recall or RecallSource
