## Context

`UsageBadge` 组件（`src/components/usage-badge.tsx`）在 popover 里并排显示「新 Input 564k」「Cache 命中 488.7k」「实际 Prompt 564k」「当前 ctx 79.1k / 1.00M」。前三个是跨 turn 累加值（`run_usage.input_tokens +=`），最后一个是单 turn 快照值（`run_usage.last_input_tokens =`，覆盖取最后 turn）。对 DeepSeek，`prompt_tokens` 已含 `prompt_cache_hit_tokens`，所以 564k 里 488.7k 是缓存复用，但 UI 标注「新 Input · 按 1× 计费」未扣除 cache，失真。

根因在两个层面：
1. **数据层面**：`RunUsage` 只有 `lastInputTokens` 一个单次快照字段，无法拆解「最后 turn 的 cache 命中」和「最后 turn 的 output」，前端无法在单次栏展示拆解树。
2. **展示层面**：累加值与快照值并排，无分区无标注，用户无法区分维度。

## Goals / Non-Goals

**Goals:**
- 区分「累计（跨 N 轮）」与「最近一次调用（单 turn）」两个维度，上下分区展示
- 累计栏「新内容(净)」对 DeepSeek 扣除 cacheRead，对 Anthropic 加回 cacheCreation，使该行对两种 provider 都是「真·1× 计费量」
- 单次栏「当前 ctx」展开拆解树：缓存命中 + 新内容
- 顶部标注 turn 数，ctx 行补 hint「单轮，非累计」
- 后端 `RunUsage` 作为 token 语义单一数据源，前端不在 UI 层复刻 provider-aware 累减逻辑
- 累计栏底部新增「估算费用」行，根据模型定价表计算实际花费 + 缓存节省对比

**Non-Goals:**
- 不追踪 mid-run compact 后的「本轮峰值 ctx」（`lastInputTokens` 仍是最后一个 turn，若中途 compact 过峰值不可见）——留待后续 change
- 不改计费系统或 `usage_summary_service`（全局聚合仍按现有 `input + output + cacheRead + cacheCreation` 累加）
- 不增加新 StreamEvent 类型（仍是 `run.usage`，仅 payload 扩字段）
- 不改 `MessageUsage`（单条消息级 usage 不含 cacheCreation，本次不扩展）
- 不做 DB schema 迁移（`agent_runs.usage` 是 JSON 列，自然扩展）
- 不做实时汇率转换（CNY / USD 各自原币种显示，不互相换算）
- 不从后端 API 动态获取定价（MVP 硬编码在 `model-registry.ts`，后续可改为配置化）

## Decisions

### Decision 1: 上下分区，不左右并排

`PopoverContent` 是 `w-96`（384px）。左右并排每栏不到 180px，标签 + 数字 + 进度条放不下，且「累计」栏有 5 行、「单次」栏有 4 行，行数不对等左右会参差。上下分区每行有完整宽度，符合「先总结→再细节」的阅读流。

被否决方案：左右并排（宽度不足）、Tab 切换（增加交互成本、用户可能只看一栏错过另一栏）。

### Decision 2: 后端新增 3 字段，前端不做 provider-aware 累减

新增 `lastCacheReadTokens` / `lastOutputTokens` / `turnCount`。理由：
- 后端 `_RunUsage` 累加点（`agent_runner.py:1126-1129`）已在处理 `event.usage`，补两个覆盖赋值成本极低
- 前端若自行累减（`lastInputTokens - cacheReadTokens`），对 DeepSeek 成立但对 Anthropic 不成立（Anthropic 的 `input` 不含 cache，减出负数），需要 provider 判断，把 `usage.ts` 的 `computeTotalTokens` 同源逻辑散到 UI 层，易随 provider 扩展漂移
- 后端是 token 语义的单一数据源，前端只负责展示

`turnCount` 从 `term.model_call_count` 取（ReAct loop 的实际模型调用次数，含 forced final turn），不是 `turn` 变量（resume 时会偏移）。

### Decision 3: 「新内容(净)」provider-aware 计算放在前端 helper

虽然 Decision 2 说后端是数据源，但「净新内容」是一个**展示派生量**（累计 input 扣除累计 cacheRead），不是原始计量。在 `usage-badge.tsx` 内新增 `computeNetInput(provider, inputTokens, cacheReadTokens, cacheCreationTokens)` helper，复用 `usage.ts` 已有的 provider 判断信号（`cacheCreationTokens > 0` 表示 Anthropic 风格）：

```
DeepSeek (cacheCreation == 0):  netInput = inputTokens - cacheReadTokens
Anthropic (cacheCreation > 0):  netInput = inputTokens + cacheCreationTokens
```

这与 `computeCacheHitRate`（`usage-badge.tsx:345-357`）和 `computeTotalTokens`（`usage.ts:19-29`）同源，保持一致。

### Decision 4: ctx 行拆解树用缩进 + 树形字符

```
当前 ctx    79.1k / 1.00M (8%)  ████████░░░░
  ├ 缓存命中   ~70k  (88%)
  └ 新内容      ~9k
```

不新增进度条，只用缩进和 `├` / `└` 字符表达从属关系。`~` 表示估算（单次 cacheRead 是后端实测值，不是估算，但前缀 `~` 避免 用户以为 79.1k 精确等于 70k+9k 的四舍五入误差困惑）。实际上 `lastInputTokens = lastCacheReadTokens + netNew` 对 DeepSeek 严格成立，对 Anthropic `lastInputTokens + lastCacheCreationTokens = lastCacheReadTokens + netNew` 严格成立，不需要 `~`，但为 UX 容错保留。

