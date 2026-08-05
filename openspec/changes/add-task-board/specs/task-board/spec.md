# Spec: Task Board

## ADDED Requirements

### Requirement: Task Entity

A `Task` is a persistent, user-scoped work item in the global task pool. It is independent of any Conversation but MAY be bound to one when an Agent claims it.

Fields:
- `id`: unique identifier (string)
- `userId`: owner user (FK → users)
- `title`: short description (string, max 500)
- `description`: detailed description (text, default empty)
- `status`: lifecycle status (enum: `backlog`, `todo`, `in_progress`, `in_review`, `done`, `blocked`, `canceled`)
- `priority`: urgency level (enum: `none`, `urgent`, `high`, `medium`, `low`)
- `labels`: free-form tag array (JSON array of strings)
- `assigneeAgentId`: Agent assigned to work on this task (FK → agents, nullable)
- `creatorType`: who created it (enum: `user`, `agent`)
- `creatorId`: creator identifier
- `creatorName`: creator display name
- `conversationId`: bound Conversation when an Agent is working on it (FK → conversations, nullable)
- `workspaceMode`: workspace binding mode (enum: `sandbox`, `local`, `null`; default `null`)
- `workspacePath`: local project absolute path, used when `workspaceMode === 'local'` (string, nullable)
- `version`: optimistic concurrency control version (int, starts at 1, increment on every update)
- `failureCount`: number of consecutive dispatch failures (int, default 0; incremented on scheduler failure rollback, reset to 0 on successful `task_complete`)
- `sortOrder`: manual ordering within a status column (int, default 0)
- `dueDate`: optional ISO date string (nullable)
- `createdAt`, `updatedAt`: timestamps
- `completedAt`: timestamp when status moved to `done` (nullable)

#### Scenario: Task persists across runs

- **WHEN** a user creates a Task with status `todo`
- **THEN** the Task is stored in the database and survives Agent run boundaries, conversation ends, and server restarts

#### Scenario: Task user isolation

- **WHEN** User A creates a Task
- **THEN** User B cannot see, claim, or modify that Task; all Task API endpoints filter by `userId`

### Requirement: Task Workspace Binding

A Task MAY optionally bind to a workspace configuration (`workspaceMode` + `workspacePath`). When the scheduler dispatches a bound task, it creates a Conversation using the bound workspace configuration instead of the default sandbox.

Binding modes:
- `sandbox`: Scheduler creates a sandbox workspace (auto-generated temporary directory). `workspacePath` is `null`.
- `local`: Scheduler creates a Conversation with `workspaceMode='local'` and `workspacePath` set to the bound path. The Agent works directly in the user's real project directory.
- `null` (default): Same behavior as `sandbox`.

#### Scenario: Task with local workspace binding

- **WHEN** a user creates a Task with `workspaceMode='local'` and `workspacePath='/home/user/my-project'`
- **AND** the scheduler dispatches this task
- **THEN** the scheduler creates a Conversation with `workspaceMode='local'` and `workspacePath='/home/user/my-project'`, and the Agent works in that directory

#### Scenario: Task with sandbox workspace binding

- **WHEN** a user creates a Task with `workspaceMode='sandbox'`
- **AND** the scheduler dispatches this task
- **THEN** the scheduler creates a Conversation with `workspaceMode='sandbox'` (auto-generated sandbox directory)

#### Scenario: Task without workspace binding

- **WHEN** a user creates a Task without specifying `workspaceMode`
- **AND** the scheduler dispatches this task
- **THEN** the scheduler creates a Conversation with default sandbox mode (same as `workspaceMode='sandbox'`)

### Requirement: Task Comment Entity

A `TaskComment` is a note attached to a Task, authored by either a user or an Agent. Comments support OCC via `version`.

Fields:
- `id`, `taskId` (FK → tasks, cascade delete), `userId`, `body` (text), `authorType` (`user` / `agent`), `authorId`, `authorName`, `version`, `createdAt`, `updatedAt`

#### Scenario: Comment cascade delete

- **WHEN** a Task is deleted
- **THEN** all TaskComments for that Task are automatically deleted via database cascade

### Requirement: Task Lifecycle

Tasks follow a status lifecycle:

```
backlog → todo → in_progress → in_review → done
                    ↓              ↓
                blocked        canceled
```

