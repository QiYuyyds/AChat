# Conversation Context

## MODIFIED Requirements

### Requirement: Sub-agent prompts SHALL not duplicate global history

Orchestrator-dispatched child runs MUST use their isolated task prompt and skip generic conversation history injection. Additionally, clone-subagent messages (those with `hidden=true`) MUST be excluded from `build_history_for` results to prevent context pollution.

#### Scenario: Orchestrator dispatches a child task
- **WHEN** `override_prompt` is set on `RunArgs`
- **THEN** AgentRunner does not call `build_history_for` for that child run

#### Scenario: Clone-subagent messages are excluded from history
- **WHEN** `build_history_for` queries messages for a conversation
- **AND** some messages have `hidden=true` (from clone-subagent runs)
- **THEN** those hidden messages are excluded from the query results
- **AND** only `hidden=false` messages are included in the serialized history

#### Scenario: Group-member dispatch messages remain in history
- **WHEN** a group member is dispatched in coordinated mode
- **AND** the group member's messages have `hidden=false`
- **THEN** those messages are included in subsequent `build_history_for` results
