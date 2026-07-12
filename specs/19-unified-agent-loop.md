# Spec 19: Unified Agent Loop

> 替代旧的三阶段 Orchestrator（PLAN → EXECUTE → AGGREGATE + 验证 gate）。
> 所有 Agent（solo / orchestrated / subagent）走同一个 `run_agent_loop` while-loop。

---

## 1. 动机

旧 Orchestrator 有三阶段流程（plan_tasks → DAG 执行 → aggregate），验证 gate（`report_task_result` + `_evaluate_child_task_result`），以及重试 harness（`MAX_CHILD_TASK_ATTEMPTS`）。这套系统：

- 复杂度高（~1000 行 orchestrator.py）
- LLM 调用多（plan / sub-agent / verify / aggregate 各一轮）
- 验证 gate 经常误判（advisory issues 被当成 hard fail）
- 与 Claude Code 风格的 solo agent 体验不一致

Unified Agent Loop 将三种模式统一为一个 while-loop，移除验证 gate 和重试 harness。

## 2. 核心抽象

### 2.1 `run_agent_loop`

```python
async def run_agent_loop(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
    mode: LoopMode,  # 'solo' | 'coordinated' | 'subagent'
) -> RunExecutionResult
```

所有模式委托给 `execute_simple_run`（已有的 ReAct while-loop）。区别仅在于：
- **工具列表**：coordinated 模式注入 `task_dispatch` + `dispatch_plan`；solo 和 subagent 模式在 `dispatch_depth < MAX_DISPATCH_DEPTH` 时注入 `task_dispatch`
- **System prompt**：solo 注入自检提示 + 派发指导，coordinated 注入协调者指导，subagent 注入子 Agent 指导

### 2.2 三种模式

| 模式 | 触发条件 | 工具列表 | System Prompt |
|---|---|---|---|
| `solo` | `dispatch_mode='solo'` 或非 orchestrator | agent 工具 + `task_dispatch`（depth < MAX） | base + 软自检 + 派发指导 |
| `coordinated` | `dispatch_mode='orchestrated'` + `is_orchestrator=True` | agent 工具 + `task_dispatch` + `dispatch_plan` | base + 协调者指导 |
| `subagent` | `task_dispatch` / `dispatch_plan` 工具调用（`override_prompt` set） | agent 工具 + `task_dispatch`（depth < MAX） | base + 子 Agent 指导 |

> `MAX_DISPATCH_DEPTH = 3`。达到最大深度时，`task_dispatch` 不注入——该 Agent 为终端执行者。

### 2.3 路由逻辑（`execute_run`）

```python
if args.override_prompt:
    # Subagent runs use subagent mode (recursive dispatch enabled)
    result = await run_agent_loop(..., mode="subagent")
else:
    dispatch_mode = get_dispatch_mode(conv)
    if dispatch_mode == "coordinated" and is_orchestrator:
        result = await run_agent_loop(..., mode="coordinated")
    else:
        result = await run_agent_loop(..., mode="solo")
```

### 2.4 递归子 Agent 派发

任何 Agent（solo / coordinated / subagent）都可以通过 `task_dispatch` 克隆自己来处理子任务。

- **clone-self（默认）**：`task_dispatch` 不传 `agentId`（或传自己的 `agent_id`），子 Agent 是调用者的完整克隆（相同模型、工具、系统提示），仅任务提示不同
- **递归深度**：`dispatch_depth` 从 0 开始，每次 `spawn_subagent_loop` 传递 `dispatch_depth + 1`。`MAX_DISPATCH_DEPTH = 3` 时深度 3 为终端执行者
- **可见性**：clone-self 派发的消息 `hidden=true`（从对话历史和前端渲染中排除）；group-member 派发的消息 `hidden=false`（正常可见）
- **防环**：subagent 模式（非 coordinated）只能克隆自己，不能派发给其他群成员，防止 A→B→A 循环

### 2.5 `dispatch_visibility` 与 `Message.hidden`

- `dispatch_visibility`：`RunArgs` 上的字段，值为 `"visible"`（group-member 派发）或 `"hidden"`（clone-self 派发）
- `Message.hidden`：`messages` 表的 boolean 列（默认 `false`），`persist_event` 根据 `dispatch_visibility` 设置
- `build_history_for`：查询时过滤 `hidden == False`，防止 clone-subagent 消息污染上下文
- 前端 `useMessagesForConversation`：过滤 `hidden=true` 消息，不渲染在聊天视图

