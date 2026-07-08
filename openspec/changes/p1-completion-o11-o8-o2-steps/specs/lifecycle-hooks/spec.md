# Spec Delta: Lifecycle Hooks

## ADDED Requirements

### Requirement: skill_auto_activator hook SHALL suggest skill loading based on tool usage

The system MUST provide a `skill_auto_activator` built-in hook that registers a `post_tool_use` handler. The handler inspects the tool name and arguments, matches against a rule table, and when a match is found, returns `HookResult(action="inject")` with a system hint message guiding the LLM to call `load_skill(<slug>)`.

#### Scenario: fs_write writes Python file

- **WHEN** `post_tool_use` is dispatched for `fs_write` with args `{"path": "src/main.py", "content": "..."}`
- **AND** the `skill_auto_activator` hook is enabled for this agent
- **THEN** the hook returns `HookResult(action="inject", data=[{"type": "system_hint", "content": "...load_skill('python-best-practices')..."}])`
- **AND** the hint message is injected into the `messages` list for the next turn.

#### Scenario: bash runs pnpm

- **WHEN** `post_tool_use` is dispatched for `bash` with args `{"command": "pnpm install"}`
- **AND** the `skill_auto_activator` hook is enabled
- **THEN** the hook returns `HookResult(action="inject")` with a hint to load `frontend-guidelines`.

#### Scenario: Tool does not match any rule

- **WHEN** `post_tool_use` is dispatched for `fs_read` with args `{"path": "config.json"}`
- **AND** the `skill_auto_activator` hook is enabled
- **THEN** the hook returns `HookResult(action="allow")` (no injection).

#### Scenario: Hook disabled by agent configuration

- **WHEN** the agent's `hook_names_list` does not include `skill_auto_activator`
- **THEN** the hook is not registered for this agent's runs
- **AND** no skill suggestions are injected.

### Requirement: skill_auto_activator SHALL use deterministic matching rules

The hook MUST use pure Python condition matching (tool name + argument patterns), not LLM calls. The matching rules MUST be extensible via a rule table.

#### Scenario: Rule table covers fs_write and bash

- **WHEN** the hook is initialized
- **THEN** the rule table includes entries for `fs_write` (path suffix matching) and `bash` (command substring matching).

#### Scenario: Matching is case-insensitive for file extensions

- **WHEN** `fs_write` is called with `{"path": "Main.PY"}`
- **THEN** the hook matches the `.py` suffix case-insensitively and suggests `python-best-practices`.

### Requirement: Injected hint messages SHALL NOT produce SSE events

The `inject` action from `post_tool_use` MUST add the hint as a system-role message in the `messages` list consumed by the next `call_once` turn. The hint MUST NOT be published as a `StreamEvent` to the SSE stream.

#### Scenario: Hint message visible to LLM only

- **WHEN** the `skill_auto_activator` returns an inject result
- **THEN** a `{"role": "system", "content": "..."}` message is appended to `messages`
- **AND** no `PartStartEvent` or `MessageStartEvent` is published for this hint.
