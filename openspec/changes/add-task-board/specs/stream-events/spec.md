# Spec: Stream Events (Delta — Task Board)

## ADDED Requirements

### Requirement: Task Created Event

A `task.created` event is published when a new Task is created (via API or Agent tool).

Payload:
- `type`: `"task.created"`
- `conversationId`: empty string `""` (task events are not conversation-scoped)
- `task`: full Task object (camelCase fields)

**Frontend routing**: The SSE reducer MUST handle Task events with `conversationId === ""` via a dedicated early-return path that dispatches directly to the task store actions. These events MUST NOT be processed through the conversation bucket logic (which would create an erroneous empty-string conversation bucket).

#### Scenario: Task created via API

- **WHEN** a POST request creates a new Task at `/api/tasks`
- **THEN** a `task.created` event is published to `event_bus` with the full Task object

### Requirement: Task Moved Event

A `task.moved` event is published when a Task's status changes (via API, Agent tool, or scheduler).

Payload:
- `type`: `"task.moved"`
- `taskId`: string
- `fromStatus`: previous status string
- `toStatus`: new status string
- `task`: full updated Task object

#### Scenario: Agent claims task

- **WHEN** an Agent calls `task_claim` and the task moves from `todo` to `in_progress`
- **THEN** a `task.moved` event is published with `fromStatus="todo"`, `toStatus="in_progress"`

### Requirement: Task Commented Event

A `task.commented` event is published when a new comment is added to a Task.

Payload:
- `type`: `"task.commented"`
- `taskId`: string
- `comment`: full TaskComment object (camelCase fields)

#### Scenario: Agent adds summary comment

- **WHEN** an Agent calls `task_complete` with a summary
- **THEN** a `task.commented` event is published with the auto-created comment

### Requirement: Task Assigned Event

A `task.assigned` event is published when a Task's `assigneeAgentId` changes.

Payload:
- `type`: `"task.assigned"`
- `taskId`: string
- `agentId`: string | null (null when unassigned)
- `task`: full updated Task object

#### Scenario: Scheduler assigns agent

- **WHEN** the scheduler dispatches a task and sets the assignee
- **THEN** a `task.assigned` event is published with the assigned `agentId`

### Requirement: Task Updated Event

A `task.updated` event is published when a Task's non-status fields are updated (title, description, priority, labels).

Payload:
- `type`: `"task.updated"`
- `task`: full updated Task object

#### Scenario: User edits task title

- **WHEN** a user sends a PATCH request to update the task title
- **THEN** a `task.updated` event is published with the updated Task object

### Requirement: Scheduler Status Event

A `scheduler.status` event is published when the TaskSchedulerService starts, stops, or changes active/pending counts.

Payload:
- `type`: `"scheduler.status"`
- `running`: boolean
- `pendingCount`: int (number of `todo` tasks)
- `activeCount`: int (number of tasks currently being dispatched)

#### Scenario: Scheduler starts

- **WHEN** the user starts the scheduler via `manage_tasks(action=scheduler_start)`
- **THEN** a `scheduler.status` event is published with `running=true`
