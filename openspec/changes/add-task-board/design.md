# Design: Add Task Board

## Context

AChat 已有的任务管理能力是 run-scoped 的：

- `create_plan` / `plan_step` / `add_plan_steps` — 内存态步骤卡片，run 结束清理
- `task_dispatch` — clone-self 或 group-member，在 run 内派发子任务
- `DispatchPlanCard` + DAG 可视化 — Orchestrator 调度计划

这些能力服务于「**一次对话内的工作安排**」。但用户需要一个**跨会话、持久化**的全局任务池：用户创建一批待办任务，Agent 定时认领并完成，用户评审后标记完成。

参考 dashi-taskboard 的设计（Codex 生态的 local-first issue board），但适配 AChat 的多 Agent 架构：

- dashi-taskboard 是单 Agent 平台（Codex），AChat 是多 Agent 平台
- dashi-taskboard 的调度通过 Codex cron RPC，AChat 用 asyncio 后台调度器（复用 memory pipeline 模式）
- dashi-taskboard 用 CDP 注入 UI，AChat 用原生 sidebar 模式
- 两者的乐观并发控制、任务生命周期设计一致

## Goals / Non-Goals

**Goals:**
- 持久化的全局任务池，跨会话、跨 run 存在
- 完整任务生命周期：`backlog → todo → in_progress → in_review → done` + `blocked / canceled`
- 乐观并发控制（version-based OCC），多 Agent 可安全并发认领
- asyncio 后台调度器，周期扫描 `todo` 任务并通过 `run_agent_loop(mode='solo')` 派发
- Agent 工具：自主认领、完成、评论任务
- 前端 Kanban 看板 UI + SSE 实时更新
- 小A 管理工具：创建/分配任务、启停调度器
- 任务可绑定沙箱或本地项目目录，调度器派发时使用绑定的 workspace
- 完整的前端 Kanban 交互：拖拽改变状态、拖拽排序、撤销/重做、列可见性控制、空列显示控制、任务搜索、任务筛选、上下文菜单、任务复制

**Non-Goals:**
- 任务间依赖关系（parent/sub/blocks/related）—— 后续迭代
- Worktree 隔离（调度器为任务创建独立 worktree）—— 后续迭代
- 任务标签管理系统（手动标签，不做标签 CRUD）—— 后续迭代
- 任务的 recurring / due-date 自动提醒 —— 后续迭代
- Cloud 协作模式（Cloudflare 部署）—— 后续迭代

## Decisions

### D1: 新增独立 Task 实体，不扩展 Plan 系统

**选择**：新建 `Task` + `TaskComment` DB 表，独立于现有 `PlanState`（内存态）。

**理由**：
- `PlanState` 的语义是「run 内步骤追踪」——它是 `pending → in_progress → done` 的线性步骤列表，没有 assignee、priority、跨会话持久化
- Task 需要的语义是「全局任务池」——有 assignee、priority、OCC version、跨会话可见
- 强行扩展 Plan 会导致概念混乱（plan step ≠ task）

**替代方案**：将 `PlanState` 持久化到 DB 并加跨会话聚合视图。否决理由：语义不匹配，plan step 缺少 assignee / priority / 完整生命周期。

### D2: Task 与 Conversation 的关系——认领时创建绑定

**选择**：`Task.conversation_id` 初始为 `NULL`。调度器认领任务时创建新 Conversation，绑定 `conversation_id`。一个 Task 生命周期内可绑定多个 Conversation（失败重试场景）。

**理由**：
- Task 是「做什么」，Conversation 是「在哪做」——两者解耦
- 用户创建任务时不需要指定会话
- 调度器认领时自动创建会话，Agent 在会话中工作
- 失败回退后重新认领会创建新会话

**替代方案**：Task 必须属于某个 Conversation。否决理由：限制了用户在对话外创建任务的能力。

### D3: 调度器用 asyncio 后台循环，不用 cron 库

**选择**：`TaskSchedulerService` 用 `asyncio.create_task` + `while True: sleep(interval)` 模式，与现有 `auto_dream` pipeline 一致。

**理由**：
- AChat 已有此模式（memory pipeline），无需新依赖
- CLAUDE.md 禁止随意加依赖
- 进程重启后调度器丢失，但 Task 是持久化的，重启后重新扫描即可

**替代方案**：引入 APScheduler。否决理由：新依赖，且 asyncio 后台循环已足够。