### Decision 5: 全部新字段可选 + 向后兼容

`RunUsage` 新增字段在 Pydantic / TS 接口里全部 `Optional` 且 `default=0`。旧 `agent_runs.usage` JSON 记录缺这些字段时：
- 前端 `?? 0` 兜底，单次栏拆解显示「缓存命中 0」「新内容 = lastInputTokens」（对 DeepSeek 旧记录等于全量，可接受——旧 run 本就无拆解数据）
- `turnCount` 缺失时顶部不显示「· N 轮」（条件渲染）

无需 DB 迁移、无需回填。

### Decision 6: 价格估算——模型定价表放在 model-registry，费用计算在前端

价格数据天然和模型绑定（同模型同价格），放在 `model-registry.ts` 的 `KNOWN_MODELS` 表里最自然——和 `contextWindow` / `outputReserve` 同级。新增 `getModelPricing(provider, modelId)` 函数返回 `ModelPricing | null`。

```typescript
interface ModelPricing {
  currency: 'CNY' | 'USD'
  // 所有单价均 per 1M tokens
  inputCacheHit: number    // 缓存命中 input 单价
  inputCacheMiss: number   // 缓存未命中 input 单价（=净新内容单价）
  output: number           // output 单价
}
```

DeepSeek V4 官方定价（来源 https://api-docs.deepseek.com/zh-cn/quick_start/pricing ，2026年7月）：

| 模型 | cacheHit (CNY/1M) | cacheMiss (CNY/1M) | output (CNY/1M) |
|---|---|---|---|
| deepseek-v4-flash | 0.02 | 1 | 2 |
| deepseek-v4-pro | 0.025 | 3 | 6 |

费用计算公式（provider-aware，复用 `computeNetInput` 同源逻辑）：
```
DeepSeek:  cost = cacheRead × hitPrice + (input - cacheRead) × missPrice + output × outPrice
Anthropic: cost = input × missPrice + cacheCreation × missPrice×1.25 + cacheRead × hitPrice + output × outPrice
```

Anthropic 的 `cacheCreation` 按 1.25× input 单价计费（官方文档），但 Anthropic 价格本次不填——只有 DeepSeek 有实测数据验证。其他 provider 价格缺失时 `getModelPricing` 返回 null，UsageBadge 不渲染费用行（优雅降级）。

被否决方案：
- **从后端 API 动态获取**——价格变动不频繁（年级别），MVP 硬编码足够，后续需要时改 API 获取
- **用户自填价格**——增加配置负担，且用户不一定知道准确价格
- **只显示 token 不显示钱**——用户明确要求价格估算，token 数和钱的体感不同

用用户实际数据验证（deepseek-v4-flash）：
```
cacheRead = 488.7k, netNew = 564k - 488.7k = 75.3k, output = 7.1k

cost = 488.7k × 0.02/1M + 75.3k × 1/1M + 7.1k × 2/1M
     = 0.0098 + 0.0753 + 0.0142
     = ¥0.099 ≈ ¥0.10

若无缓存：
noCacheCost = 564k × 1/1M + 7.1k × 2/1M = 0.564 + 0.0142 = ¥0.578
节省 = 0.578 - 0.099 = ¥0.48（83%）
```

这验证了面板现有「省 ~440k 计费」标注——440k tokens × 1元/百万 = ¥0.44，和 ¥0.48 吻合（差异因现有标注只算 input 节省不算 output）。

## Risks / Trade-offs

- **[旧 JSON 记录缺新字段]** → 前端 `?? 0` 兜底 + 条件渲染，单次栏拆解对历史 run 降级为不展示拆解（只显示 ctx 总量），不影响新 run。
- **[turnCount 对 orchestrator 子任务语义]** → 子 agent run 有独立 `turnCount`，rolled up 到 parent 时不累加（与 `subagentTokens` 同理，subagent 的 turn 数不计入 parent 的 turnCount 显示）。仅顶层 run 的 turnCount 显示在顶部。可接受。
- **[popover 高度增加]** → 两栏分区 + 拆解树使内容变长，但已有 `max-h-[70vh] overflow-y-auto`，不会溢出。
- **[「新内容(净)」与「实际 Prompt」行可能重复]** → 「实际 Prompt」行（累加总量）保留但改 hint 为「累计 input + cache + output 总量」，与「新内容(净)」（计费维度）语义区分明确。若用户反馈仍混淆，后续可折叠「实际 Prompt」行。
- **[价格可能过时]** → DeepSeek 官方页面写明「产品价格可能发生变动，DeepSeek 保留修改价格的权利」。价格表注释标注来源 URL 和采集日期，便于后续维护时核对。价格过时不影响 token 统计准确性，只影响费用估算行。
- **[多模型会话的费用估算]** → 取 `byModel` 中 token 用量最大的模型作为主模型查价格。若会话内有多个不同 provider 的模型（如 orchestrator 派发到不同 agent），费用行只估算主模型部分，其余在「按 Model」栏各自显示 token 但不估费。可接受——多数会话单模型。

## Migration Plan

无需迁移。后端字段扩展是附加性的（全部 Optional + default=0），前端对缺失值兜底。部署后新 run 自动携带新字段，旧 run 不受影响。

回滚策略：若前端重构引入 UI regression，可单独 revert `usage-badge.tsx` + `app-store.ts` 改动，后端字段扩展是无害的附加字段，无需回滚。
