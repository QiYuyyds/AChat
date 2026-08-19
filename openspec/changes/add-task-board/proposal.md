# Add Task Board

## Why

AChat 当前的任务管理是 ephemeral 的——`create_plan` 生成内存态步骤卡片，`task_dispatch` 在 run 内派发子任务，`DispatchPlanCard` 展示 Orchestrator DAG。这些都在单次 run / 单个 conversation 范围内。

用户缺少一个**跨会话、持久化的全局任务池**，让 Agent 能定时认领并完成待办任务。参考 dashi-taskboard 的设计，但融入 AChat 的多 Agent 架构：任务池是全局的，调度器通过 `run_agent_loop(mode='solo')` 派发，Agent 通过乐观并发控制（OCC）自主认领任务。

## What Changes

- **新增 `Task` + `TaskComment` 实体**：持久化到 SQLite，用户隔离（`user_id`），支持乐观并发控制（`version` 字段）
- **新增任务生命周期**：`backlog → todo → in_progress → in_review → done` + `blocked / canceled`
- **新增 7 个 Agent 工具**：`task_list` / `task_get` / `task_create` / `task_claim` / `task_complete` / `task_move` / `task_comment`（Custom Agent 可选工具）
- **新增 `TaskSchedulerService`**：asyncio 后台调度器，周期扫描 `todo` 任务，创建 Conversation 并通过 `run_agent_loop(mode='solo')` 派发 Agent 执行
- **新增 `manage_tasks` Guide 管理工具**：小A 可创建/分配/移动任务，启停调度器
- **新增 6 个 SSE 事件类型**：`task.created` / `task.updated` / `task.moved` / `task.commented` / `task.assigned` / `scheduler.status`
- **新增 Task Workspace 绑定**：任务可绑定沙箱模式或本地项目目录路径，调度器派发时使用绑定的 workspace 配置创建 Conversation
- **新增完整前端看板交互**：拖拽改变状态 + 拖拽排序 + 撤销/重做 + 列可见性控制 + 空列显示控制 + 任务搜索 + 任务筛选 + 上下文菜单 + 任务复制
- **新增前端看板 UI**：Sidebar `tasks` 模式 + Kanban 看板视图 + 任务详情侧栏
- **新增 REST API**：`/api/tasks` CRUD + `/api/tasks/scheduler` 调度控制

## Capabilities

### New Capabilities

- `task-board`: 全局任务池实体、生命周期、乐观并发控制、Agent 工具、调度器服务、SSE 事件、REST API、Task Workspace 绑定、前端看板 UI + 完整 Kanban 交互

### Modified Capabilities

- `stream-events`: 新增 6 个 Task 相关 SSE 事件类型
- `tools`: 新增 7 个 Agent 可选工具（`task_list` 等），加入 baseline 合并逻辑
- `guide-agent`: 新增 `manage_tasks` 管理工具 + 调度器启停 + GUIDE_SYSTEM_PROMPT 更新
- `frontend`: Sidebar 新增 `tasks` 模式 + 看板组件 + app-store 任务状态

## Impact

- **DB**: 新增 `tasks` 和 `task_comments` 两张表（`backend/app/db/models.py`），`tasks` 表含 `workspace_mode` / `workspace_path` 字段
- **后端服务层**: 新增 `task_service.py` + `task_scheduler.py`（调度器派发时使用 task 绑定的 workspace 配置）
- **后端 API**: 新增 `api/tasks.py` router
- **后端工具层**: 新增 `task_tools.py` + `manage_tasks.py`，注册到 `tool_registry`
- **后端 schemas**: 新增 `schemas/task.py` + `events.py` 新增事件
- **后端启动**: `main.py` lifespan 中初始化调度器 + include tasks router
- **前端类型**: `src/shared/types.ts` 新增 Task/TaskComment 类型（含 workspaceMode/workspacePath 字段）
- **前端状态**: `src/stores/app-store.ts` 新增 task 状态 + SSE reducer + undo stack
- **前端 API**: `src/lib/api.ts` 新增 task 函数
- **前端组件**: 9 个新组件（看板视图/列/卡片/详情/编辑器/sidebar 导航/上下文菜单/搜索筛选栏/撤销 toast）
- **前端路由**: `src/components/sidebar.tsx` 新增 tasks 模式 + `src/app/page.tsx` 渲染
- **现有系统不受影响**: `create_plan` / `task_dispatch` / `DispatchPlanCard` 保持不变，新任务池是独立的高层次抽象
