# Design: add-execution-plan-prompt-optimization

## Context

Phase 1 的 `_PLAN_SUFFIX` prompt 指导是：

```
当你面对一个复杂任务（3步以上），建议先创建执行计划
简单任务（1-2步），直接做就好
```

这个指导过于粗放，模型经常误判：
- "帮我写一个快速排序" → 1步就能完成，但模型可能认为需要"理解需求→写代码→测试"3步
- "重构整个用户模块" → 显然需要 plan，但模型可能直接开始改文件

`complexity` 参数目前仅存储在 `execution_plan` part 中，没有任何统计反馈。

## Goals / Non-Goals

**Goals:**

- 提升"简单 vs 复杂"的判断准确率（减少不必要的 create_plan 调用，增加必要时的 create_plan 调用）
- 收集可量化的统计数据，形成 prompt 优化的反馈闭环
- 提供 few-shot 示例帮助模型理解边界条件
- 可选：为常见任务类型提供 Plan 模板，减少模型规划 token 开销

**Non-Goals:**

- 不做模型 fine-tuning（纯 prompt 优化）
- 不做用户自定义 prompt（这是全局优化，不是 per-agent 定制）
- 不改变 plan 工具的接口或行为

**Future Work:**

- **A/B 测试**：对 prompt 变体做 A/B 测试，量化对比准确率
- **自适应 prompt**：根据用户历史任务的 complexity 分布动态调整指导词

## Decisions

### D1: 统计数据收集方式——agent_runs.usage 扩展 vs 独立表

**选择**：在 `agent_runs` 表的 `usage` JSON 列中追加 `plan` 统计字段。

```json
{
  "inputTokens": 1234,
  "outputTokens": 567,
  "plan": {
    "created": true,
    "complexity": "moderate",
    "stepCount": 4,
    "completedSteps": 3,
    "skippedSteps": 1
  }
}
```

**备选**：
- A) 独立 `plan_usage` 表 → 过重，统计数据是 run 的附属信息
- B) 只存 execution_plan part 里 → 查询不方便，需要遍历 messages

**理由**：`usage` 列已是 JSONB，追加字段零迁移。Run 结束时 `plan_registry` 的数据可一次性写入。

### D2: Prompt 优化策略——few-shot + 边界条件细化

**选择**：三层优化：

1. **边界条件细化**：把"3步以上"改为更具体的场景描述
   - 不用 create_plan："改一个配置项"、"回答一个问题"、"读一个文件"
   - 要用 create_plan："涉及3个以上文件的修改"、"需要先研究再实现"、"用户明确要求分步执行"

2. **Few-shot 示例**：在 prompt 中嵌入 2-3 个典型判断示例
   - 正例：用户要求"搭建一个完整的用户系统" → create_plan
   - 反例：用户要求"修复这个 typo" → 直接做

3. **Self-check 提示**：在 prompt 末尾加一句"如果你不确定是否需要 create_plan，问自己：用户是否需要看到我的工作计划？如果不需要，直接做。"

**理由**：从粗规则→具体场景→自我校验，层层递进。few-shot 示例对 LLM 的判断力提升最显著。

### D3: Plan 模板——可选增强

**选择**：Phase 1 不实现模板。Phase 2 可选，作为 `create_plan` 的 `template` 参数。

**理由**：模板需要维护任务类型分类体系（code refactor / feature development / bug investigation / ...），这个体系本身需要从统计数据中自然涌现，不能预设。先有数据，再建模板。

### D4: 统计 API 端点

**选择**：新增 `GET /api/plan-usage/stats` 端点，返回聚合统计：

```json
{
  "totalRuns": 1000,
  "withPlan": 300,
  "withoutPlan": 700,
  "complexityDistribution": { "simple": 50, "moderate": 180, "complex": 70 },
  "avgStepCount": { "simple": 2.1, "moderate": 4.3, "complex": 6.7 },
  "completionRate": { "simple": 0.95, "moderate": 0.82, "complex": 0.71 }
}
```

**理由**：给运营/开发者一个观测面板，量化"模型判断准确率"这个抽象概念。

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Few-shot 示例增加 prompt token 开销 | 示例控制在 200 token 以内，对比减少的不必要 create_plan 调用（省 ~100 token/次），净收益为正 |
| 统计数据样本量小，误判分析不可靠 | 初期不依赖统计做自动化决策，仅作为人工 prompt 优化的参考 |
| 边界条件细化可能过拟合当前模型行为 | 定期复查统计，如果某个规则导致误判就调整；prompt 不是一锤子买卖 |