## 3. TaskDispatch 工具

- **名称**: `task_dispatch`
- **参数**: `agentId` (string, **optional**), `taskDescription` (string, required), `dependsOn` (string[], optional)
- **行为**: 
  - 不传 `agentId`（或传调用者自己的 `agent_id`）→ clone-self，`dispatch_visibility='hidden'`
  - 传其他 `agentId`（仅 coordinated 模式）→ group-member 派发，`dispatch_visibility='visible'`
  - 同步调用 `spawn_subagent_loop`，等待子 Agent 完成，返回 `{ status, summary }`
- **深度检查**: `dispatch_depth >= MAX_DISPATCH_DEPTH` 时返回错误
- **防环检查**: 非 coordinated 模式下传其他 `agentId` 返回错误
- **注册**: 全局注册在 `tool_registry`，在 solo / subagent / coordinated 模式下注入（当 `dispatch_depth < MAX_DISPATCH_DEPTH`）
- **适用场景**: 克隆自己处理子任务、单个即时派发、探索性任务

### 3.1 `spawn_subagent_loop`

```python
async def spawn_subagent_loop(
    agent_id: str,
    task_description: str,
    conversation_id: str,
    trigger_message_id: str,
    parent_run_id: str,
    parent_cancel_event: asyncio.Event,
    workspace_path: str | None = None,
    on_start: Callable[[str], None] | None = None,
    dispatch_depth: int = 0,
    dispatch_visibility: str = "visible",
) -> LoopRunResult
```

- 创建新的 `RunArgs`（带 `override_prompt` + `parent_run_id` + `parent_cancel_event` + `dispatch_depth` + `dispatch_visibility`）
- 调用 `run_with_args` 启动子 run
- `on_start` 回调在子 run 创建后立即调用（传入 `child_run_id`），用于发射 `dispatch.start` 事件
- `dispatch_depth` 传递父 run 的深度 + 1
- `dispatch_visibility` 决定子 run 消息的 `hidden` 属性
- 等待完成，提取最终文本
- 返回 `LoopRunResult(status, text, artifact_ids, output_message_ids, run_id)`

## 3a. dispatch_plan 工具（DAG 派发）

- **名称**: `dispatch_plan`
- **参数**: `tasks` (array of `{ id, agentId?, task, dependsOn? }`, required) — `agentId` 可选，省略时 clone-self
- **行为**: 声明一个结构化 DAG，系统进行拓扑排序并按 wave 调度——同一 wave 内独立任务并行执行，依赖任务等待上游完成。返回 `{ tasks: { <id>: { status, summary } } }` 平坦 map
- **深度/防环检查**: 与 `task_dispatch` 相同
- **可见性**: 所有任务都 clone-self 时 `visibility='hidden'`；有任何 group-member 时 `visibility='visible'`
- **注册**: 全局注册在 `tool_registry`，仅 coordinated 模式注入（solo/subagent 暂不支持 DAG）
- **适用场景**: 3+ 子任务且有明确依赖关系、用户要求生成完整项目（PRD → 设计 → 前端+后端 → 集成测试）

### 3a.1 DAG 验证

`validate_dag(tasks)` 检查：
- 重复 id
- 自依赖（`dependsOn` 包含自身）
- 缺失的 `dependsOn` 引用
- 环（通过拓扑排序检测）

验证失败时返回错误 tool result，不启动任何子 Agent。

### 3a.2 波调度

`topological_waves(tasks)` 将任务分组为波：
- 每波包含所有依赖都在之前波中的任务
- 同一波内的任务通过 `asyncio.gather` 并行执行
- 检测到环时抛出 `ValueError`

### 3a.3 执行语义

`execute_dag(tasks, ctx)` 逐波执行：
- **ready** 任务（所有依赖成功完成）→ 调用 `spawn_subagent_loop` 并行执行
- **skipped** 任务（任一上游失败/中止）→ 标记 `skipped`，不执行
- 每个节点发射 `dispatch.start`（开始时）和 `dispatch.end`（结束时）事件
- skipped 节点只发射 `dispatch.end`（`status="skipped"`，无 `childRunId`）
- 返回 `dict[task_id, NodeResult]`，`NodeResult` 含 `task_id`, `status`, `summary`, `child_run_id`

### 3a.4 可选计划审批

