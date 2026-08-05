# Spec: Frontend (Delta — Task Board)

## ADDED Requirements

### Requirement: Tasks Sidebar Mode

The sidebar gains a new mode `tasks` alongside `conversations`, `artifacts`, `agents`, `resources`, `cognition`, `extensions`. When selected, the main area renders the `TaskBoardView` component.

#### Scenario: Switch to tasks mode

- **WHEN** the user clicks the "任务" icon in the sidebar navigation
- **THEN** the sidebar mode changes to `tasks` and the main area renders the Kanban board

### Requirement: Kanban Board View

The `TaskBoardView` component renders a horizontal Kanban board with columns for each status: 待办池, 待办, 进行中, 评审中, 已完成, 已阻塞. Each column shows task cards sorted by `sortOrder` then `priority`.

Each task card displays:
- Title (truncated)
- Priority badge (colored by level)
- Assignee Agent avatar (if assigned)
- Label tags (if any)
- Conversation link indicator (if bound)

#### Scenario: Board loads tasks

- **WHEN** the TaskBoardView mounts
- **THEN** it fetches `GET /api/tasks` and renders cards in the appropriate columns

#### Scenario: Real-time task move

- **WHEN** a `task.moved` SSE event is received
- **THEN** the task card animates from the old column to the new column

### Requirement: Task Detail Side Panel

Clicking a task card opens a detail panel on the right side (similar to the existing `TaskDetailPanel` for DAG tasks). It shows:
- Full title, description, status, priority
- Assignee Agent info (avatar, name)
- Bound Conversation link (clickable to switch to that conversation)
- Version number
- Comment timeline (reverse chronological)
- Add comment input
- Action buttons: 移动状态, 分配 Agent, 编辑

#### Scenario: Open task detail

- **WHEN** the user clicks a task card in the Kanban board
- **THEN** the detail panel slides in from the right, showing full task info and comments

#### Scenario: Jump to conversation

- **WHEN** the task has a bound `conversationId` and the user clicks the conversation link
- **THEN** the sidebar switches to `conversations` mode and opens the bound conversation

### Requirement: Task Editor Dialog

A dialog for creating and editing tasks. Fields:
- Title (required, text input)
- Description (optional, textarea)
- Priority (select: 无 / 紧急 / 高 / 中 / 低)
- Labels (tag input, comma-separated)
- Assignee (optional, agent dropdown)
- Workspace mode (radio: sandbox / local / none; default none)
- Workspace path (text input, shown when mode is `local`; absolute path to local project directory)
- Due date (optional, date picker)

#### Scenario: Create task via dialog

- **WHEN** the user clicks "+ 新建" and fills in the form
- **THEN** a POST request is sent to `/api/tasks` and the new task appears on the board

#### Scenario: Set workspace binding to local project

- **WHEN** the user selects workspace mode `local` and enters `/home/user/my-project` as the path
- **THEN** the created task has `workspaceMode='local'` and `workspacePath='/home/user/my-project'`

### Requirement: Drag-and-Drop Interactions

The Kanban board supports drag-and-drop using native HTML5 Drag and Drop API (no external drag library).

**Drag to change status**: A task card can be dragged from one status column to another. On drop, a `POST /api/tasks/{id}/move` request is sent with the new status and current `sortOrder`. The card animates to the new column.

