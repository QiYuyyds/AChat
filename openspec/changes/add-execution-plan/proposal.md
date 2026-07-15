# Proposal: add-execution-plan

## Why

当前 Agent 的 ReAct 循环是"边想边做"模式——模型隐式规划、隐式执行，用户看不到 Agent 打算做什么，也不知道当前进展到哪一步。对于复杂任务（3步以上），用户缺乏可控感和进度感知，且模型容易在中途偏离初始目标。

## What Changes

- 新增 `execution_plan` MessagePart 类型：结构化执行计划卡片，包含步骤列表与实时状态
- 新增 3 个工具：`create_plan`、`plan_step`、`add_plan_steps`，让模型主动生成和更新执行计划
- 新增 2 个 SSE 事件：`plan.created`、`plan.step_update`，驱动前端实时渲染计划进度
- 新增前端 `ExecutionPlanPart` 组件：渲染 checklist 样式的进度卡片，步骤状态实时更新
- 修改 System Prompt：在 solo 模式下注入计划工具和指导，模型自主判断是否需要生成计划
- 修改 `consume_stream`：处理 `plan.created` 事件注入 `execution_plan` part（对称于 `artifact_ref` 注入路径）
- Run 结束时自动清理 Plan 终态（未完成步骤标 skipped/failed）

## Capabilities

### New Capabilities

- `execution-plan`: 结构化执行计划能力——模型通过工具生成可追踪的步骤列表，前端渲染为进度卡片，步骤状态实时更新

### Modified Capabilities

- `message-parts`: 新增 `execution_plan` MessagePart 类型
- `stream-events`: 新增 `plan.created` 和 `plan.step_update` 事件
- `tools`: 新增 `create_plan`、`plan_step`、`add_plan_steps` 三个工具

## Future Work (Out of Scope for Phase 1)

- **Phase 2 — Coordinated 联动** → 已创建 change: `add-execution-plan-coordinated`
- **Prompt 优化** → 已创建 change: `add-execution-plan-prompt-optimization`

## Impact

- **后端**：新增 `tools/execution_plan.py`；修改 `agent_runner.py`（consume_stream part 注入 + 工具事件生成）、`agent_loop.py`（prompt 注入 + 工具注入）、`schemas/events.py`（新事件类型）、`tools/registry.py`（注册新工具）
- **前端**：修改 `shared/types.ts`（MessagePart + StreamEvent 新分支）、`stores/app-store.ts`（reducer 新增 plan.step_update case）、`components/message-parts.tsx`（新增 ExecutionPlanPart 组件 + PartRenderer case）
- **内存**：新增轻量 `plan_registry`（in-memory，run 结束即清除，存储 plan 步骤状态供工具 handler 读写）
- **无 DB 迁移**：parts 是 JSON 列，无需 schema 变更
- **无破坏性变更**：新 MessagePart 类型 + 新事件，旧数据不受影响
