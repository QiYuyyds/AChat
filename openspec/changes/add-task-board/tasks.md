## 1. 数据模型与 Schemas

- [x] 1.1 在 `backend/app/db/models.py` 中新增 `Task` 模型（含所有字段：id, userId, title, description, status, priority, labels, assigneeAgentId, creatorType, creatorId, creatorName, conversationId, workspaceMode, workspacePath, version, failureCount, sortOrder, dueDate, createdAt, updatedAt, completedAt）
- [x] 1.2 在 `backend/app/db/models.py` 中新增 `TaskComment` 模型（含 FK cascade delete）
- [x] 1.3 新建 `backend/app/schemas/task.py`，定义 Task 和 TaskComment 的 Pydantic 模型（camelCase 字段别名）
- [x] 1.4 在 `backend/app/schemas/events.py` 中新增 6 个 Task SSE 事件类（TaskCreatedEvent, TaskMovedEvent, TaskCommentedEvent, TaskAssignedEvent, TaskUpdatedEvent, SchedulerStatusEvent）
- [x] 1.5 在 `backend/app/schemas/events.py` 的 `StreamEvent` Union 中加入新的 Task 事件类型

## 2. 后端服务层

- [x] 2.1 新建 `backend/app/services/task_service.py`，实现 Task CRUD 业务逻辑（list, create, get, update, move, assign, archive）。create / update 支持可选的 `workspace_mode` / `workspace_path` 字段
- [x] 2.2 在 `task_service.py` 中实现乐观并发控制：所有变更操作校验 `if_version`，不匹配返回冲突错误
- [x] 2.3 在 `task_service.py` 中实现评论 CRUD（list_comments, add_comment）
- [x] 2.4 在 `task_service.py` 中实现 `task_complete` 逻辑：移动到 `in_review` 时自动创建 TaskComment 记录 summary
- [x] 2.5 在 `task_service.py` 中实现事件发布：每个变更操作通过 `event_bus.publish()` 发布对应的 SSE 事件

## 3. 后端 API

- [x] 3.1 新建 `backend/app/api/tasks.py`，实现所有 REST 端点（GET/POST /api/tasks, GET/PATCH/DELETE /api/tasks/{id}, POST /api/tasks/{id}/move, POST /api/tasks/{id}/assign, GET/POST /api/tasks/{id}/comments）。POST /api/tasks 和 PATCH /api/tasks/{id} 支持可选的 `workspaceMode` / `workspacePath` body 字段
- [x] 3.2 实现 `/api/tasks/scheduler/start`、`/api/tasks/scheduler/stop`、`/api/tasks/scheduler/status` 端点
- [x] 3.3 在 `backend/app/main.py` 中 include tasks router（`app.include_router(tasks.router, prefix="/api", tags=["tasks"])`）
- [x] 3.4 确保所有端点通过 JWT 获取 `user_id` 做用户隔离

## 4. Agent 工具

- [x] 4.1 新建 `backend/app/tools/task_tools.py`，实现 7 个工具的 ToolDef（task_list, task_get, task_create, task_claim, task_complete, task_move, task_comment）。`task_create` 支持可选的 `workspace_mode` / `workspace_path` 参数（D14）
- [x] 4.2 在 `task_claim` 工具中实现 OCC：校验 `if_version`，更新 status 为 `in_progress`，设置 `assignee_agent_id` 为 `ctx.agent_id`
- [x] 4.3 在 `task_complete` 工具中自动创建 TaskComment（authorType=agent, authorId=ctx.agent_id, body=summary）
- [x] 4.4 所有变更工具调用后通过 `event_bus.publish()` 发布 SSE 事件
- [x] 4.5 在 `backend/app/tools/registry.py` 的 `_build_registry()` 中注册 7 个 task 工具
- [x] 4.6 在 `src/shared/agent-builder-config.ts` 中将 7 个 task 工具添加到可选工具列表（`AVAILABLE_AGENT_TOOLS`），并在 `AGENT_TOOL_META` 中为每个新工具添加 label/desc 元数据

## 5. 定时调度器