### D4: 乐观并发控制——version 字段 + ifVersion 参数

**选择**：`Task` 和 `TaskComment` 都有 `version` 字段（int，初始 1）。每次 update / move / claim / complete 必须传 `ifVersion`，不匹配返回冲突错误。

**理由**：与 dashi-taskboard 的 OCC 设计一致，防止多 Agent 并发认领同一任务。

**替代方案**：DB 行锁（`SELECT ... FOR UPDATE`）。否决理由：SQLite 不支持行锁，且 OCC 更适合「先读后写」的场景。

### D5: Agent 工具作为可选工具，不进 baseline

**选择**：`task_list` / `task_claim` 等 7 个工具加入 Custom Agent 的可选工具列表（`agent.tool_names`），不进 baseline 9 工具。

**理由**：
- 不是所有 Agent 都需要任务管理能力
- 用户在 Agent Builder 中勾选「任务管理」才注入这些工具
- CLI Agent（Claude Code / Codex）不参与 baseline 合并，也跳过这些工具

### D6: 小A 的 manage_tasks 工具复用现有管理工具模式

**选择**：新增 `manage_tasks` 工具，action 列表包括 `list` / `create` / `update` / `move` / `assign` / `delete` / `scheduler_start` / `scheduler_stop` / `scheduler_status`。

**理由**：与现有 `manage_agents` / `manage_skills` / `manage_mcp` 等管理工具模式一致。小A 作为管理门面，用户通过小A 语音管理任务池。

### D7: SSE 事件复用现有 event_bus，不新建通道

**选择**：新增 6 个 Task 事件类型，通过现有 `event_bus.publish()` 发布，前端 SSE 连接自动接收。

**理由**：AChat 的 SSE 是一条全局连接，所有事件通过 `event_bus` fan-out。Task 事件不应开新通道。

### D8: 前端用 Sidebar 新模式，不用浮窗

**选择**：Sidebar 新增 `tasks` 模式，主区域渲染 Kanban 看板。

**理由**：
- Kanban 看板需要足够空间，浮窗太小
- 与现有 `conversations` / `artifacts` / `agents` 模式一致
- 任务列表也可在 sidebar 侧边显示

### D9: 调度器派发的完整调用链

**选择**：`_dispatch_task` 不直接调用 `run_agent_loop`，而是走标准入口 `run_with_args` → `execute_run` → `run_agent_loop(mode='solo')`。

**完整流程**：
1. 创建 Conversation（指定 `agent_id`，创建空 sandbox workspace）
2. 创建 trigger Message（role=user，内容=`build_task_prompt(task)`）
3. 构造 `RunArgs`（`agent_id` / `conversation_id` / `trigger_message_id` / `user_id` / `dispatch_mode='solo'`）
4. 调用 `run_with_args(args)` → 内部调 `execute_run` → 插入 AgentRun 记录 → 发布 `RunStartEvent` → 解析 workspace → 构建 adapter input → `run_agent_loop(mode='solo')`
5. 等待 run 完成，根据 `RunResult.status` 更新 task 状态

**理由**：`run_agent_loop` 不是直接调用的入口——`execute_run` 做了大量准备工作（插入 AgentRun 记录、发布 RunStartEvent、解析 workspace、构建 adapter input、处理 cancel_event）。跳过这些步骤会导致 run 记录缺失、SSE 事件不完整、workspace 未初始化。

### D10: manage_tasks 发射 GuideSideEffectEvent

**选择**：`manage_tasks` 的所有变更操作（create / update / move / assign / delete / scheduler_start / scheduler_stop）执行成功后，调用 `emit_guide_side_effect(ctx=ctx, target="tasks", action=...)` 发射 `guide_side_effect` SSE 事件。

**理由**：现有 `manage_agents` / `manage_skills` / `manage_mcp` 等管理工具都在变更后发射 `guide_side_effect`，前端通过 `useGuideSideEffectRefresh('tasks', callback)` 监听并刷新对应面板。如果不发，用户通过小A 创建/移动任务后，看板 UI 不会自动刷新——需要手动切换 tab。

### D11: 更新 _MANAGEMENT_TOOL_NAMES 集合

**选择**：在 `backend/app/services/agent_runner.py` 的 `_MANAGEMENT_TOOL_NAMES` frozenset 中添加 `"manage_tasks"`。

