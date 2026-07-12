# Frontend

## MODIFIED Requirements

### Requirement: Store reducers SHALL apply StreamEvent deterministically

Zustand reducers MUST update conversation, message, artifact, pending write, pending bash command, dispatch, and usage state from `StreamEvent` payloads. Hidden messages (from clone-subagent runs) SHALL NOT be rendered in the chat message list. Token usage from subagent runs SHALL be attributed to the top-level parent agent.

#### Scenario: `part.delta` arrives
- **WHEN** the event references an existing part
- **THEN** the store appends content to that part without reordering other parts.

#### Scenario: A failed run leaves an open tool call
- **WHEN** `run.end` arrives with `status='failed'` or `status='aborted'`
- **THEN** the store marks streaming messages from that run as terminal
- **AND** appends local error `tool_result` parts for any unmatched `tool_use` call ids.

#### Scenario: Hidden message is not rendered
- **WHEN** a message with `hidden=true` is loaded from the API
- **THEN** the message list does not render it in the chat view

#### Scenario: Subagent token usage rolls up to parent agent
- **WHEN** a run has `parent_run_id` set (subagent run)
- **THEN** the usage hook walks the `parent_run_id` chain to find the top-level run
- **AND** attributes the subagent's tokens to the top-level run's `agent_id`
- **AND** increments the parent agent's `subagentTokens` and `subagentRunCount`

#### Scenario: Usage panel displays subagent annotation
- **WHEN** an agent has `subagentTokens > 0`
- **THEN** the `AgentUsageCard` renders an additional line: "含 subagent: Nk tok · M 次"
