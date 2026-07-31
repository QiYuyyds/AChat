# Design: Add Effective Context Window

## Context

项目 `model_registry.py`（后端）和 `model-registry.ts`（前端）维护一份模型参数表，其中 `context_window` 字段表示模型的物理上下文窗口大小。token budget 计算链路如下：

```
get_model_limits(provider, model_id)
  → ModelLimits(context_window=1_000_000, output_reserve=4_096)
    → agent_runner: history_budget = context_window - output_reserve - prompt_estimate
      → build_history_for(token_budget=history_budget, model_context_limit=context_window)
        → conversation_context: ratio = loaded_tokens / model_context_limit
          → ratio ≥ 0.65: pre-run 裁剪
          → ratio ≥ 0.70~0.88: run 内五阶段压缩
          → ratio ≥ 0.87: auto-compact
```

DeepSeek V4 全系列物理窗口为 1M，因此实际 budget 上限约为 990K，压缩阈值在 650K~870K 区间触发。业界研究（Stanford "Lost in the Middle" TACL 2023、arxiv 2509.21361 "Maximum Effective Context Window"）表明 LLM 在超过 ~200K tokens 后质量显著退化。Claude Code 等主流 Agent 框架即使在 1M 可用时也默认使用 200K。

当前约束：
- 前后端各有一份 `model_registry`，必须同步修改
- CLI Agent（Claude Code / Codex）不经过此 budget 计算，不受影响
- `output_reserve` 当前 DeepSeek 非推理模式为 4K（DEFAULT），推理模式为 16K

## Goals / Non-Goals

**Goals:**
- 将有效上下文窗口 cap 到 200K，与业界实践对齐
- 保留物理窗口元信息（不丢失模型能力数据，用于定价展示等）
- DeepSeek 全系列 `output_reserve` 统一为 13K（学习 Claude Code 的 `AUTOCOMPACT_BUFFER_TOKENS`）
- 前后端同步

**Non-Goals:**
- 不修改压缩阈值比例（0.65 / 0.70 / 0.80 / 0.88 / 0.87 不变）
- 不修改 CLI Agent 路线
- 不引入环境变量配置（200K cap 先硬编码，未来需要再做成可配置）
- 不修改 `max_output_tokens` 字段（那是 provider 硬限，与 output_reserve 概念不同）

## Decisions

### D1: 新增 `effective_context_window` 字段而非修改 `context_window` 值

**选择**：在 `ModelLimits` 中新增 `effective_context_window` 字段，`= min(context_window, EFFECTIVE_CONTEXT_CAP)`。

**理由**：物理窗口是模型能力元信息，用于定价计算、UI 展示等。直接改 `context_window` 会丢失这些信息。

**备选方案**：
- A) 直接把 DeepSeek 的 `context` 从 1M 改成 200K → 丢失元信息，未来无法区分"模型真的只有 200K"和"我们 cap 到 200K"
- C) 全局 cap 常量 + `get_model_limits` 内做 `min()` → 语义不够显式，调用方不知道有 cap 存在

### D2: `EFFECTIVE_CONTEXT_CAP = 200_000` 硬编码常量

**选择**：在 `model_registry.py` / `model-registry.ts` 顶部定义全局常量。

**理由**：200K 是基于业界研究的工程默认值，不需要 per-model 配置。如果未来某些模型在 200K 以上仍保持高质量，可以升级为 per-model 字段。

### D3: DeepSeek 全系列 `outputReserve = 13_000`

**选择**：DeepSeek 所有 6 个模型的 `outputReserve` 统一设为 13_000。

**理由**：学习 Claude Code 的 `AUTOCOMPACT_BUFFER_TOKENS = 13_000`。对 200K 有效窗口来说，13K 占 6.5%，history_budget 仍有 ~185K，足够使用。推理模式（reasoner/r1）从 16K 降到 13K，差异不大（3K），因为 Agent 单轮 thinking + output 典型在 6-15K 范围。

### D4: budget 计算改用 `effective_context_window`

**选择**：`agent_runner.py` 中三处引用全部改用 `limits.effective_context_window`：
1. `history_budget = effective_context_window - output_reserve - prompt_estimate`
2. `build_history_for(model_context_limit=effective_context_window)`
3. `_get_agent_model_limit()` 返回 `effective_context_window`

**理由**：这三处是 budget 计算链路的全部入口，改完后所有下游 ratio 计算自动基于 200K。

### D5: 前端 `UsageBadge` 进度条分母改用 `effectiveContextWindow`

**选择**：`usage-badge.tsx` 第 51 行 `limits.contextWindow` → `limits.effectiveContextWindow`。

**理由**：用户看到的进度条应反映工程有效范围（200K），而非物理上限（1M）。这样进度条颜色变化（绿→黄→红）与实际压缩触发时机一致。

## Risks / Trade-offs

- **[Agent 历史保留更少]** → 200K vs 1M，历史消息更容易被压缩/截断。但这是有意的：更早压缩 = 更好的输出质量。补偿手段：分层记忆系统（LTM）+ RAG 按需召回。
- **[DeepSeek reasoner output_reserve 从 16K 降到 13K]** → 推理模式单轮输出可能偶尔超过 13K（长思维链）。但 Agent 场景下单轮输出极少超过 13K，且 `max_output_tokens` 仍是 384K（provider 硬限不受影响）。如果发现问题可以单独调回 16K。
- **[前后端不同步风险]** → 必须同一次 commit 修改两端。后端测试和前端 typecheck 都需要跑过。
