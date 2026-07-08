# Design: P0 编排优化

## Context

当前 AChat 的上下文管理和 Orchestrator 编排存在四个低风险、高收益的优化点：

1. **上下文压缩只有 1 层**：`context_compaction_service` 在 watermark=10 时触发单一 LLM 摘要。长会话中 tool_result 堆积占满上下文，且每次摘要都破坏 KV cache。
2. **Plan 阶段可跳过探索**：`ORCHESTRATOR_PLAN_ALLOWED_TOOLS` 虽含 `fs_list`/`fs_read`，但 system prompt 不强制 LLM 先探索，导致盲目规划。
3. **DAG 同波次无优先级**：`_execute_dag` 的 `ready` 列表无序，长任务可能排在后面拖慢关键路径。
4. **聚合只做单次总结**：`build_aggregate_prompt` 缺少跨任务一致性检查和完成度评分。

现有基础设施：`event_bus`（pub/sub）、`context_compaction_service`（单层 LLM 摘要）、`PromptAssembler`（schema 驱动上下文组装）、`build_history_for`（历史序列化）。

约束：不改 DB schema、不改 StreamEvent 协议、不改 adapter 接口、无新增依赖。

## Goals / Non-Goals

**Goals:**

- O1: 在 `build_history_for` 读取路径上增加 tool_result 裁剪层和旧消息折叠层，降低长会话 token 消耗 40-60%
- O4: 让 Orchestrator LLM 在规划前强制探索 workspace，并在 plan 中声明复杂度
- O10: 同波次 DAG 任务按预估耗时排序，长任务先启动
- O12: 聚合 prompt 增加一致性检查、完成度评分、下一步推荐

**Non-Goals:**

- 不做 ReAct 循环上提（P1 的 O2）
- 不做 Hooks 系统（P1 的 O3）
- 不做 Checkpoint & Resume（P2 的 O5）
- 不做 turn 间压缩（需要 O2 ReAct 上提）
- 不改 `ContextSummary` 表结构
- 不改 adapter 接口

## Decisions

### Decision 1: 裁剪/折叠在读取路径做，不在写入路径做

**选择**: 在 `build_history_for` 读取消息时做裁剪/折叠，不改消息写入逻辑。

**理由**:
- 消息写入是持久化的，裁剪后的数据不应覆盖原始数据（用户可能需要完整历史）
- 读取路径做裁剪可以根据不同 LLM 的上下文窗口动态调整阈值
- 改动范围最小：只改 `conversation_context.py`，不改 `persist_event`

**备选方案**: 在写入时裁剪 tool_result → 被否决，因为会丢失原始数据，且不同 adapter 可能需要不同的裁剪策略。

### Decision 2: tool_result 裁剪用 token 估算，不用字符数

**选择**: 用 `estimate_tokens` 估算 tool_result 大小，超过阈值（默认 2000 tokens）的旧 tool_result 替换为裁剪标记。

**理由**:
- 字符数与 token 数比例因语言而异（中文 1 字 ≈ 1-2 token，英文 1 词 ≈ 1-1.5 token）
- `estimate_tokens` 已在 `model_registry` 中实现，可复用
- 与 LLM 上下文窗口的计量单位一致

**裁剪规则**:
- 保留最近 N 轮（默认 3）的完整 tool_result
- 更早的 tool_result 若超过阈值 → 替换为 `[tool_result 已裁剪, 详见 message_id=xxx]`
- 不超过阈值的旧 tool_result 保持原样

### Decision 3: 旧消息折叠用消息数阈值，不调 LLM

**选择**: 消息数超过阈值（默认 30）时，将最早的若干条折叠为 `[N 条消息已折叠, 涵盖时间 range]`，纯结构化处理。

**理由**:
- LLM 摘要已有 `compact_conversation` 负责，折叠层只做轻量级结构化压缩
- 不调 LLM = 零延迟、零成本
- 与 LLM 摘要层互补：折叠处理「量」，LLM 摘要处理「质」

**折叠规则**:
- 消息数 > 30 时，保留最近 20 条 + 所有 pinned 消息
- 被折叠的消息替换为一条 `[已折叠 N 条消息 (时间 range)]` 的 system 消息
- 折叠的消息仍可通过 `ContextSummary` 的 LLM 摘要覆盖

### Decision 4: LLM 摘要触发改为 token + 消息数双阈值

**选择**: `_maybe_auto_compact_hook` 从纯 `watermark >= 10` 改为 `watermark >= 10 OR estimated_tokens > 0.87 * model_limit`。

**理由**:
- 纯消息数阈值无法处理「少量大消息」场景（如几条巨大的 tool_result）
- 87% 阈值对标 Claude Code 的 AutoCompact 触发点
- 双阈值取 OR：任一满足即触发

### Decision 5: complexity 字段是 advisory，不参与校验

**选择**: `plan_tasks` 工具的 `complexity` 字段是 advisory，`compile_and_validate_dispatch_plan` 不校验它。

**理由**:
- 向后兼容：旧 plan 不带 complexity 也能通过校验
- complexity 只影响 system prompt 引导，不影响执行逻辑
- 如果未来需要根据 complexity 做路由（P2 的 O7），再升级为 required

### Decision 6: DAG 排序用静态权重，不做动态预估

**选择**: 同波次 `ready` 列表按静态权重排序：`code(3) > document(2) > review(1)`，有 `target_paths` 加权 +1。

**理由**:
- 动态预估需要历史数据（平均执行时间），当前没有收集
- 静态权重简单可靠，覆盖最常见的场景
- 未来可升级为基于历史数据的动态预估

**排序逻辑**:
```python
def _task_priority(task: DispatchPlanItem) -> int:
    base = {"code": 3, "document": 2}.get(task.task_kind, 1)
    if task.target_paths:
        base += 1
    return base

ready.sort(key=lambda t: -_task_priority(t))
```

### Decision 7: 聚合分析只改 prompt，不加代码逻辑

**选择**: 跨任务一致性检查、完成度评分、下一步推荐全部通过修改 `build_orchestrator_aggregate_prompt` 和 `build_aggregate_prompt` 的 prompt 文本实现。

**理由**:
- 评分和一致性判断是 LLM 擅长的语义任务，不需要硬编码规则
- 只改 prompt = 零代码风险
- 聚合结果仍然是文本消息，前端不需要改

## Risks / Trade-offs

- **[tool_result 裁剪丢失上下文]** → 保留最近 3 轮完整；裁剪标记包含 message_id，LLM 可通过 `read_artifact` 或重新调工具获取
- **[折叠消息信息量不足]** → LLM 摘要层（`compact_conversation`）仍会生成结构化摘要，折叠只是补充
- **[complexity 字段 LLM 不填]** → advisory 字段不强制，system prompt 引导填写但不阻塞
- **[DAG 排序权重不准]** → 静态权重是启发式，最差情况退化为无序（与现状相同）
- **[聚合 prompt 变长]** → 增加 3 个分析维度的指令约增加 200 tokens，可接受
