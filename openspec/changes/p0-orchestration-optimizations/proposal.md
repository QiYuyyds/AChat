# Proposal: P0 编排优化

## Why

当前 AChat 的上下文管理只有单层 LLM 摘要（watermark=10 触发），长会话 token 浪费严重；Orchestrator 的 Plan 阶段允许 LLM 跳过项目探索直接规划，导致盲目拆任务；DAG 调度同波次内无优先级，长任务可能排在后面拖慢整体；聚合阶段只做单次总结，缺少跨任务一致性和完成度分析。

通过对标 Claude Code 的四层渐进式压缩和 Explore-Plan-Act 模式，这四个「无依赖、低风险、高 ROI」的优化可以立即落地，不触碰核心架构。

## What Changes

### O1: 多层上下文压缩（conversation-context）

- 新增 **tool_result 裁剪层**：单条 tool_result 超过阈值时，将旧的 tool_result 替换为裁剪标记 `[tool_result 已裁剪, 详见 message_id]`，保留最近 N 轮完整
- 新增 **旧消息折叠层**：消息数超过阈值时，将最早的若干条折叠为 `[N 条消息已折叠]`，不调 LLM，纯结构化处理
- **优化 LLM 摘要触发**：从纯消息数 watermark 改为 token 估算 + 消息数双阈值，加入 87%/90%/93% 分级警告
- 不改变现有 `ContextSummary` 的结构和持久化方式

### O4: Plan 阶段强制 Explore（orchestrator）

- Orchestrator plan prompt **强制要求**规划前先用 `fs_list`/`fs_read` 扫描 workspace 结构
- `plan_tasks` 工具参数新增 **advisory** 字段 `complexity`（`simple`/`moderate`/`complex`）和 `explored`（探索过的文件列表）
- System prompt 加复杂度引导：简单任务不拆分，直接单任务 plan

### O10: DAG 动态优先级（orchestrator）

- `_execute_dag` 中同一 wave 的 `ready` 列表在 `asyncio.gather` 前按预估耗时排序
- 排序权重：code 任务 > document 任务 > review 任务；有 `target_paths` 的任务优先

### O12: 聚合深度分析（orchestrator）

- Aggregate prompt 新增分析维度：跨任务一致性检查、整体完成度评分（0-100）、智能下一步推荐
- 不改变聚合阶段的代码结构，只改 `build_orchestrator_aggregate_prompt` 和 `build_aggregate_prompt` 的 prompt 文本

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `conversation-context`: 新增 tool_result 裁剪层和旧消息折叠层的要求；优化 LLM 摘要触发条件从纯消息数改为 token+消息数双阈值
- `orchestrator`: Plan 阶段新增强制 Explore 和复杂度评估要求；DAG 执行新增同波次任务优先级排序要求；Aggregate 阶段新增跨任务一致性检查和完成度评分要求

## Impact

### 代码影响

- `backend/app/services/context_compaction_service.py` — 新增裁剪/折叠函数，优化触发逻辑
- `backend/app/services/conversation_context.py` — `build_history_for` 增加裁剪/折叠处理
- `backend/app/services/orchestrator.py` — `_execute_dag` 加 `ready.sort()`；无其他结构改动
- `backend/app/services/orchestrator_prompts.py` — plan/aggregate prompt 文本更新
- `backend/app/schemas/dispatch.py` — `DispatchPlanItem` 加 advisory `complexity` 字段

### API 影响

- `plan_tasks` 工具参数新增 `complexity`（advisory，不强制），向后兼容
- 无 breaking change

### 依赖影响

- 无新增依赖
- 不改 DB schema
- 不改事件协议（StreamEvent 不变）

### 测试影响

- `context_compaction_service` 新增裁剪/折叠的单元测试
- `dispatch_plan` 新增 `complexity` 字段的校验测试
- `orchestrator` DAG 排序的单元测试
