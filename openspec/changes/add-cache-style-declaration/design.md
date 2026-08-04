# Design — add-cache-style-declaration

## Context

### Current state

UsageBadge 的 token 计算（`usage.ts` 的 `computeTotalTokens` / `computeNetInput` / `computeCacheHitRate` / `computeCost`）使用一个二元信号 `cacheCreationTokens > 0` 来区分两种 provider 的 cache 语义：

- **DeepSeek 风格**（`cacheCreationTokens == 0`）：`prompt_tokens` 已含 `cached_tokens`，total = input + output，netInput = input - cacheRead
- **Anthropic 风格**（`cacheCreationTokens > 0`）：`input_tokens` 不含 cache，total = input + output + cacheCreation + cacheRead，netInput = input + cacheCreation

这个信号在**单 provider 会话**里完全正确。所有历史测试都用 DeepSeek，从未暴露问题。

### What broke

ModelProfile 上线后，会话支持每条消息切换模型。当一个会话混了 DeepSeek 和 Anthropic（或任何不同 cache 语义的 provider）时：

1. `useConversationUsageTotal` 把不同 provider 的 `inputTokens` / `cacheReadTokens` / `cacheCreationTokens` 原样累加
2. UI 层用 `total.cacheCreationTokens > 0` 这个全局累积信号判断 provider 风格——但累积值混了多个 provider，信号失效
3. `computeNetInput(total...)` 对 DeepSeek 的 cacheRead 不扣（走了 Anthropic 公式），虚高
4. `computeCacheHitRate(total...)` 分母混了两种语义，偏差巨大
5. `computeLastNetInput` 用 `total.cacheCreationTokens`（全局累积值）判断最近一次 run 的风格，如果最近是 DeepSeek 但之前有 Anthropic，就走错公式

### The deeper problem: openai-compatible

`openai-compatible` provider 是个逃逸舱——它的 cache 语义不可知。用户可能接入 longcat、MiniMax、Qwen、Moonshot 等，这些模型的 cache 行为异构：

- 有的复用 DeepSeek 的 `prompt_cache_hit_tokens` 字段（input 含 cache）
- 有的复用 OpenAI 的 `cached_tokens` 字段（input 含 cache）
- 有的有 Anthropic 风格的 `cache_creation_input_tokens`
- 有的有自己的字段名，adapter 抓不到
- 有的根本不支持缓存

二元信号 `cacheCreationTokens > 0` 对这些模型无法正确分类。

### Key constraint

后端 adapter 在 run 时**知道**自己处理的是什么 provider——这信息在 `build_adapter_input` 解析 ModelProfile 时就确定了。问题是这个信息没有传递到 `RunUsage` payload 和前端。

## Goals / Non-Goals

**Goals:**

- `RunUsage` 显式携带 `cacheStyle` 字段，adapter 在构造时设置，前端逐 run 按各自 style 计算
- `openai-compatible` 的 ModelProfile 支持用户声明 cacheStyle，并有自动探测机制回写 `detectedCacheStyle`
- 前端累积逻辑改为逐 run 计算后累积（netInput / totalTokens / cacheHitRate），不再用全局信号反推
- 向后兼容：旧 `RunUsage`（无 `cacheStyle`）前端 fallback 到当前行为（`cacheCreationTokens > 0` 推断）

**Non-Goals:**

- 不改 `usage_summary_service.py` 的全局聚合逻辑（那个服务按 `input + output + cacheRead + cacheCreation` 原样累加，是独立问题）
- 不改 `MessageUsage` 的 DB schema（`MessageUsage` 的 `cacheStyle` 是 JSON 列自然扩展，不新增 DB 列）
- 不追踪 mid-run compact 后的"本轮峰值 ctx"（`lastInputTokens` 仍是最后一个 turn）
- 不做跨会话的 cacheStyle 记忆（每次新会话首 run 重新探测或读 ModelProfile）
- 不改 CLI adapter 的模型选择机制（CLI agent 不参与，Claude adapter 硬编码 `'anthropic'`）

## Decisions

### D1: cacheStyle 三态：`'deepseek'` / `'anthropic'` / `'none'`

**选择**：三种 cacheStyle。

| Style | input 含 cacheRead? | 有 cacheCreation? | netInput | total |
|---|---|---|---|---|
| `'deepseek'` | 是 | 否（恒 0） | input - cacheRead | input + output |
| `'anthropic'` | 否 | 是 | input + cacheCreation | input + output + cacheCreation + cacheRead |
| `'none'` | N/A | N/A | input | input + output |

