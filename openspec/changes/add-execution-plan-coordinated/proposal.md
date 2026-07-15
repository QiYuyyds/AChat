# Proposal: add-execution-plan-coordinated

## Why

Phase 1（`add-execution-plan`）已落地，solo 模式下 Agent 可以创建执行计划并追踪进度。但在 coordinated 模式中，协调者使用 `dispatch_plan` 分发子任务，与 `create_plan` 完全独立——用户看不到协调者的总体工作计划，子任务完成时 plan step 也不会自动打勾。两个 Plan 体系割裂，用户在群聊场景中仍然缺乏全局进度感知。

## What Changes

- 在 coordinated 模式下注入 `create_plan`、`plan_step`、`add_plan_steps` 工具
- 协调者的 system prompt 新增执行计划指导，与现有 `dispatch_plan` 指导并存
- `dispatch_plan` 执行完成时（`dispatch.end` 事件），系统自动更新对应的 `execution_plan` part 步骤状态
- 新增 `plan_dispatch_mapping` 注册表：维护 plan step → dispatch task 的映射关系
- 协调者可先调 `create_plan` 展示总体计划，再调 `dispatch_plan` 分发子任务；也可只用 `create_plan` 自己执行部分步骤

## Capabilities

### New Capabilities

- `execution-plan-dispatch-link`: 执行计划与 dispatch_plan 的联动能力——dispatch task 完成时自动更新对应 plan step 状态

### Modified Capabilities

- `execution-plan`: 工具注入从 solo 扩展到 coordinated 模式；新增 prompt 指导协调者如何组合使用 create_plan 与 dispatch_plan
- `orchestrator`: 协调者 system prompt 新增执行计划使用指导

## Impact

- **后端**：修改 `agent_loop.py`（coordinated 模式注入 plan 工具 + prompt）、修改 `consume_stream`（dispatch.end 事件联动 plan step 状态更新）、新增 `plan_dispatch_mapping.py`（映射注册表）
- **前端**：无需改动——plan.step_update 事件已在 Phase 1 支持
- **无 DB 迁移**