**Drag to reorder**: A task card can be dragged within its own column to reorder. The new `sortOrder` is calculated using the midpoint insertion method (average of adjacent cards' sortOrder, or ±1024 from the nearest card). A `POST /api/tasks/{id}/move` request is sent with the same status and updated `sortOrder`.

#### Scenario: Drag card to different column

- **WHEN** the user drags a card from `todo` to `in_progress` and releases
- **THEN** a move request is sent, the card animates to the `in_progress` column, and the board refreshes

#### Scenario: Drag card within same column

- **WHEN** the user drags a card between two other cards in the same column and releases
- **THEN** the card's `sortOrder` is recalculated and a move request updates the ordering

#### Scenario: Version conflict on drag

- **WHEN** a drag triggers a move request that returns HTTP 409 (version conflict)
- **THEN** the card reverts to its original position and an error toast is shown; the board refreshes with the latest server state

### Requirement: Undo/Redo System

The Kanban board maintains a client-side undo stack (max 20 entries). Every mutating action (create, move, update, archive) pushes an undo closure onto the stack. The undo closure is a function that reverses the specific operation.

- **Trigger**: `Ctrl+Z` (Windows/Linux) or `⌘+Z` (macOS) keyboard shortcut, or clicking the "撤销" button on the toast notification
- **Toast**: After each mutation, a toast appears with the operation description and a "撤销" button. The toast auto-dismisses after 5 seconds.
- **Undo closure**: Each undo stores the exact inverse operation (e.g., undo a create = archive the created task; undo a move = move back to original status+sortOrder)
- **No redo**: Only undo is supported (no redo stack). After undoing, the operation is removed from the stack.

#### Scenario: Undo after task move

- **WHEN** the user moves a task from `backlog` to `todo`
- **THEN** a toast appears with "移动任务到 待办" and a "撤销" button
- **WHEN** the user clicks "撤销" or presses `Ctrl+Z`
- **THEN** the task is moved back to `backlog` with its original `sortOrder`

#### Scenario: Undo after task create

- **WHEN** the user creates a new task
- **THEN** a toast appears with "创建任务" and a "撤销" button
- **WHEN** the user clicks "撤销"
- **THEN** the created task is archived (soft-deleted) and removed from the board

### Requirement: Column Visibility Control

The user can hide and show individual status columns. Hidden columns are collapsed into a compact "hidden columns" strip at the end of the board. Clicking a hidden column restores it.

Configuration is persisted to `localStorage` under key `taskboard.columnVisibility`.

#### Scenario: Hide a column

- **WHEN** the user clicks the "隐藏列" button on a column header
- **THEN** the column collapses and its task count appears in the hidden columns strip

#### Scenario: Show a hidden column

- **WHEN** the user clicks a hidden column in the strip
- **THEN** the column is restored to its original position in the board

### Requirement: Empty Column Toggle

A toggle controls whether columns with zero tasks are displayed. Persisted to `localStorage` under key `taskboard.showEmptyColumns`.

#### Scenario: Hide empty columns

- **WHEN** the user toggles "显示空列" off
- **THEN** all columns with zero tasks are hidden from the board

#### Scenario: Show empty columns

- **WHEN** the user toggles "显示空列" on
- **THEN** all status columns are displayed, including those with zero tasks

### Requirement: Task Search

A search input at the top of the board filters task cards in real-time. Matching is case-insensitive against task title and description. Only matching cards are rendered; non-matching cards are hidden (not removed from state).

#### Scenario: Search filters cards

- **WHEN** the user types "auth" in the search box
- **THEN** only cards whose title or description contains "auth" (case-insensitive) are visible

#### Scenario: Clear search

- **WHEN** the user clears the search input
- **THEN** all cards are visible again

### Requirement: Task Filters

A filter menu allows filtering by status, priority, labels, and assignee. Multiple filters can be combined (AND logic). The active filter count is shown as a badge on the filter button. A "Clear filters" button resets all filters.

#### Scenario: Filter by priority

- **WHEN** the user selects `high` priority filter
- **THEN** only cards with `priority=high` are visible

#### Scenario: Combined filters

- **WHEN** the user selects `high` priority and `bug` label
- **THEN** only cards with `priority=high` AND `bug` in labels are visible

### Requirement: Task Context Menu

Right-clicking a task card opens a context menu with quick actions:
- Move to status (submenu: 待办池 / 待办 / 进行中 / 评审中 / 已完成 / 已阻塞 / 已取消)
- Change priority (submenu: 无 / 紧急 / 高 / 中 / 低)
- Edit labels (inline tag editor)
- Duplicate task
- Archive task

The menu closes on outside click or `Escape` key.

#### Scenario: Quick status change via context menu

- **WHEN** the user right-clicks a card and selects "移动到 → 评审中"
- **THEN** the task is moved to `in_review` without opening the detail panel

#### Scenario: Quick priority change via context menu

- **WHEN** the user right-clicks a card and selects "优先级 → 高"
- **THEN** the task's priority is updated to `high` via PATCH request

### Requirement: Task Duplicate

The context menu includes a "复制任务" action that creates a copy of the task:
- Title: original title + " (副本)"
- Status: `backlog`
- Priority, labels, description: copied from original
- `workspaceMode` / `workspacePath`: NOT copied (set to `null`)
- `assigneeAgentId`: NOT copied (set to `null`)
- `version`: 1 (new task)

#### Scenario: Duplicate a task

- **WHEN** the user right-clicks a card and selects "复制任务"
- **THEN** a new task is created with title "<original title> (副本)" and status `backlog`, and appears in the `backlog` column

### Requirement: Scheduler Control

The TaskBoardView header includes a scheduler toggle showing current status:
- When stopped: shows "调度器：已停止" with a "启动调度" button
- When running: shows "调度器：运行中" with pending/active counts and a "停止调度" button

#### Scenario: Start scheduler from UI

- **WHEN** the user clicks "启动调度" on the scheduler toggle
- **THEN** a POST request is sent to `/api/tasks/scheduler/start` and the toggle switches to running state

### Requirement: App Store Task State

The Zustand store gains:
- `tasks: Record<string, TaskRow>` — all loaded tasks keyed by ID
- `taskIdsByStatus: Record<TaskStatus, string[]>` — task IDs grouped by status for column rendering
- `taskComments: Record<string, TaskCommentRow[]>` — comments keyed by task ID
- `schedulerRunning: boolean`
- `schedulerPendingCount: number`
- `schedulerActiveCount: number`

Actions:
- `upsertTask(task)` — add or update a task in the store
- `removeTask(taskId)` — remove a task
- `moveTaskStatus(taskId, fromStatus, toStatus)` — move ID between status arrays
- `setTaskComments(taskId, comments)` — set comments for a task
- `addTaskComment(taskId, comment)` — append a comment
- `setSchedulerStatus(running, pending, active)` — update scheduler state
- `pushUndo(message, undoFn)` — push an undo closure onto the undo stack
- `popUndo()` — pop and execute the latest undo closure
- `undoStackSize` — current undo stack size (for UI state)

SSE reducer handles all 6 new event types and dispatches the appropriate store actions.

#### Scenario: SSE task.created updates store

- **WHEN** a `task.created` event is received via SSE
- **THEN** `upsertTask` is called with the event's task data, and the task ID is added to the appropriate `taskIdsByStatus` array
