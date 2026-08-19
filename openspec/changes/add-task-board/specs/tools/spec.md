# Spec: Tools (Delta — Task Board)

## ADDED Requirements

### Requirement: Task Management Agent Tools

Seven new tools are registered in `tool_registry` and available as optional tools for Custom Agents (SDK route). They are NOT part of the baseline 9 tools; they must be explicitly added to `agent.tool_names` via the Agent Builder UI.

Tools:
1. `task_list` — list user's tasks (params: `status?`, `limit?` default 20)
2. `task_get` — get task detail with comments (params: `taskId`)
3. `task_create` — create a new task (params: `title`, `description?`, `priority?`, `labels?`, `workspaceMode?`, `workspacePath?`)
4. `task_claim` — claim a `todo` task → `in_progress` (params: `taskId`, `ifVersion`)
5. `task_complete` — mark `in_progress` → `in_review` (params: `taskId`, `ifVersion`, `summary` required)
6. `task_move` — move to other status (params: `taskId`, `status`, `ifVersion`, `reason?`)
7. `task_comment` — add a comment (params: `taskId`, `body`)

All task tools execute with `ToolContext.user_id` isolation. Tools that mutate (claim, complete, move, comment) publish SSE events via `event_bus`.

The `task_complete` tool additionally resets the task's `failureCount` to 0 upon successful completion, clearing any prior failure history.

### Requirement: _MANAGEMENT_TOOL_NAMES Update

The `_MANAGEMENT_TOOL_NAMES` frozenset in `backend/app/services/agent_runner.py` MUST be updated to include `"manage_tasks"`. This is the single source of truth for guide agent tool injection:
- Guide agents (`is_guide=True`): only tools in `_MANAGEMENT_TOOL_NAMES` + `ask_user` are injected
- Non-guide agents: tools in `_MANAGEMENT_TOOL_NAMES` are filtered out from `tool_names`

Without this update, `manage_tasks` would be filtered out for guide agents, making the tool inaccessible to 小A.

#### Scenario: Tool not in baseline

- **WHEN** a Custom Agent runs without `task_list` in its `tool_names`
- **THEN** the `task_list` tool is NOT injected into the Agent's available tools

#### Scenario: Tool in tool_names

- **WHEN** a Custom Agent has `task_claim` in its `tool_names`
- **THEN** `task_claim` is resolved from `tool_registry` and made available to the Agent's LLM

#### Scenario: Claim with version conflict

- **WHEN** an Agent calls `task_claim(taskId="t1", ifVersion=1)` but DB version is `2`
- **THEN** the tool returns an error result: `"版本冲突：任务已被其他 Agent 认领或状态已变更"`

#### Scenario: Complete auto-adds comment

- **WHEN** an Agent calls `task_complete(taskId="t1", ifVersion=2, summary="Done, tests pass")`
- **THEN** the task status becomes `in_review`, version increments, a new TaskComment is created with `body=summary`, `authorType="agent"`, `authorId=<agent_id>`, `authorName=<agent_name>`, AND the task's `failureCount` is reset to 0