**理由**：`'none'` 的计算结果和 `'deepseek'` + cacheRead=0 相同，但从 UX 角度 `'none'` 可让 UI 不显示"缓存命中率 0%"迷惑信息，且语义清晰（明确声明"不支持缓存"而非"支持缓存但恰好没命中"）。

**替代方案**：只保留 `'deepseek'` / `'anthropic'` 两态，无缓存归入 `'deepseek'`。否决：对 longcat 这类明确不支持缓存的模型，显示 0% 命中率有误导性。

### D2: cacheStyle 放在 `RunUsage`，不在 `MessageUsage` 强制

**选择**：`RunUsage.cacheStyle` 是必填字段（adapter 一定知道）。`MessageUsage.cacheStyle` 是可选字段（adapter 在 emit `message.usage` 时可带可不带）。前端消费 `MessageUsage` 时，如果 `cacheStyle` 为 null，从关联 run 的 `cacheStyle` 查（runs map 里查 runId），查不到 fallback `'deepseek'`。

**理由**：`RunUsage` 是 token 语义的单一数据源，每个 run 一定有确定的 cacheStyle。`MessageUsage` 是单条消息级别的快照，让它也带 cacheStyle 是锦上添花（Phase 2 从 DB 加载时能用），但不是必须的——Phase 2 可从 run 关联推断。

**替代方案**：`MessageUsage` 也强制带 cacheStyle。否决：增加 adapter 的负担，且 `MessageUsage` 已存的旧 JSON 没有 `cacheStyle`，向后兼容需要 fallback 逻辑——不如统一走"从 run 推断"的 fallback。

### D3: cacheStyle 解析优先级链（adapter 侧）

**选择**：adapter 在 run 开始时确定 `cacheStyle`，走以下优先级：

1. **已知 provider 硬编码**：`anthropic` → `'anthropic'`；`deepseek` / `openai` / `volcano-ark` → `'deepseek'`。结束。
2. **`openai-compatible` → 用户声明**：检查 `ModelProfile.cache_style`（非 null → 返回用户声明）。结束。
3. **`openai-compatible` → 自动探测结果**：检查 `ModelProfile.detected_cache_style`（非 null → 返回探测结果）。结束。
4. **`openai-compatible` + 首次（auto）→ 从 LLM 响应字段探测**：
   - 响应有 `cache_creation_input_tokens` 或 `cache_creation_tokens` → `'anthropic'`
   - 响应有 `prompt_cache_hit_tokens` 或 `cached_tokens` → `'deepseek'`
   - 都没有 → `'none'`
   - 探测后回写 `ModelProfile.detected_cache_style`（持久化，下次直接用）
5. **无法探测（首次运行就无 usage）→ `'deepseek'`**（保守默认）

**理由**：已知 provider 不需要用户干预。`openai-compatible` 的优先级链让"首次自动探测 → 后续复用"成为无感体验，同时允许用户手动声明覆盖。

**替代方案**：不做自动探测，纯靠用户声明。否决：大部分用户不知道自己接的模型是什么 cache 风格，自动探测是必须的。

### D4: 前端累积改为逐 run 计算

**选择**：在 `useConversationUsageTotal` 的 Phase 1 循环中，对每个 run 用其自己的 `cacheStyle` 调 `computeTotalTokens(style, ...)` 和 `computeNetInput(style, ...)`，然后累加。新增 `result.netInput` 字段（逐 run 算好后累积）。新增 `result.byCacheStyle` 分桶（用于加权命中率和分桶费用估算）。

**理由**：这是根本性的修复——让每个 run 的 token 语义由该 run 自己的 `cacheStyle` 决定，不再用全局累积信号反推。

**替代方案**：在 UI 层用 `byCacheStyle` 分桶反推。否决：累积逻辑在 store selector 里做一次，比每次 render 都在 UI 层重算高效。

### D5: `usage.ts` 所有函数改为显式 `cacheStyle` 参数

**选择**：`computeTotalTokens(cacheStyle, ...)` / `computeNetInput(cacheStyle, ...)` / `computeCacheHitRate(cacheStyle, ...)` / `computeCost(cacheStyle, ...)` 全部第一个参数是 `cacheStyle`。新增 `inferCacheStyle(cacheCreationTokens)` 做向后兼容。