Valid transitions:
- `backlog` → `todo` (user or agent moves to ready)
- `todo` → `in_progress` (agent claims via `task_claim`)
- `in_progress` → `in_review` (agent completes via `task_complete`)
- `in_review` → `done` (user confirms acceptance only)
- `in_review` → `todo` (user rejects, sends back for rework)
- `in_progress` → `blocked` (agent cannot continue)
- Any status → `canceled` (user or agent decides not to continue)

#### Scenario: Agent cannot skip to done

- **WHEN** an Agent calls `task_complete` on a task in `in_progress`
- **THEN** the task moves to `in_review`, NOT `done`; only the user can move it to `done`

#### Scenario: User rejects review

- **WHEN** a user moves a task from `in_review` to `todo`
- **THEN** the task version increments and it becomes available for the scheduler or an Agent to claim again

### Requirement: Optimistic Concurrency Control

Every Task and TaskComment mutation (status move, field update, claim, comment add) MUST include `ifVersion`. The server compares the provided version with the current DB version. If they differ, the mutation is rejected.

#### Scenario: Concurrent claim conflict

- **WHEN** Agent A and Agent B both try to `task_claim` the same `todo` task with `ifVersion=1`
- **AND** Agent A's request is processed first (version becomes 2)
- **THEN** Agent B's request is rejected with a version conflict error

#### Scenario: Version mismatch on update

- **WHEN** a user tries to update a task with `ifVersion=3` but the DB version is `4`
- **THEN** the server returns a 409 conflict error with the current version

### Requirement: Task Scheduler Service

A background asyncio service (`TaskSchedulerService`) that periodically scans for `todo` tasks and dispatches them to Agents.

Configuration:
- `intervalSeconds`: scan interval (default 300s / 5 min)
- `maxConcurrent`: maximum simultaneous task executions (default 3)
- `defaultAgentId`: Agent to use when task has no assignee
- `userId`: the user whose tasks to scan (single-user mode)

#### Scenario: Scheduler scans and dispatches