- [x] 5.1 新建 `backend/app/services/task_scheduler.py`，实现 `TaskSchedulerService` 类（单例模式，`get_instance()`）
- [x] 5.2 实现 `start(user_id, agent_id, interval, max_concurrent)` 和 `stop()` 方法
- [x] 5.3 实现 `_run_loop()` asyncio 后台循环
- [x] 5.4 实现 `_scan_and_dispatch()`：查询 status=todo 且 `failure_count < MAX_FAILURES`（默认 5）的任务，按优先级排序，跳过已在处理的任务
- [x] 5.5 实现 `_dispatch_task(task)`：① 创建 Conversation（指定 agent_id；如果 task 有 `workspace_mode='local'` 且 `workspace_path`，则创建会话时使用该 workspace 配置；否则创建默认 sandbox workspace）② 创建 trigger Message（role=user，内容=`build_task_prompt(task)`，prompt 中包含 workspace 信息）③ 构造 `RunArgs`（agent_id / conversation_id / trigger_message_id / user_id / dispatch_mode='solo'）④ 调用 `run_with_args(args)` → 内部走 `execute_run` → `run_agent_loop(mode='solo')`（不直接调 `run_agent_loop`）⑤ 绑定 task.conversation_id、更新 status=in_progress、发布 SSE 事件
- [x] 5.5a 实现 `build_task_prompt(task)` 函数：生成包含任务信息（标题/描述/优先级/标签/工作目录描述）+ 生命周期规则（必须调用 task_complete 而非 task_move 到 done）+ 阻塞处理指引（task_move 到 blocked）的提示词（参见 design.md Task Prompt Template）。`workspace_description` 根据 `workspace_mode` 生成：local 模式显示本地项目路径，sandbox/null 模式显示沙箱模式说明
- [x] 5.7 实现失败回退逻辑：Agent run 异常时将 task 回退到 `todo`，清除 `conversation_id`，version+1，`failure_count += 1`；调度器扫描时跳过 `failure_count >= MAX_FAILURES` 的任务（D13）
- [x] 5.8a 在 `task_complete` 工具逻辑中重置 `failure_count = 0`（Agent 成功完成时清除失败计数）
- [x] 5.8b 在 SQLite 连接配置中确保 `busy_timeout` 设为 30s（`connect_args={"timeout": 30}`），防止调度器与 API 并发写时 `database is locked` 错误
- [x] 5.8c 在 `backend/app/main.py` 的 lifespan 中初始化 `TaskSchedulerService`

## 6. Guide Agent 集成

- [x] 6.1 新建 `backend/app/tools/manage_tasks.py`，实现 `manage_tasks` 管理工具（action: list/create/update/move/assign/delete/scheduler_start/scheduler_stop/scheduler_status）。所有变更操作成功后调用 `emit_guide_side_effect(ctx=ctx, target="tasks", action=...)` 发射 `guide_side_effect` 事件（D10）
- [x] 6.2 在 `backend/app/tools/registry.py` 中注册 `manage_tasks` 工具
- [x] 6.3 在 `backend/app/services/agent_runner.py` 的 `_MANAGEMENT_TOOL_NAMES` frozenset 中添加 `"manage_tasks"`，确保 Guide Agent 工具注入逻辑能识别此工具（D11）
- [x] 6.4 更新 `backend/app/services/guide_prompt.py` 的 `GUIDE_SYSTEM_PROMPT`，新增第 8 项管理能力："任务面板 —— 创建、分配、移动、删除任务；启停定时调度器"
- [x] 6.5 在 `backend/app/api/agents.py` 的 guide agent 工具列表中添加 `manage_tasks`
- [x] 6.6 在 `src/shared/agent-builder-config.ts` 的 guide 工具常量中添加 `manage_tasks`（前端无 guide 工具常量，guide 工具列表由后端 seed 管理）

## 7. 前端共享类型与 API

- [x] 7.1 在 `src/shared/types.ts` 中新增 `TaskStatus`、`TaskPriority`、`TaskRow`（含 `workspaceMode` / `workspacePath` 字段）、`TaskCommentRow` 类型定义
- [x] 7.2 在 `src/lib/api.ts` 中新增 task API 函数
- [x] 7.3 新建 `src/shared/task-board-config.ts`，定义 TASK_STATUSES、TASK_PRIORITIES 常量

## 8. 前端状态管理

- [x] 8.1 在 `src/stores/app-store.ts` 中新增 task 相关 state（tasks, taskIdsByStatus, taskComments, schedulerRunning, schedulerPendingCount, schedulerActiveCount, undoStack: undo closure 数组 max 20）
- [x] 8.2 实现 actions：upsertTask, removeTask, moveTaskStatus, setTaskComments, addTaskComment, setSchedulerStatus, pushUndo(message, undoFn), popUndo()
- [x] 8.3 在 SSE reducer 中处理 6 个新事件类型（task.created, task.updated, task.moved, task.commented, task.assigned, scheduler.status）。在 reducer 入口处对 Task 事件类型做 early return，不走 conversation 分桶逻辑，直接派发到 task store actions（D12）
- [x] 8.4 在 `SidebarMode` 联合类型中添加 `'tasks'`
- [x] 8.5 新增 `useGuideSideEffectRefresh('tasks', callback)` hook，监听 `guide_side_effect` 事件中 `target="tasks"` 的变更，自动调用 `fetchTasks()` 刷新看板数据

## 9. 前端看板 UI

