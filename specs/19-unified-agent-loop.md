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
- **工具列表**：coordinated 模式额外注入 `task_dispatch`
- **System prompt**：solo 注入自检提示，coordinated 注入协调者指导

### 2.2 三种模式

| 模式 | 触发条件 | 工具列表 | System Prompt |
|---|---|---|---|
| `solo` | `dispatch_mode='solo'` 或非 orchestrator | agent 自己的工具 | base + 软自检提示 |
| `coordinated` | `dispatch_mode='orchestrated'` + `is_orchestrator=True` | agent 工具 + `task_dispatch` | base + 协调者指导 |
| `subagent` | `task_dispatch` 工具调用 | agent 自己的工具 | base（无额外注入） |

### 2.3 路由逻辑（`execute_run`）

```python
if args.override_prompt:
    # Subagent runs always use simple run (solo mode)
    result = await execute_simple_run(...)
else:
    dispatch_mode = get_dispatch_mode(conv)
    if dispatch_mode == "coordinated" and is_orchestrator:
        result = await run_agent_loop(..., mode="coordinated")
    else:
        result = await run_agent_loop(..., mode="solo")
```

## 3. TaskDispatch 工具

- **名称**: `task_dispatch`
- **参数**: `agentId` (string, required), `taskDescription` (string, required), `dependsOn` (string[], optional)
- **行为**: 同步调用 `spawn_subagent_loop`，等待子 Agent 完成，返回 `{ status, summary }`
- **注册**: 全局注册在 `tool_registry`，但仅在 coordinated 模式下注入到 agent 的工具列表

### 3.1 `spawn_subagent_loop`

```python
async def spawn_subagent_loop(
    agent_id: str,
    task_description: str,
    conversation_id: str,
    trigger_message_id: str,
    parent_run_id: str,
    parent_cancel_event: asyncio.Event,
) -> LoopRunResult
```

- 创建新的 `RunArgs`（带 `override_prompt` + `parent_run_id` + `parent_cancel_event`）
- 调用 `run_with_args` 启动子 run
- 等待完成，提取最终文本
- 返回 `LoopRunResult(status, text, artifact_ids, output_message_ids)`

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

1. **DB Schema**: `conversations` 表新增 `dispatch_mode` 列，`engine.py` 中有 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 自动迁移
2. **旧会话**: 缺少 `dispatch_mode` 的旧会话默认为 `'solo'`
3. **PendingDispatchPlans**: `pending_dispatch_plans` 模块保留（API 仍引用），但不再有新的 plan 被创建
4. **dispatch_run_evidence**: 保留（fs_write / bash 等工具仍在使用），但不再用于验证 gate

## 7. 文件地图

| 文件 | 职责 |
|---|---|
| `backend/app/services/agent_loop.py` | `run_agent_loop` / `spawn_subagent_loop` / `get_dispatch_mode` / prompt builders |
| `backend/app/services/agent_runner.py` | `execute_run`（路由）/ `execute_simple_run`（ReAct loop）/ `consume_stream` |
| `backend/app/tools/task_dispatch.py` | `task_dispatch` 工具定义 + handler |
| `backend/app/services/orchestrator.py` | stub（已移除三阶段逻辑） |
| `backend/app/services/orchestrator_prompts.py` | 仅保留 `extract_text_from_parts` 等工具函数 |
