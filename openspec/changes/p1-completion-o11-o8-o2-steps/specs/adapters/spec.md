# Spec Delta: Adapters

## ADDED Requirements

### Requirement: ReAct loop SHALL cache read-only tool results within a single run

`_run_react_loop` MUST maintain a `tool_call_cache` dictionary keyed by `"{tool_name}:{json.dumps(args, sort_keys=True)}"`. When a cached entry exists for a read-only tool call, the cached result MUST be returned instead of executing the tool again. The cache MUST only apply to read-only tools: `fs_read`, `read_artifact`, `read_attachment`.

#### Scenario: Repeated fs_read hits cache

- **WHEN** the LLM calls `fs_read({"path": "src/index.ts"})` in turn 2
- **AND** calls `fs_read({"path": "src/index.ts"})` again in turn 4
- **THEN** the second call returns the cached result from turn 2
- **AND** the result is prefixed with `[cached]`
- **AND** the tool is NOT executed a second time.

#### Scenario: fs_write is never cached

- **WHEN** the LLM calls `fs_write({"path": "a.py", "content": "v1"})` in turn 1
- **AND** calls `fs_write({"path": "a.py", "content": "v2"})` in turn 3
- **THEN** both calls are executed (cache is not consulted for `fs_write`)
- **AND** the file is written twice.

#### Scenario: Different args do not hit cache

- **WHEN** `fs_read({"path": "a.py"})` is called in turn 1
- **AND** `fs_read({"path": "b.py"})` is called in turn 2
- **THEN** the second call does NOT hit the cache
- **AND** the tool is executed normally.

#### Scenario: Cache is per-run, not shared

- **WHEN** run A calls `fs_read({"path": "a.py"})` and caches the result
- **AND** run B (different run_id) calls `fs_read({"path": "a.py"})`
- **THEN** run B does NOT hit run A's cache
- **AND** the tool is executed for run B.

### Requirement: ReAct loop SHALL enforce token budget control

`_run_react_loop` MUST estimate the total token count of the `messages` list at the start of each turn. When the estimated tokens exceed 90% of the model's context window, a mid-run compact MUST be triggered. When estimated tokens exceed 95%, the loop MUST stop.

#### Scenario: Token usage exceeds 90% triggers mid-run compact

- **WHEN** the estimated tokens of `messages` exceed 90% of `model_limit`
- **THEN** `_run_react_loop` calls `_mid_run_compact(messages)` which applies `prune_old_tool_results` and `fold_old_messages`
- **AND** the compacted `messages` list is used for the next turn
- **AND** the loop continues.

#### Scenario: Token usage exceeds 95% forces stop

- **WHEN** the estimated tokens of `messages` exceed 95% of `model_limit`
- **THEN** `_run_react_loop` breaks out of the turn loop
- **AND** a `RunUsageEvent` is yielded with the accumulated usage
- **AND** the run completes with the results collected so far.

#### Scenario: No model info skips token check

- **WHEN** `model_id` is None or `get_model_limits` returns no context window
- **THEN** token budget control is skipped
- **AND** the loop relies on `MAX_TURNS` as the only safeguard.

#### Scenario: Mid-run compact does not call LLM

- **WHEN** `_mid_run_compact` is triggered
- **THEN** only `prune_old_tool_results` and `fold_old_messages` are applied (structural compression)
- **AND** no LLM summarization call is made (latency constraint).

## MODIFIED Requirements

### Requirement: SDK adapters SHALL implement call_once for single-turn execution

SDK adapters (Custom) MUST implement `call_once(input, cancel_event) -> AsyncIterator[StreamEvent]` that performs a single LLM API call and yields all events for that turn (message.start → parts → message.end → tool.call *). CLI adapters are exempt. The AgentRunner's `_run_react_loop` SHALL manage the ReAct loop, including tool result caching (read-only tools only) and token budget control (90% compact, 95% stop).

#### Scenario: call_once yields single-turn events

- **WHEN** AgentRunner calls `call_once` with a valid `AdapterInput` containing `messages`
- **THEN** the adapter performs one Chat Completions API call
- **AND** yields message.start, part.start/delta/end, tool.call events, and message.end
- **AND** does NOT execute any tools (tool execution is AgentRunner's responsibility).

#### Scenario: CLI adapter call_once raises NotImplementedError

- **WHEN** AgentRunner calls `call_once` on a CLI adapter (Claude Code / Codex)
- **THEN** the adapter raises `NotImplementedError`
- **AND** AgentRunner falls back to the `stream` path.