当 `plan_approval_enabled` 为 `True`（默认 `False`，从 `AppSettings.settings` JSONB 读取）时：
1. 通过 `pending_dispatch_plans.register()` 注册待审批计划，发射 `dispatch.plan.pending`
2. 等待用户决定（通过 `asyncio.Future` + `cancel_event` 竞争）
3. **approve**: 重新验证计划，执行 DAG
4. **reject**: 返回 `{ status: "rejected" }`
5. **cancel**（父 run 被取消）: 返回 `{ status: "aborted" }`

### 3a.5 与 task_dispatch 的关系

两个工具共存，协调者 LLM 根据场景选择：
- `dispatch_plan`: 结构化多任务 DAG（有明确依赖关系）
- `task_dispatch`: 单个即时派发（探索性、快速试错）

系统提示词明确指导何时使用每个工具。无验证 gate、无重试 harness、无自动重规划——LLM 可根据返回结果自行决定是否重新派发失败的任务。

## 4. Conversation.dispatch_mode

- **字段**: `dispatch_mode VARCHAR NOT NULL DEFAULT 'solo'`
- **值**: `'solo'` | `'orchestrated'`
- **映射**: `'orchestrated'` (DB) → `'coordinated'` (loop mode)
- **向后兼容**: `get_dispatch_mode` 使用 `getattr` 防御缺失字段
- **设置时机**: 创建会话时根据 `mode` 自动设置（group → orchestrated, single → solo）

## 5. 已移除的旧代码

| 组件 | 状态 |
|---|---|
| `plan_tasks` 工具 | 已删除 |
| `report_task_result` 工具 | 已删除 |
| `task_result_report.py` 服务 | 已删除 |
| `dispatch_plan.py` 服务 | 已删除 |
| `verify_stage.py` 服务 | 已删除 |
| `orchestrator.py` 三阶段逻辑 | 已替换为 stub |
| `DispatchExpectedOutput` / `DispatchRequiredCommand` / `TaskResultReport` schema | 已删除 |
| `RunArgs.require_task_report` | 已删除 |
| `RunExecutionResult.task_report` | 已删除 |
| `ORCHESTRATOR_PLAN_ALLOWED_TOOLS` / `MAX_DISPATCH_ROUNDS` / `MAX_CHILD_TASK_ATTEMPTS` 常量 | 已删除 |
| `plan_tasks` 终端工具调用处理 | 已删除 |

## 6. 迁移注意事项

1. **DB Schema**: `conversations` 表新增 `dispatch_mode` 列；`messages` 表新增 `hidden BOOLEAN NOT NULL DEFAULT FALSE` 列，`engine.py` 中有 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 自动迁移
2. **旧会话**: 缺少 `dispatch_mode` 的旧会话默认为 `'solo'`；缺少 `hidden` 的旧消息默认为 `false`（可见）
3. **PendingDispatchPlans**: `pending_dispatch_plans` 模块现在被 `dispatch_plan` 工具的审批流程复用（`plan_approval_enabled=True` 时创建新的 pending plan）
4. **dispatch_run_evidence**: 保留（fs_write / bash 等工具仍在使用），但不再用于验证 gate
5. **Token 汇总**: 前端 `useConversationUsageTotal` hook 通过 `parentRunId` 链回溯到顶层 run，将 subagent token 归属到顶层 agent 的 `subagentTokens` / `subagentRunCount` 字段

## 7. 文件地图

| 文件 | 职责 |
|---|---|
| `backend/app/services/agent_loop.py` | `run_agent_loop` / `spawn_subagent_loop` / `get_dispatch_mode` / prompt builders |
| `backend/app/services/agent_runner.py` | `execute_run`（路由）/ `execute_simple_run`（ReAct loop）/ `consume_stream` |
| `backend/app/tools/task_dispatch.py` | `task_dispatch` 工具定义 + handler |
| `backend/app/tools/dispatch_plan.py` | `dispatch_plan` 工具定义 + handler（DAG 派发 + 可选审批） |
| `backend/app/services/dag_executor.py` | DAG 验证 / 波调度 / 执行（`validate_dag` / `topological_waves` / `execute_dag`） |
| `backend/app/services/orchestrator.py` | stub（已移除三阶段逻辑） |
| `backend/app/services/orchestrator_prompts.py` | 仅保留 `extract_text_from_parts` 等工具函数 |
