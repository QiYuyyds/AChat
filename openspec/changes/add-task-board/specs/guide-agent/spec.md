# Spec: Guide Agent (Delta — Task Board)

## ADDED Requirements

### Requirement: manage_tasks Management Tool

The Guide Agent (小A, `is_guide=True`) gains a new management tool `manage_tasks` alongside the existing 7 management tools. This tool is only injected for `is_guide=True` agents; non-guide agents with `manage_tasks` in `tool_names` are filtered out.

Actions:
- `list` — list tasks (optional: `status`, `priority` filters)
- `create` — create a task (`title`, `description?`, `priority?`, `labels?`)
- `update` — update task fields (`taskId`, `title?`, `description?`, `priority?`, `labels?`, `ifVersion`)
- `move` — move task status (`taskId`, `status`, `ifVersion`)
- `assign` — assign task to an Agent (`taskId`, `agentId`, `ifVersion`)
- `delete` — archive a task (`taskId`, `ifVersion`)
- `scheduler_start` — start the TaskSchedulerService (`agentId?`, `intervalMinutes?` default 5, `maxConcurrent?` default 3)
- `scheduler_stop` — stop the TaskSchedulerService
- `scheduler_status` — get current scheduler state

All mutating actions (create / update / move / assign / delete / scheduler_start / scheduler_stop) MUST call `emit_guide_side_effect(ctx=ctx, target="tasks", action=...)` after successful execution. This allows the frontend to auto-refresh the Kanban board via `useGuideSideEffectRefresh('tasks', callback)` when the user manages tasks through 小A.

#### Scenario: Guide creates task

- **WHEN** the user tells 小A "创建一个任务：实现用户认证"
- **THEN** 小A calls `manage_tasks(action=create, title="实现用户认证", priority="high")` and a new Task is created

#### Scenario: Guide starts scheduler

- **WHEN** the user tells 小A "开始定时执行待办任务"
- **THEN** 小A calls `manage_tasks(action=scheduler_start, agentId="<default>", intervalMinutes=5)` and the TaskSchedulerService starts

#### Scenario: Non-guide agent filtered

- **WHEN** a non-guide Custom Agent has `manage_tasks` in its `tool_names`
- **THEN** the tool is filtered out and not made available to that Agent

#### Scenario: Guide side-effect refreshes Kanban board

- **WHEN** 小A successfully executes `manage_tasks(action=create, title="实现认证")`
- **THEN** a `guide_side_effect` SSE event with `target="tasks"`, `action="create"` is published, and the frontend Kanban board auto-refreshes its task list via `useGuideSideEffectRefresh('tasks', callback)`

### Requirement: _MANAGEMENT_TOOL_NAMES Update

The `_MANAGEMENT_TOOL_NAMES` frozenset in `backend/app/services/agent_runner.py` MUST include `"manage_tasks"`. This ensures:
- Guide agents (`is_guide=True`): `manage_tasks` is injected alongside the other 7 management tools
- Non-guide agents: `manage_tasks` is filtered out even if mistakenly listed in `tool_names`

#### Scenario: Guide agent receives manage_tasks

- **WHEN** a guide agent (`is_guide=True`) runs
- **THEN** `manage_tasks` is included in its tool list because it is in `_MANAGEMENT_TOOL_NAMES`

#### Scenario: Non-guide agent filtered

- **WHEN** a non-guide agent has `manage_tasks` in `tool_names`
- **THEN** it is filtered out because `manage_tasks` is in `_MANAGEMENT_TOOL_NAMES` (management tools are guide-only)

### Requirement: Guide System Prompt Update

The `GUIDE_SYSTEM_PROMPT` is updated to include task management as the 8th management capability:

```
8. 任务面板 —— 创建、分配、移动、删除任务；启停定时调度器
```

#### Scenario: Guide knows task capabilities

- **WHEN** a user asks 小A "帮我创建几个待办任务"
- **THEN** 小A uses `manage_tasks(action=create)` to create tasks, because the system prompt lists task management as a capability
