# Spec Delta: Adapters

## ADDED Requirements

### Requirement: _run_react_loop SHALL yield TurnMetricEvent per turn

`_run_react_loop` MUST record the start time of each turn (using `time.monotonic()`) before calling `call_once`. After tool execution is complete and deferred events (message.usage + message.end) have been yielded, but before the `post_turn` hook is dispatched, the loop MUST yield a `TurnMetricEvent` containing: the 1-based turn number, the turn's token usage (extracted from the `message.usage` event accumulated during `call_once`), the list of tool names called in that turn, and the duration in milliseconds (computed as `time.monotonic() - turn_start`).

#### Scenario: Turn metric includes correct token data

- **WHEN** turn 3's `message.usage` event reports `input_tokens=1200, output_tokens=800, cache_read_tokens=500`
- **THEN** the `TurnMetricEvent` for turn 3 contains `tokens={input: 1200, output: 800, cacheRead: 500}`

#### Scenario: Turn metric includes tool call names

- **WHEN** turn 2's LLM response includes tool calls for `fs_read` and `bash`
- **THEN** the `TurnMetricEvent` for turn 2 contains `tool_calls=["fs_read", "bash"]`

#### Scenario: Turn metric duration measures call_once through tool execution

- **WHEN** `call_once` starts at T0 and tool execution finishes at T1
- **THEN** the `TurnMetricEvent` for that turn contains `duration_ms = (T1 - T0) * 1000` (rounded to int)

#### Scenario: on_run_start dispatch captures inject return value

- **WHEN** `on_run_start` hook dispatch returns `HookResult(action="inject")`
- **THEN** `_run_react_loop` appends the injected system hint messages to `messages` before the first `call_once` call
- **AND** the inject logic is the same as `post_tool_use` inject handling