- [x] 9.1 新建 `src/components/task-board-view.tsx`，实现看板主视图容器（列布局 + 搜索栏 + 筛选菜单 + 调度器控制栏 + 空列 toggle + 列可见性设置菜单）
- [x] 9.2 新建 `src/components/task-board-column.tsx`，实现单列组件（标题 + 任务卡片列表 + 拖拽 drop target + 列隐藏按钮）
- [x] 9.3 新建 `src/components/task-board-card.tsx`，实现任务卡片（标题截断 + 优先级 badge + Agent 头像 + 标签 + 会话链接 + HTML5 draggable）
- [x] 9.4 新建 `src/components/task-board-detail.tsx`，实现任务详情侧栏（完整信息 + workspace 绑定信息 + 评论时间线 + 添加评论 + 操作按钮）
- [x] 9.5 新建 `src/components/task-board-editor.tsx`，实现创建/编辑任务对话框（标题 + 描述 + 优先级 + 标签 + 分配 + workspace 模式选择（sandbox/local/none）+ workspace 路径输入 + 截止日期）
- [x] 9.6 新建 `src/components/task-sidebar-nav.tsx`，实现 sidebar Tasks 导航（任务计数概览）
- [x] 9.7 新建 `src/components/task-board-context-menu.tsx`，实现右键上下文菜单（改状态/改优先级/改标签/复制/归档，子菜单展开）
- [x] 9.8 新建 `src/components/task-board-undo-toast.tsx`，实现撤销 toast 通知（操作描述 + 撤销按钮 + 5s 自动消失 + Ctrl+Z 快捷键监听）
- [x] 9.9 新建 `src/components/task-board-filter-menu.tsx`，实现筛选菜单（status/priority/labels/assignee 组合筛选 + 活跃筛选数 badge + 清除筛选按钮）
- [x] 9.10 新建 `src/components/task-board-hidden-columns.tsx`，实现隐藏列折叠条（显示被隐藏列的 task 计数 + 点击恢复）
- [x] 9.11 实现拖拽逻辑：在 `task-board-card.tsx` 中添加 `draggable` 属性 + `onDragStart` / `onDragEnd` 事件；在 `task-board-column.tsx` 中添加 `onDragOver` / `onDrop` 事件处理。拖拽排序使用中点插入法计算新 `sortOrder`（D15）
- [x] 9.12 实现撤销系统：在 `task-board-view.tsx` 中维护 undo stack（useRef），每次 create/move/update/archive 操作后调用 `pushUndo(message, undoFn)`，监听 `Ctrl+Z` / `⌘+Z` 键盘事件触发 `popUndo()`（D15）
- [x] 9.13 实现搜索与筛选：在 `task-board-view.tsx` 中使用 `useMemo` 对 `tasks` 做实时过滤（搜索匹配 title + description，筛选按 status/priority/labels/assignee 组合），过滤结果传给列组件渲染
- [x] 9.14 实现列可见性控制：从 `localStorage` 读取 `taskboard.columnVisibility` 和 `taskboard.showEmptyColumns`，在 `task-board-view.tsx` 中计算 `visibleStatuses` 和 `hiddenStatuses`

## 10. 前端集成

- [x] 10.1 在 `src/components/sidebar.tsx` 中添加 Tasks 模式入口（CheckSquare 图标 + 点击切换）
- [x] 10.2 在 `src/app/page.tsx` 中根据 `sidebarMode === 'tasks'` 渲染 `TaskBoardView`
- [x] 10.3 在看板视图加载时调用 `fetchTasks()` 和 `getSchedulerStatus()` 初始化 store
- [x] 10.4 在 `task-board-view.tsx` 中添加 `Ctrl+Z` / `⌘+Z` 全局键盘快捷键监听，触发 `popUndo()`
- [x] 10.5 在 `task-board-view.tsx` 中添加 `/` 键聚焦搜索框、 `C` 键打开新建任务编辑器、 `Escape` 键关闭详情面板的快捷键
- [x] 10.6 实现任务复制功能：右键菜单 "复制任务" 调用 `createTask({ title: original.title + ' (副本)', status: 'backlog', priority: original.priority, labels: original.labels, description: original.description })`，复制时不拷贝 workspace 绑定和 assignee

## 11. 测试与验证

- [ ] 11.1 编写 `backend/tests/test_task_service.py`，测试 Task CRUD + OCC 逻辑 + workspace 绑定字段
- [ ] 11.2 编写 `backend/tests/test_task_tools.py`，测试 Agent 工具（claim 冲突、complete 自动评论、failure_count 重置、task_create 带 workspace 参数）
- [ ] 11.3 编写 `backend/tests/test_task_scheduler.py`，测试调度器扫描、派发（含 workspace 绑定场景）、回退逻辑、failure_count 阈值跳过
- [ ] 11.4 手动验证：创建任务（绑定本地项目目录）→ 启动调度器 → Agent 认领并完成 → 用户评审标记 done
- [ ] 11.5 手动验证前端交互：拖拽改变状态 → 拖拽排序 → Ctrl+Z 撤销 → 搜索过滤 → 右键菜单 → 任务复制
- [x] 11.6 运行 `ruff check .` 和 `pnpm typecheck` 确保无错误（已有 B008 是 FastAPI Depends 模式，与现有代码一致）
