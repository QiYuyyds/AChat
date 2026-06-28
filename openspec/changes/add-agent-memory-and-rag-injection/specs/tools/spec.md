## MODIFIED Requirements

### Requirement: Tool definitions SHALL be registered centrally

AChat-managed tools MUST be registered through `toolRegistry` with name, description, JSON schema, and handler.

#### Scenario: Custom agent enables a tool
- **WHEN** an agent's `toolNames` includes `fs_read`
- **THEN** CustomAgentAdapter resolves the tool definition from `toolRegistry`.

### Requirement: Runtime SHALL inject memory_recall for custom agents

The tool resolution pipeline MUST automatically include `memory_recall` in the resolved tool list for all custom adapter agents, regardless of the agent's configured `tool_names`.

#### Scenario: Custom agent run starts
- **WHEN** `agent_runner` resolves tools for a custom adapter agent
- **THEN** `memory_recall` is appended to the tool list if not already present
- **AND** an INFO log entry records the injection

#### Scenario: SDK adapter agent run starts
- **WHEN** `agent_runner` resolves tools for a `claude-code` or `codex` adapter agent
- **THEN** no implicit tool injection occurs

### Requirement: Runtime SHALL inject RAG tools based on conversation state

The message routing pipeline MUST dynamically inject RAG tools into responder agents' tool lists when the conversation has `rag_enabled=true`.

#### Scenario: Conversation has RAG enabled
- **WHEN** `conversation_service.send_message()` processes a message in a conversation with `rag_enabled=true`
- **THEN** each responder's `tool_names` is augmented with `rag_search`, `rag_ingest`, `rag_list_documents`, and `rag_delete_document` before being passed to `agent_runner`

#### Scenario: Conversation has RAG disabled
- **WHEN** `conversation_service.send_message()` processes a message in a conversation with `rag_enabled=false`
- **THEN** no RAG tools are injected
