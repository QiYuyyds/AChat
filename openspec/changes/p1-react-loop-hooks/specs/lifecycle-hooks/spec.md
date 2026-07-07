# Spec Delta: Lifecycle Hooks

## ADDED Requirements

### Requirement: HookRegistry SHALL provide lifecycle hook registration and dispatch

The system MUST provide a `HookRegistry` that allows registering async handler functions for lifecycle events. Handlers MUST be invoked in priority order (lower number first) when the corresponding event occurs.

#### Scenario: Hook registered and dispatched

- **WHEN** a handler is registered for `pre_tool_use` with priority 10
- **AND** a tool is about to be executed
- **THEN** the handler is invoked with a `HookContext` containing the tool name and args
- **AND** the handler's `HookResult` is processed before tool execution proceeds.

#### Scenario: Multiple hooks dispatched in priority order

- **WHEN** two handlers are registered for `post_tool_use` with priorities 5 and 10
- **THEN** the priority-5 handler is invoked first
- **AND** the priority-10 handler is invoked second
- **AND** both handlers receive the same `HookContext`.

#### Scenario: No handlers registered

- **WHEN** an event is dispatched with no registered handlers
- **THEN** the default `allow` result is returned
- **AND** the operation proceeds normally.

### Requirement: HookContext SHALL carry event-specific data

Each hook dispatch MUST provide a `HookContext` with the event type, run metadata (run_id, agent_id, conversation_id), and event-specific data (tool_name, args, result, turn_number, etc.).

#### Scenario: Pre-tool-use context

- **WHEN** `pre_tool_use` is dispatched
- **THEN** the context includes `tool_name`, `args`, `call_id`, `run_id`, `agent_id`, `conversation_id`, `turn_number`.

#### Scenario: Post-tool-use context

- **WHEN** `post_tool_use` is dispatched
- **THEN** the context includes `tool_name`, `args`, `result`, `is_error`, `call_id`, `run_id`, `agent_id`, `conversation_id`, `turn_number`.

#### Scenario: Post-turn context

- **WHEN** `post_turn` is dispatched
- **THEN** the context includes `turn_number`, `message_id`, `tool_calls`, `finish_reason`, `usage`, `run_id`, `agent_id`, `conversation_id`.

### Requirement: HookResult SHALL support four control-flow actions

Handlers MUST return a `HookResult` with one of four actions: `allow`, `deny`, `modify`, or `inject`.

#### Scenario: Deny prevents tool execution

- **WHEN** a `pre_tool_use` handler returns `HookResult(action="deny", data="blocked by policy")`
- **THEN** the tool is NOT executed
- **AND** a tool result with the deny reason is returned as an error.

#### Scenario: Modify changes tool arguments

- **WHEN** a `pre_tool_use` handler returns `HookResult(action="modify", data={"args": modified_args})`
- **THEN** the tool is executed with the modified arguments instead of the original ones.

#### Scenario: Modify changes tool result

- **WHEN** a `post_tool_use` handler returns `HookResult(action="modify", data={"result": modified_result})`
- **THEN** the modified result is used in place of the original tool result.

#### Scenario: Inject adds events

- **WHEN** an `on_stop` handler returns `HookResult(action="inject", data=[event1, event2])`
- **THEN** the injected events are published to the event stream after the stop event.

#### Scenario: Allow proceeds normally

- **WHEN** a handler returns `HookResult(action="allow")` or `None`
- **THEN** the operation proceeds without modification.

### Requirement: Built-in hooks SHALL be registered at startup

The system MUST register built-in hooks at application startup: `audit_log`, `memory_persist`, `auto_compact`, and `tool_approval`. Agents MAY enable specific hook groups via `hook_names` configuration.

#### Scenario: Agent enables audit_log hook

- **WHEN** an agent's `hook_names` includes `audit_log`
- **THEN** the `pre_tool_use` and `post_tool_use` audit log handlers are active for that agent's runs.

#### Scenario: Agent has no hook_names

- **WHEN** an agent's `hook_names` is empty or None
- **THEN** no built-in hooks are active for that agent's runs
- **AND** the runs proceed without hook overhead.

### Requirement: Hook dispatch SHALL be best-effort and non-blocking on error

If a hook handler raises an exception, the dispatch MUST log the error and continue with the default `allow` action. Hook failures MUST NOT crash the run.

#### Scenario: Hook handler raises exception

- **WHEN** a `post_tool_use` handler raises a `ValueError`
- **THEN** the error is logged with the handler name and event type
- **AND** the tool result is used as-is (default `allow`)
- **AND** the run continues normally.

### Requirement: Hooks SHALL support ten lifecycle event types

The system MUST support the following hook event types: `pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`, `on_stop`, `on_error`, `on_run_start`, `on_run_end`, `on_message_end`.

#### Scenario: Run lifecycle hooks

- **WHEN** a run starts
- **THEN** `on_run_start` is dispatched before the first turn
- **AND** `on_run_end` is dispatched after the run completes (success or failure).

#### Scenario: Turn lifecycle hooks

- **WHEN** a turn begins in the ReAct loop
- **THEN** `pre_turn` is dispatched before `call_once`
- **AND** `post_turn` is dispatched after the turn's events are consumed.

#### Scenario: Stop hook

- **WHEN** the LLM stops calling tools (finish_reason="stop" or no tool_calls)
- **THEN** `on_stop` is dispatched before the run ends.
