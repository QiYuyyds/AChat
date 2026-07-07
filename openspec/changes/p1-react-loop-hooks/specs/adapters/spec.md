# Spec Delta: Adapters

## ADDED Requirements

### Requirement: SDK adapters SHALL implement call_once for single-turn execution

SDK adapters (Custom) MUST implement `call_once(input, cancel_event) -> AsyncIterator[StreamEvent]` that performs a single LLM API call and yields all events for that turn (message.start → parts → message.end → tool.call *). CLI adapters are exempt.

#### Scenario: call_once yields single-turn events

- **WHEN** AgentRunner calls `call_once` with a valid `AdapterInput` containing `messages`
- **THEN** the adapter performs one Chat Completions API call
- **AND** yields message.start, part.start/delta/end, tool.call events, and message.end
- **AND** does NOT execute any tools (tool execution is AgentRunner's responsibility).

#### Scenario: call_once with no tool calls

- **WHEN** the LLM response has no tool calls and finish_reason="stop"
- **THEN** the adapter yields message.start, text parts, message.end
- **AND** no tool.call events are yielded.

#### Scenario: CLI adapter call_once raises NotImplementedError

- **WHEN** AgentRunner calls `call_once` on a CLI adapter (Claude Code / Codex)
- **THEN** the adapter raises `NotImplementedError`
- **AND** AgentRunner falls back to the `stream` path.

### Requirement: AdapterInput SHALL carry the full messages list

`AdapterInput` MUST include a `messages: list[dict] | None` field containing the complete conversation history (system + history + user + prior turns). When `messages` is provided, the adapter MUST use it directly instead of constructing its own message list.

#### Scenario: call_once receives messages from AgentRunner

- **WHEN** AgentRunner calls `call_once` with `messages = [system, history..., user, assistant, tool_result, ...]`
- **THEN** the adapter passes `messages` to the Chat Completions API as-is
- **AND** does NOT prepend system prompt or history from `AdapterInput.system_prompt` / `AdapterInput.history`.

#### Scenario: Legacy stream path ignores messages field

- **WHEN** the `stream` method is called (CLI adapter or fallback)
- **AND** `messages` is None
- **THEN** the adapter constructs messages from `system_prompt`, `history`, and `prompt` as before.

## MODIFIED Requirements

### Requirement: Adapters SHALL translate provider output to StreamEvent

Each adapter MUST expose `stream(input, signal)` and yield only AChat `StreamEvent` objects to the application layer. SDK adapters SHALL additionally expose `call_once(input, signal)` for single-turn execution; when `call_once` is used, tool execution and the ReAct loop are managed by AgentRunner, not the adapter.

#### Scenario: Custom model emits tool calls

- **WHEN** Chat Completions streaming returns function tool call deltas
- **THEN** CustomAgentAdapter accumulates arguments
- **AND** emits AChat `tool.call` events
- **AND** does NOT execute the tool (AgentRunner executes it after `call_once` returns).

#### Scenario: Custom model emits tool calls via stream (legacy path)

- **WHEN** the `stream` method is used (fallback mode)
- **THEN** CustomAgentAdapter accumulates arguments, emits `tool.call` and `tool.result` events, and manages the ReAct loop internally.

#### Scenario: call_once yields tool calls for AgentRunner

- **WHEN** `call_once` is used and the LLM response includes tool calls
- **THEN** the adapter yields `tool.call` events with accumulated arguments
- **AND** AgentRunner executes the tools and feeds results back into `messages` for the next `call_once` invocation.
