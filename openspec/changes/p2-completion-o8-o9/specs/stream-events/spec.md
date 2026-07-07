# Spec Delta: Stream Events

## ADDED Requirements

### Requirement: Turn metric events SHALL provide per-turn usage data

The AgentRunner's `_run_react_loop` MUST yield a `TurnMetricEvent` after each turn's tool execution is complete and deferred events (message.usage + message.end) have been yielded, but before the `post_turn` hook is dispatched. The event MUST contain the turn number (1-based), the turn's token breakdown (input, output, cacheRead), the list of tool names called in that turn, and the turn's duration in milliseconds.

The `turn.metric` event type MUST be a new `StreamEvent` type. Frontend clients that do not recognize `turn.metric` MUST silently ignore it (backward compatible).

#### Scenario: Turn metric emitted after each turn

- **WHEN** `_run_react_loop` completes turn 2 (LLM response + tool execution)
- **AND** `message.usage` and `message.end` have been yielded
- **THEN** a `TurnMetricEvent` is yielded with `turn=2`, `tokens={input, output, cacheRead}`, `tool_calls=["fs_read", "bash"]`, `duration_ms=<elapsed>`

#### Scenario: Turn metric for turn with no tool calls

- **WHEN** `_run_react_loop` completes a turn where the LLM produced text only (no tool calls, finish_reason=stop)
- **THEN** a `TurnMetricEvent` is yielded with `tool_calls=[]` (empty list)

#### Scenario: CLI adapter path does not emit turn metrics

- **WHEN** a CLI adapter (Claude Code / Codex) run uses the `stream` path (not `_run_react_loop`)
- **THEN** no `TurnMetricEvent` is yielded (CLI manages its own loop, turn-level data is unavailable)

#### Scenario: Frontend ignores unknown event type

- **WHEN** a frontend client receives a `turn.metric` event but does not have a reducer for it
- **THEN** the event is silently dropped
- **AND** no error is raised
