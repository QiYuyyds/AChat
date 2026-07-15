# Proposal: add-execution-plan-prompt-optimization

## Why

Phase 1（`add-execution-plan`）落地后，模型对"简单 vs 复杂"的判断并不稳定：有时简单任务也生成 Plan（浪费 tool call），有时复杂任务却直接开干（用户看不到进度）。`complexity` 参数目前仅存未用，缺乏统计反馈闭环来驱动 prompt 迭代。

## What Changes

- 收集 `create_plan` 调用的 `complexity` 统计数据，暴露给管理端
- 基于 Phase 1 运行数据分析误判模式（简单任务却 create_plan、复杂任务却没 create_plan）
- 优化 system prompt 中的"何时使用 create_plan"指导，增加 few-shot 示例和更明确的边界条件
- 新增 `plan_usage` 统计 API 端点（用于观测模型判断准确率）
- 可选：引入 Plan 模板——常见任务类型的预定义步骤列表，减少模型规划负担

## Capabilities

### New Capabilities

- `execution-plan-analytics`: 执行计划使用统计与误判分析能力——收集 complexity 自评数据、实际步骤数、是否生成了 Plan 等指标，供 prompt 优化参考

### Modified Capabilities

- `execution-plan`: 优化"何时使用 create_plan"的 prompt 指导，增加 few-shot 示例和更精确的判断条件

## Impact

- **后端**：修改 `agent_loop.py`（prompt 优化）、新增统计 API 端点、修改 `execution_plan.py`（记录统计数据）
- **前端**：可能需要管理端统计面板（Phase 2 可选）
- **无 DB 迁移**：统计数据可存 `agent_runs.usage` JSON 列或新表