**理由**：消除"从数据反推 provider"的反模式。函数签名直接声明"我需要知道 cache 风格"，调用方必须提供。

### D6: 费用估算分桶

**选择**：`CostEstimateRow` 按 `byCacheStyle` 分桶各自查定价后求和。每桶取该桶内用量最大的 modelId 查 `getModelPricing`。货币单位取主桶（用量最大的桶）的货币。某桶无定价 → 该桶不显示费用但不影响其他桶。

**理由**：跨 provider 时定价不同（DeepSeek 用 CNY，OpenAI 用 USD），不能取一个模型的全局定价套全部。

### D7: ModelProfile UI — `openai-compatible` 时展示 cacheStyle 选择器

**选择**：创建/编辑 ModelProfile 时，如果 provider 是 `openai-compatible`，展开一个 cacheStyle 单选区域（自动探测 / deepseek-style / anthropic-style / none）。其他 provider 不展示（由 adapter 硬编码）。选择器旁边显示 `detectedCacheStyle`（如有）作为提示。

## Risks / Trade-offs

- **[风险] 旧 `RunUsage` 无 `cacheStyle` → 前端 `inferCacheStyle` fallback**：`inferCacheStyle(cacheCreationTokens > 0 ? 'anthropic' : 'deepseek')` 与当前行为完全一致，向后兼容。但混了旧数据和新数据的会话，旧 run 的 cacheStyle 可能猜错（只是延续当前的问题，不恶化）。

- **[风险] 自动探测回写竞态**：如果同一 ModelProfile 并发两个 run 同时首次探测，可能同时回写 `detected_cache_style`。→ 使用 UPDATE ... SET 语句幂等，探测结果应该一致（同一模型同一响应格式）。最差情况是两次探测结果不同（概率极低），取最后一次写入的值。

- **[风险] `openai-compatible` 的 cache 字段名不在 adapter 覆盖范围内**：longcat 可能有自定义字段名。→ adapter 已覆盖 `prompt_cache_hit_tokens` / `cached_tokens` / `cache_creation_input_tokens` / `cache_creation_tokens` 四种。用户可通过 ModelProfile 手动声明 cacheStyle 来覆盖探测结果。

- **[代价] `RunUsage` JSON 体积略增**：新增一个 `cacheStyle` 字符串字段，每个 run JSON 增加约 20 bytes。可忽略。

## Migration Plan

1. **DB 迁移**：`ALTER TABLE model_profiles ADD COLUMN cache_style TEXT NULL` + `ADD COLUMN detected_cache_style TEXT NULL`。两列 nullable，不回填。SQLite 和 PostgreSQL 都支持 `ALTER ADD COLUMN ... NULL`。
2. **后端**：`RunUsage` / `MessageUsage` Pydantic 模型加 `cache_style` 字段（default `'deepseek'` / `None`）。Adapter 在 `_to_run_usage()` / `_RunUsage` / RunUsage 构造处填充。旧 `agent_runs.usage` JSON 无 `cacheStyle` → Pydantic default `'deepseek'` 兜底（但前端会再 `inferCacheStyle` 做更准的 fallback）。
3. **前端**：`usage.ts` 所有函数改签名。`useConversationUsageTotal` 累积逻辑重构。`UsageBadge` 用 `total.netInput` / `byCacheStyle` / `lastCacheStyle`。旧 `RunUsageEvent` 无 `cacheStyle` → `inferCacheStyle()` fallback。
4. **ModelProfile UI**：`openai-compatible` 时展示选择器。CRUD API 支持 `cacheStyle` 字段。
5. **无需回滚**：所有新字段可选/default，删除 change 不影响已有数据（只是 `cacheStyle` 列没人读）。

## Open Questions

- **Codex adapter 的 cacheStyle**：Codex 走 OpenAI Responses API，usage 格式可能与 Chat Completions 不同。需确认 Codex 返回的 usage 是否有 cache 字段，以及是 DeepSeek-style 还是其他。暂定 `'deepseek'`，待实测确认。
- **`volcano-ark` 的 cache 语义**：豆包 API 是否有 cache 字段？当前 adapter 统一走 `prompt_cache_hit_tokens` / `cached_tokens` 探测，如果豆包有不同字段名，需要补充。暂定 `'deepseek'`。