**理由**：Guide Agent 的工具注入逻辑依赖此集合——只有在此集合中的工具才会被注入到 `is_guide=True` 的 Agent。不加的话 `manage_tasks` 会被 Guide 工具过滤逻辑剔除，小A 无法使用。同时，非 guide Agent 的 `manage_tasks` 也会被过滤（与现有管理工具一致）。

### D12: Task SSE 事件的 conversationId 路由

**选择**：Task SSE 事件的 `conversationId` 设为空字符串 `""`，前端 SSE reducer 对 `conversationId === ""` 的事件走**全局 task store 更新路径**，不走 conversation 分桶逻辑。

**理由**：现有 SSE reducer 按 `conversationId` 分桶事件（更新 unread count、追加消息等）。Task 事件不属于任何会话，如果走 conversation 分桶会导致空字符串 key 意外创建空 conversation bucket。前端需在 SSE reducer 入口处对 Task 事件类型做 early return，直接派发到 task store actions。

### D13: failure_count 字段防止无限重试

**选择**：Task 实体新增 `failure_count` 字段（int，初始 0）。调度器失败回退时 `failure_count += 1`；Agent 成功完成（`task_complete`）时重置为 0。调度器扫描时跳过 `failure_count >= MAX_FAILURES`（默认 5）的 `todo` 任务。

**理由**：即使不做完整的 `retry_count` + `max_retry` 机制，也需要防止持续失败的任务（如 Agent 缺少 API Key）被无限重试、每 5 分钟消耗一次 LLM 调用额度。`failure_count` 是最小代价的防护——一个 int 字段 + 一行跳过逻辑。

**替代方案**：完整的 `retry_count` + `max_retry` + 退避策略。否决理由：本期 Non-Goal，`failure_count` + 硬阈值已足够防护。

### D14: Task Workspace Binding——绑定沙箱或本地项目

**选择**：Task 实体新增 `workspace_mode` 和 `workspace_path` 字段。`workspace_mode` 取值 `sandbox` / `local` / `null`（默认 `null`）。当 `workspace_mode === 'local'` 时，`workspace_path` 为本地项目绝对路径；当 `workspace_mode === 'sandbox'` 时，`workspace_path` 为 `null`（调度器自动创建沙箱目录）。调度器派发任务时：
- 有绑定 workspace：创建 Conversation 时使用绑定的 `workspace_mode` + `workspace_path` 作为 workspace 配置
- 无绑定（`null`）：创建 Conversation 时使用默认 sandbox 模式（当前行为不变）

**理由**：
- 用户创建任务时通常知道「这个任务要在哪个项目里做」——绑定后 Agent 直接在正确的目录工作，不需要用户在 prompt 里写路径
- 沙箱模式适合探索性任务（Agent 自由创建文件），本地模式适合针对真实项目的任务（Agent 修改用户代码）
- 与 AChat 现有 Workspace 架构对齐：`workspace.mode` 已有 `sandbox` / `local` 两种模式
- 与 dashi-taskboard 的 `developmentContext`（branch/worktree）类似，但适配 AChat 的 workspace 概念

**替代方案**：FK 到 `workspaces` 表。否决理由：workspaces 是 conversation-scoped 的，创建时才生成；任务绑定一个 workspace FK 会导致该 workspace 无法回收。用 `workspace_mode` + `workspace_path` 两个轻量字段更灵活。

### D15: 前端 Kanban 交互——完整看板体验

**选择**：前端看板 UI 实现完整的 Kanban 交互能力，参考 dashi-taskboard 的前端实现：

1. **拖拽改变状态**：卡片可从一列拖到另一列，松开时调用 `POST /api/tasks/{id}/move`
2. **拖拽排序**：卡片可在同列内拖拽重新排序，使用 `sortOrder` 字段（中点插入法计算新 sortOrder）
3. **撤销/重做**：前端维护 undo stack（最多 20 步），每次 create/move/update/archive 操作推入 stack，支持 `Ctrl+Z` / `⌘+Z` 快捷键撤销，toast 通知带「撤销」按钮
4. **列可见性控制**：用户可隐藏/显示任意状态列，设置持久化到 localStorage，按用户维度存储
5. **空列显示控制**：toggle 控制是否显示没有卡片的列
6. **任务搜索**：看板顶部搜索框，实时过滤标题 + 描述匹配的卡片
7. **任务筛选**：按 status / priority / labels / assignee 组合筛选
8. **上下文菜单**：右键卡片弹出菜单——快速改状态、改优先级、改标签、复制、归档
9. **任务复制**：在当前列创建一个副本（标题加「副本」后缀，status=backlog，version=1）

