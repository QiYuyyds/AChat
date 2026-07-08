# Spec Delta: Lifecycle Hooks

## MODIFIED Requirements

### Requirement: skill_auto_activator hook SHALL suggest skill loading based on tool usage and user message

The system MUST provide a `skill_auto_activator` built-in hook that registers two handlers:
1. A `post_tool_use` handler that inspects the tool name and arguments, matches against the auto-derived rule table, and when a match is found, returns `HookResult(action="inject")` with a system hint guiding the LLM to call `load_skill(<slug>)`.
2. An `on_run_start` handler that extracts the last user message from `messages`, matches its text against the same rule table's keywords, and when a match is found, returns `HookResult(action="inject")` with a skill suggestion hint injected before the first `call_once` turn.

Both handlers MUST share a single rule table auto-derived from skill frontmatter `trigger_keywords` fields. Both handlers MUST check that the suggested slug exists in `list_skills()` AND that `load_skill` is present in the current run's `tool_names` before returning an inject result. If either check fails, the handler MUST return `HookResult(action="allow")` silently.

The `_run_react_loop` MUST capture the `on_run_start` dispatch return value and apply inject actions (appending system hint messages to `messages`) before the first turn, using the same inject logic as `post_tool_use`.

#### Scenario: fs_write writes Python file with existing skill

- **WHEN** `post_tool_use` is dispatched for `fs_write` with args `{"path": "src/main.py"}`
- **AND** a skill with slug `python-best-practices` exists in the registry
- **AND** `load_skill` is in the current run's `tool_names`
- **AND** the `skill_auto_activator` hook is enabled for this agent
- **THEN** the hook returns `HookResult(action="inject", data=[{"type": "system_hint", "content": "...load_skill('python-best-practices')..."}])`
- **AND** the hint message is injected into the `messages` list for the next turn

#### Scenario: Suggested skill does not exist

- **WHEN** `post_tool_use` matches a rule pointing to slug `python-best-practices`
- **AND** no skill with slug `python-best-practices` exists in `list_skills()`
- **THEN** the hook returns `HookResult(action="allow")` (no injection)
- **AND** no hint message is added to `messages`

#### Scenario: load_skill not in tool_names

- **WHEN** `post_tool_use` matches a rule and the suggested skill exists
- **AND** `load_skill` is NOT in the current run's `tool_names` (agent has no equipped skills)
- **THEN** the hook returns `HookResult(action="allow")` (no injection)

#### Scenario: on_run_start matches user message keyword

- **WHEN** `on_run_start` is dispatched with `messages` containing a user message "帮我写个 Python 脚本"
- **AND** a skill with `trigger_keywords: ["python"]` exists in the registry
- **AND** `load_skill` is in `tool_names`
- **THEN** the hook returns `HookResult(action="inject")` with a hint to load the matching skill
- **AND** the hint is appended to `messages` before the first `call_once` call

#### Scenario: on_run_start no keyword match

- **WHEN** `on_run_start` is dispatched with a user message "今天天气怎么样"
- **AND** no skill's `trigger_keywords` match any substring in the message
- **THEN** the hook returns `HookResult(action="allow")` (no injection)

#### Scenario: Hook disabled by agent configuration

- **WHEN** the agent's `hook_names_list` does not include `skill_auto_activator`
- **THEN** neither handler is registered for this agent's runs
- **AND** no skill suggestions are injected at any point

### Requirement: skill_auto_activator SHALL auto-derive rules from skill frontmatter

The hook MUST NOT use hardcoded rule tables. Instead, at registration time, it MUST call `list_skills()` and read each skill's `trigger_keywords` field to build the rule table dynamically. Skills without `trigger_keywords` in their frontmatter are excluded from auto-activation (they remain manually loadable via `/` command or `load_skill`).

#### Scenario: Rule table built from registered skills

- **WHEN** the hook is registered at app startup
- **AND** the registry contains skill A with `trigger_keywords: ["python", ".py"]` and skill B with no `trigger_keywords`
- **THEN** the rule table contains entries for skill A only
- **AND** skill B is not suggested by the auto-activator

#### Scenario: New skill uploaded with trigger_keywords

- **WHEN** a new skill with `trigger_keywords: ["go", ".go"]` is uploaded after app startup
- **THEN** the skill is NOT immediately available for auto-activation until the hook's rule table is refreshed (process restart or explicit refresh call)

#### Scenario: Matching is case-insensitive

- **WHEN** `fs_write` is called with `{"path": "Main.PY"}`
- **AND** a skill has `trigger_keywords: [".py"]`
- **THEN** the hook matches `.py` case-insensitively and suggests the skill