- **WHEN** the scheduler is running and finds a `todo` task with no active conversation and `failureCount < MAX_FAILURES` (default 5)
- **THEN** it creates a new Conversation (using the task's `workspaceMode`/`workspacePath` binding if set, otherwise default sandbox), binds it to the task, sets status to `in_progress`, and dispatches the Agent via `run_with_args` → `execute_run` → `run_agent_loop(mode='solo')`

#### Scenario: Scheduler respects concurrency limit

- **WHEN** the scheduler has `maxConcurrent=3` and 3 tasks are already being processed
- **THEN** it does not dispatch additional tasks until at least one active dispatch completes

#### Scenario: Scheduler failure rollback

- **WHEN** a dispatched task fails (Agent run throws exception)
- **THEN** the task status reverts to `todo`, `conversationId` is cleared, `version` increments, `failureCount` increments by 1; the task becomes eligible for retry on the next scan unless `failureCount >= MAX_FAILURES`

#### Scenario: Scheduler skips exhausted tasks

- **WHEN** a `todo` task has `failureCount >= 5` (MAX_FAILURES)
- **THEN** the scheduler skips it and does not dispatch it; the task remains in `todo` status until the user manually resets it (e.g., by editing the task, which resets `failureCount` to 0)

#### Scenario: Scheduler start/stop

- **WHEN** the user (via smallA `manage_tasks(action=scheduler_start)`) starts the scheduler
- **THEN** an asyncio background task begins scanning; when stopped (`action=scheduler_stop`), the background task is cancelled

### Requirement: Task REST API

CRUD endpoints under `/api/tasks`:

- `GET /api/tasks` — list tasks (query: status, priority, assigneeAgentId, labels)
- `POST /api/tasks` — create task
- `GET /api/tasks/{id}` — get task detail (includes comments)
- `PATCH /api/tasks/{id}` — update fields (body: `ifVersion`)
- `POST /api/tasks/{id}/move` — move status (body: `status`, `ifVersion`)
- `POST /api/tasks/{id}/assign` — assign Agent (body: `agentId`, `ifVersion`)
- `DELETE /api/tasks/{id}` — archive task
- `GET /api/tasks/{id}/comments` — list comments
- `POST /api/tasks/{id}/comments` — add comment
- `POST /api/tasks/scheduler/start` — start scheduler
- `POST /api/tasks/scheduler/stop` — stop scheduler
- `GET /api/tasks/scheduler/status` — scheduler status

#### Scenario: Create task

- **WHEN** a POST request is sent to `/api/tasks` with `title`, `priority`, `labels`, and optional `workspaceMode` / `workspacePath`
- **THEN** a new Task is created with `status=todo`, `creatorType=user`, `version=1`, and returned as JSON

#### Scenario: Move with version conflict

- **WHEN** a POST request is sent to `/api/tasks/{id}/move` with `ifVersion=1` but DB version is `2`
- **THEN** the server returns HTTP 409 with `{"error": "version_conflict", "currentVersion": 2}`

### Requirement: Agent Task Tools

Seven tools available to Custom Agents (SDK route) when `task_*` tools are in `agent.tool_names`:

1. `task_list(status?, limit?)` — list user's tasks, returns JSON array
2. `task_get(taskId)` — get task detail + comments
3. `task_create(title, description?, priority?, labels?)` — create a new task
4. `task_claim(taskId, ifVersion)` — claim a `todo` task → `in_progress` (OCC enforced)
5. `task_complete(taskId, ifVersion, summary)` — mark `in_progress` → `in_review`, auto-adds comment with `summary`
6. `task_move(taskId, status, ifVersion, reason?)` — move to `blocked` / `canceled` / `backlog` / `todo`
7. `task_comment(taskId, body)` — add a comment

#### Scenario: Agent claims task

- **WHEN** an Agent calls `task_claim(taskId="task_abc", ifVersion=1)` on a `todo` task
- **THEN** the task's `status` becomes `in_progress`, `assigneeAgentId` becomes the calling Agent's ID, `version` becomes 2, and a `task.moved` SSE event is published

#### Scenario: Agent completes task with summary

- **WHEN** an Agent calls `task_complete(taskId="task_abc", ifVersion=2, summary="Implemented auth, tests pass")`
- **THEN** the task's `status` becomes `in_review`, `version` becomes 3, a new TaskComment is created with the summary as body and `authorType=agent`, and `task.moved` + `task.commented` SSE events are published

### Requirement: Task Prompt Builder

When the scheduler dispatches a task to an Agent, it builds a prompt that includes the task title, description, priority, labels, and workflow instructions.

The prompt template (see design.md "Task Prompt Template" for full example) includes:
- Task metadata: title, description, priority, labels
- Workspace info: workspace mode and path description
- Workflow instructions: use `create_plan` to break down steps, use `plan_step` to track progress
- Completion rule: must call `task_complete` (NOT `task_move` to `done`) when finished
- Blockage rule: call `task_move` to `blocked` with a reason if unable to continue
- Progress tracking: use `task_comment` to record intermediate progress

#### Scenario: Task prompt includes lifecycle rules

- **WHEN** the scheduler dispatches a task
- **THEN** the prompt instructs the Agent to use `create_plan`, work through steps, and call `task_complete` (NOT `task_move` to `done`) when finished

#### Scenario: Task prompt includes blockage guidance

- **WHEN** the scheduler dispatches a task
- **THEN** the prompt instructs the Agent to call `task_move(taskId, status="blocked", ifVersion, reason)` if it encounters an unresolvable blocker, rather than silently failing

### Requirement: Task SSE Events

Six new event types published through `event_bus`:

1. `task.created` — new task added (payload: `task`)
2. `task.updated` — task fields updated (payload: `task`)
3. `task.moved` — status changed (payload: `taskId`, `fromStatus`, `toStatus`, `task`)
4. `task.commented` — comment added (payload: `taskId`, `comment`)
5. `task.assigned` — agent assigned (payload: `taskId`, `agentId`, `task`)
6. `scheduler.status` — scheduler running state (payload: `running`, `pendingCount`, `activeCount`)

#### Scenario: Task created event

- **WHEN** a new Task is created via API or Agent tool
- **THEN** a `task.created` event is published to `event_bus`, and all SSE subscribers receive it

#### Scenario: Scheduler status event

- **WHEN** the scheduler starts, stops, or changes active count
- **THEN** a `scheduler.status` event is published with current running state, pending count, and active count
