## MODIFIED Requirements

### Requirement: Custom agents SHALL receive bounded chat history
CustomAgentAdapter runs MUST receive serialized conversation history assembled via PromptAssembler instead of hardcoded context construction. The PromptAssembler SHALL apply Schema-driven slot filling with token budget trimming. History SHALL be built through `prompt_assembler.assemble(Query)` and rendered via `ctx.render_system_prompt()` and `ctx.render_history()`.

#### Scenario: Conversation has long history
- **WHEN** AgentRunner builds adapter input
- **THEN** it calls PromptAssembler which trims history to fit the model context window and output reserve.

#### Scenario: Context includes memory and RAG augmentation
- **WHEN** a conversation is built for an agent with memory/RAG capabilities
- **THEN** the assembled context includes recall_memory slot items from LongTerm memory
- **AND** profile slot items from agent system prompt and stored preferences

### Requirement: Sub-agent prompts SHALL not duplicate global history
Orchestrator-dispatched child runs MUST use their isolated task prompt and skip PromptAssembler context injection.

#### Scenario: Orchestrator dispatches a child task
- **WHEN** `overridePrompt` is set
- **THEN** AgentRunner does not call PromptAssembler for that child run.

## ADDED Requirements

### Requirement: BuildHistoryOptions SHALL remain backward compatible
The existing `BuildHistoryOptions` interface MUST continue to work after PromptAssembler integration. The `build_history_for()` function SHALL accept the same parameters and return the same message format.

#### Scenario: Existing API calls work unchanged
- **WHEN** `build_history_for(agent_id, conversation_id, options)` is called
- **THEN** the function internally delegates to PromptAssembler
- **AND** returns the same message list format as before
