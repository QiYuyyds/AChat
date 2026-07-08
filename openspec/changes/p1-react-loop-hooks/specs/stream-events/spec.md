# Spec Delta: Stream Events

## MODIFIED Requirements

### Requirement: Adapters SHALL translate provider output to StreamEvent

Each adapter MUST expose `stream(input, signal)` and yield only AChat `StreamEvent` objects to the application layer. For SDK adapters using `call_once`, `tool.call` events are yielded by the adapter, while `tool.result` events are yielded by AgentRunner after tool execution. The event types and field contracts remain unchanged.

#### Scenario: SDK adapter yields tool.call without tool.result

- **WHEN** `call_once` is used and the LLM response includes tool calls
- **THEN** the adapter yields `tool.call` events with `call_id`, `tool_name`, and `args`
- **AND** does NOT yield `tool.result` events (AgentRunner yields them after execution).

#### Scenario: AgentRunner yields tool.result after execution

- **WHEN** AgentRunner executes a tool via `execute_with_hooks`
- **THEN** it yields a `tool.result` event with `call_id`, `result`, and `is_error` fields
- **AND** the event is persisted and published through the same path as adapter-produced events.

#### Scenario: Legacy stream path yields both tool.call and tool.result

- **WHEN** the `stream` method is used (CLI adapter or fallback)
- **THEN** the adapter yields both `tool.call` and `tool.result` events as before
- **AND** the event contract is unchanged.
