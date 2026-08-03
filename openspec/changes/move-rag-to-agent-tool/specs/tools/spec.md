## ADDED Requirements

### Requirement: rag_search SHALL be an agent-level opt-in tool

AChat MUST register `rag_search` in `toolRegistry`. The tool MUST be available as a UI-selectable tool in the agent builder (included in `AVAILABLE_AGENT_TOOLS`). An agent gains `rag_search` only when it is present in the agent's `toolNames`. AChat MUST NOT implicitly inject `rag_search` based on conversation state.

#### Scenario: Custom agent enables rag_search

- **WHEN** a custom agent's `toolNames` includes `rag_search`
- **THEN** AgentRunner resolves the tool from `toolRegistry` during baseline merge
- **AND** the agent can call `rag_search({ query })` to retrieve knowledge-base passages

#### Scenario: Custom agent without rag_search

- **WHEN** a custom agent's `toolNames` does not include `rag_search`
- **THEN** the agent does NOT have access to `rag_search`
- **AND** AChat does NOT implicitly inject it

#### Scenario: SDK agents are out of scope

- **WHEN** an agent uses an SDK adapter (`claude-code` or `codex`)
- **THEN** `rag_search` is not available because SDK agents use their own CLI built-in tool sets and do not participate in baseline merge

#### Scenario: rag_search prompt guidance

- **WHEN** a run includes `rag_search` in its tool list
- **THEN** the injected system prompt guidance includes a `rag_search` usage instruction
- **AND** does NOT include instructions for `rag_ingest`, `rag_list_documents`, or `rag_delete_document` (those tools are not injected into any agent)

## REMOVED Requirements

### Requirement: Runtime SHALL inject RAG tools based on conversation state

**Reason**: RAG tool triggering is moving from conversation-level (`Conversation.rag_enabled` flag) to agent-level (`agent.toolNames` opt-in). The `rag_enabled` flag is deprecated and no longer read by `agent_runner`. `rag_search` is now resolved through the standard baseline-merge path like other UI-selectable tools.

**Migration**: Agents that previously relied on the conversation-level `rag_enabled` toggle must be edited to include `rag_search` in their `toolNames`. The `researcher` role preset is updated to include `rag_search` automatically. Existing conversations with `rag_enabled=true` will no longer trigger RAG tool injection; the `rag_enabled` column is retained in the database but not read.
