# Fix usage badge token display semantics

## Why

右上角 UsageBadge 把「跨 turn 累加值」和「单 turn 快照值」并排显示，且对 DeepSeek 的 `prompt_tokens`（已含 cache hit）语义未修正，导致用户看到「新 Input 564k」与「当前 ctx 79.1k」时产生认知冲突——两个数字差 7 倍，看似矛盾实则度量维度不同（564k 是 7 轮 ReAct 的累加，79.1k 是最后一轮的快照）。同时「新 Input」标注「按 1× 计费」对 DeepSeek 失真：564k 里 488.7k 是缓存复用（按 0.1× 计费），真正按 1× 计费的净新内容仅 ~75k。

## What Changes

- **后端 `RunUsage` 新增 3 个字段**：`lastCacheReadTokens`（最后 turn 的缓存命中）、`lastOutputTokens`（最后 turn 的输出）、`turnCount`（本轮 ReAct 的模型调用次数）。`lastInputTokens` 已有但语义对 DeepSeek 含 cache，单次栏拆解需要配套的 cache/output 快照值。
- **前端 `usage-badge.tsx` 重构为两栏分区**：「累计（跨 N 轮）」+「最近一次调用（第 N 轮）」上下分区，明确区分累加值与快照值，不再并排混淆。
- **累计栏「新 Input」改为「新内容(净)」**：对 DeepSeek（`prompt_tokens` 含 cache）显示 `inputTokens - cacheReadTokens`；对 Anthropic（`input_tokens` 不含 cache）保持 `inputTokens + cacheCreationTokens`。修正后该行对两种 provider 都是「真正按 1× 计费的量」，tooltip「按正常 input 单价 (1×) 计费」终于准确。
- **单次栏「当前 ctx」行带拆解树**：在 ctx 总量下展开「缓存命中」+「新内容」两行，让「79k = 70k 命中 + 9k 新内容」的关系一目了然。
- **顶部标注 turn 数**：从「1 次响应」改为「1 次响应 · 7 轮」，让用户知道累计是跨 turn 的。
- **ctx 行补充 hint**：标注「最近一次 prompt 大小（单轮，非累计）」。
- **新增价格估算**：在 `model-registry.ts` 的 `KNOWN_MODELS` 表中为每个模型添加 `pricing` 字段（每百万 token 的 cacheHit / cacheMiss / output 单价 + 货币单位），UsageBadge 根据会话主模型查价格，在累计栏底部显示「估算费用」行——分别展示 cacheHit 费用、net new 费用、output 费用、总计，以及「若无缓存」对比值和节省金额。当前先填入 DeepSeek V4 系列的官方定价（来源 https://api-docs.deepseek.com/zh-cn/quick_start/pricing ），其他 provider 预留结构但价格待填（价格缺失时不显示费用行）。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `stream-events`: `RunUsageEvent` payload 新增 `lastCacheReadTokens` / `lastOutputTokens` / `turnCount` 三个可选字段，扩展「Usage events SHALL update durable accounting」requirement 以支持单次调用快照拆解。
- `frontend`: `useConversationUsageTotal` 派生新增字段，`UsageBadge` 重构为累计/单次两栏分区并修正 DeepSeek 净新内容语义，新增模型定价表与费用估算行，扩展「Store reducers SHALL apply StreamEvent deterministically」requirement。

## Impact

- **后端**：
  - `backend/app/schemas/messages.py` — `RunUsage` 新增 3 字段
  - `backend/app/adapters/custom_adapter.py` — `_RunUsage` 新增字段 + `_to_run_usage` 填充
  - `backend/app/adapters/claude_adapter.py` — 同步 `last_input_tokens` 旁的 `last_cache_read_tokens` / `last_output_tokens` 赋值
  - `backend/app/adapters/codex_adapter.py` — 同步
  - `backend/app/services/agent_runner.py` — `_RunUsage` 累加点补 `last_cache_read_tokens` / `last_output_tokens` 覆盖赋值 + `turnCount` 从 `term.model_call_count` 取
- **前端**：
  - `src/shared/types.ts` — `RunUsageEvent` 接口新增 3 字段
  - `src/stores/app-store.ts` — `ConversationUsageTotal` 新增 `lastCacheReadTokens` / `lastOutputTokens` / `turnCount`，`useConversationUsageTotal` 派生
  - `src/shared/model-registry.ts` — `KNOWN_MODELS` 新增 `pricing` 字段 + `getModelPricing()` 函数
  - `src/components/usage-badge.tsx` — 两栏分区重构 + DeepSeek 净新内容计算 + ctx 拆解树 + turn 标注 + hint + 费用估算行
- **后端 model_registry.py 同步**：`backend/app/utils/model_registry.py` 的 `KNOWN_MODELS` 同步添加 `pricing` 字段（两端保持一致，后端可用于未来的服务端费用汇总）
- **DB**：`agent_runs.usage` JSON 列自然扩展（JSON 无 schema 约束），旧记录缺新字段时前端兜底为 0，无需迁移。
- **不碰**：StreamEvent 事件类型本身不变（仍是 `run.usage`），只是 payload 字段扩展（新增字段全部可选，向后兼容）；不碰 DB schema、不新增依赖。