**理由**：
- dashi-taskboard 的前端已验证这些交互是 Kanban 看板的标准能力，用户期望「和 Linear / Trello 一样的操作体验」
- 拖拽和撤销是高频操作，没有它们用户体验会显著下降
- 搜索和筛选在任务超过 20 条后是刚需

**实现要点**：
- 拖拽使用原生 HTML5 Drag and Drop API（不引入 react-dnd 等新依赖）
- undo stack 存储逆向操作函数引用，非序列化快照（避免大对象拷贝）
- 搜索/筛选纯前端 useMemo 过滤，不额外请求 API
- 列可见性配置 key: `taskboard.columnVisibility`，空列配置 key: `taskboard.showEmptyColumns`

## Task Prompt Template

`build_task_prompt(task)` 生成的提示词模板：

```
你正在执行一个全局任务池中的任务。

## 任务信息
- 标题：{task.title}
- 描述：{task.description}
- 优先级：{task.priority}
- 标签：{task.labels}
- 工作目录：{workspace_description}

## 工作流程
1. 先用 create_plan 拆解任务步骤
2. 按步骤执行，每完成一步用 plan_step 更新状态
3. 全部完成后，调用 task_complete(taskId="{task.id}", ifVersion={task.version}, summary="<完成摘要>")

## 重要约束
- 你必须调用 task_complete 来标记任务完成，不要调用 task_move 到 done 状态
- task_complete 会将任务移到 in_review 状态，由用户评审后决定是否接受
- 如果遇到无法继续的阻塞，调用 task_move(taskId, status="blocked", ifVersion, reason="<阻塞原因>")
- 你可以使用 task_comment 添加评论来记录进度
```

`workspace_description` 生成规则：
- `workspace_mode === 'local'`：`本地项目目录 {workspace_path}`
- `workspace_mode === 'sandbox'`：`沙箱模式（自动创建临时工作目录）`
- `workspace_mode === null`：`沙箱模式（自动创建临时工作目录）`

## Risks / Trade-offs

- [调度器进程重启丢失] → 调度器是进程内 asyncio 任务，进程重启后丢失。缓解：Task 持久化在 DB，重启后调度器重新启动时扫描 `in_progress` 的任务（标记为 `todo` 回退或保持 `in_progress` 等待用户手动处理）。
- [多 Agent 并发认领冲突] → OCC 保证了同一任务不会被两个 Agent 同时认领。缓解：`if_version` 不匹配时返回冲突错误，Agent 跳过该任务。
- [调度器派发的 Agent 失败] → Agent run 可能失败（API 错误、超时等）。缓解：`_dispatch_task` 的 except 分支将任务回退到 `todo`，`failure_count += 1`，下次调度重试。
- [任务无限重试] → 失败回退到 `todo` 后可能无限重试。缓解：`failure_count` 字段 + `MAX_FAILURES=5` 硬阈值，达到上限后调度器跳过该任务，等待用户手动处理（D13）。
- [SQLite 并发写] → 调度器和 API 可能并发写 Task 表。缓解：SQLite WAL 模式 + OCC version 字段，写冲突时 SQLAlchemy 会重试或报错。SQLite `busy_timeout` 设为 30s（`connect_args={"timeout": 30}`）。

## Migration Plan

1. 新增 `tasks` + `task_comments` 表到 `models.py`
2. SQLAlchemy `create_all()` 自动建表（AChat 使用 SQLite，启动时 `init_db()` 调用 `Base.metadata.create_all`）
3. 无需数据迁移（全新表，无历史数据）
4. 回滚：删除两张表即可，不影响现有功能

## Open Questions

- 调度器选择的默认 Agent 如何确定？（本期通过 `manage_tasks(action=scheduler_start, agent_id=xxx)` 显式指定，后续可加自动匹配）
- 任务 `in_progress` 但 Agent run 已结束（完成或失败）时，如何自动更新状态？（本期依赖 Agent 主动调用 `task_complete`，后续可加 run 结束 hook 自动更新）
- `failure_count` 达到上限后如何恢复？（本期依赖用户手动重置——编辑任务后 `failure_count` 重置为 0，后续可加 UI 按钮）
