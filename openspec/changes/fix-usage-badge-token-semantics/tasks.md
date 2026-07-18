# Implementation Tasks

## 1. 后端 RunUsage schema 扩展

- [x] 1.1 在 `backend/app/schemas/messages.py` 的 `RunUsage` 模型新增 `last_cache_read_tokens: int = Field(default=0, alias="lastCacheReadTokens")`、`last_output_tokens: int = Field(default=0, alias="lastOutputTokens")`、`turn_count: int = Field(default=0, alias="turnCount")` 三个字段，全部可选带默认值
- [x] 1.2 在 `backend/app/adapters/custom_adapter.py` 的 `_RunUsage` dataclass 新增 `last_cache_read_tokens: int = 0`、`last_output_tokens: int = 0` 字段
- [x] 1.3 在 `backend/app/adapters/custom_adapter.py` 的 `_to_run_usage()` 函数填充新字段（`last_cache_read_tokens=u.last_cache_read_tokens` 等），`turn_count` 暂传 0（custom adapter 的 legacy `stream()` 路径无 turn 计数，ReAct loop 路径由 AgentRunner 接管）

## 2. 后端 AgentRunner 累加点补充

- [x] 2.1 在 `backend/app/services/agent_runner.py` 的 `_RunUsage` dataclass（若存在独立定义）或复用 adapter 的 `_RunUsage`，新增 `last_cache_read_tokens` / `last_output_tokens` 字段
- [x] 2.2 在 `agent_runner.py:1126-1129` 的 `message.usage` 事件累加点，补充 `run_usage.last_cache_read_tokens = event.usage.cache_read_tokens` 和 `run_usage.last_output_tokens = event.usage.output_tokens`（覆盖赋值，与 `last_input_tokens` 同模式）
- [x] 2.3 在 `_emit_run_usage()` 或 `_to_run_usage()` 调用处，传入 `turn_count=term.model_call_count`（ReAct loop 实际模型调用次数）

## 3. 后端 Claude/Codex adapter 同步

- [x] 3.1 在 `backend/app/adapters/claude_adapter.py` 的 run usage 构造处（`:674-701` 附近），同步填充 `last_cache_read_tokens`（取 `run_cache_read`）和 `last_output_tokens`（取 `run_output_tokens`）
- [x] 3.2 在 `backend/app/adapters/codex_adapter.py` 的 run usage 构造处（`:396-422` 附近），同步填充 `last_cache_read_tokens` 和 `last_output_tokens`

## 4. 前端类型定义扩展

- [x] 4.1 在 `src/shared/types.ts` 的 `RunUsageEvent` 接口新增 `lastCacheReadTokens?: number`、`lastOutputTokens?: number`、`turnCount?: number` 三个可选字段
- [x] 4.2 在 `src/stores/app-store.ts` 的 `ConversationUsageTotal` 接口新增 `lastCacheReadTokens: number`、`lastOutputTokens: number`、`turnCount: number` 字段

## 5. 前端 store 派生逻辑

- [x] 5.1 在 `useConversationUsageTotal` 的 `result` 初始值新增 `lastCacheReadTokens: 0`、`lastOutputTokens: 0`、`turnCount: 0`
- [x] 5.2 在 Phase 1（runs map）循环中，取最后 turn 的 run 时同步赋值 `result.lastCacheReadTokens = u.lastCacheReadTokens ?? 0`、`result.lastOutputTokens = u.lastOutputTokens ?? 0`、`result.turnCount = u.turnCount ?? 0`（与 `lastInputTokens` 同一时间戳比较逻辑）
- [x] 5.3 在 Phase 2（messages map 兜底）循环中，`turnCount` 缺失（MessageUsage 无此字段）时保持 0，`lastCacheReadTokens` 取 `u.cacheReadTokens`
- [x] 5.4 确保 `ctxOverride` 覆盖逻辑仍只覆盖 `lastInputTokens`，不影响 `lastCacheReadTokens` / `turnCount`（压缩后无新 cache 数据，保持旧值或 0）

## 6. 前端 UsageBadge UI 重构

- [x] 6.1 将 popover 内容从扁平列表重构为两个分区：「累计（跨 N 轮）」+「最近一次调用（第 N 轮）」，用带标题的 `border-t` 分隔
- [x] 6.2 顶部 header 从 `{runCount} 次响应` 改为 `{runCount} 次响应{turnCount > 0 ? ` · ${turnCount} 轮` : ''}`
- [x] 6.3 累计栏：将「新 Input」行改名为「新内容(净)」，新增 `computeNetInput(provider, inputTokens, cacheReadTokens, cacheCreationTokens)` helper（DeepSeek: input - cacheRead；Anthropic: input + cacheCreation），用计算结果替代 `total.inputTokens` 显示
- [x] 6.4 累计栏：保留「Cache 命中」「Cache 写入」「Output」行不变；「实际 Prompt」行 hint 改为「累计 input + cache 总量」
- [x] 6.5 单次栏：`ContextRow` 组件扩展，在 ctx 总量行下方新增两行缩进子项：`├ 缓存命中 {lastCacheReadTokens} ({pct}%)` 和 `└ 新内容 {netNew}`，用 `computeNetInput` 同源逻辑计算单次净新内容
- [x] 6.6 单次栏：ctx 行 `title` 属性改为「最近一次 prompt 大小（单轮，非累计）/ 模型 contextWindow 上限」
- [x] 6.7 当 `lastCacheReadTokens === 0` 且 `turnCount === 0`（旧记录降级）时，隐藏拆解子树和「· N 轮」标注，只显示 ctx 总量行

## 6b. 模型定价表与费用估算

- [x] 6b.1 在 `src/shared/model-registry.ts` 新增 `ModelPricing` 接口（`currency: 'CNY' | 'USD'`、`inputCacheHit: number`、`inputCacheMiss: number`、`output: number`，均 per 1M tokens）
- [x] 6b.2 在 `KNOWN_MODELS` 表中为 DeepSeek 模型添加 `pricing` 字段：`deepseek-v4-flash` / `deepseek-chat` → `{ currency: 'CNY', inputCacheHit: 0.02, inputCacheMiss: 1, output: 2 }`；`deepseek-v4-pro` / `deepseek-reasoner` → `{ currency: 'CNY', inputCacheHit: 0.025, inputCacheMiss: 3, output: 6 }`（来源 https://api-docs.deepseek.com/zh-cn/quick_start/pricing ，2026年7月采集）
- [x] 6b.3 新增 `getModelPricing(provider, modelId): ModelPricing | null` 函数，查 `KNOWN_MODELS[modelId].pricing`，缺失时返回 null
- [x] 6b.4 在 `backend/app/utils/model_registry.py` 的 `KNOWN_MODELS` 同步添加相同的 `pricing` 字段（两端一致）
- [x] 6b.5 在 `usage-badge.tsx` 新增 `computeCost(pricing, inputTokens, cacheReadTokens, cacheCreationTokens, outputTokens, provider)` helper，返回 `{ actualCost, noCacheCost, savings, currency }`，复用 `computeNetInput` 同源逻辑计算净新内容
- [x] 6b.6 在累计栏底部新增 `CostEstimateRow` 组件：取 `byModel` 中 token 用量最大的模型作为主模型，调 `getModelPricing` 查价格；有价格时显示「估算费用 ¥X.XX」+「无缓存 ¥Y.YY」+「省 ¥Z.ZZ (NN%)」三行；无价格时不渲染
- [x] 6b.7 费用格式化：CNY 用 `¥` 前缀保留 2 位小数，USD 用 `$` 前缀保留 4 位小数（单次会话费用通常 < $0.01）

## 7. 测试与验证

- [x] 7.1 后端：新增 `backend/tests/test_run_usage_fields.py`，验证多 turn ReAct run 的 `RunUsageEvent` 携带 `lastCacheReadTokens` / `lastOutputTokens` / `turnCount` 正确值
- [x] 7.2 后端：验证旧 `agent_runs.usage` JSON（缺新字段）反序列化时三个字段默认为 0，不报错
- [x] 7.3 前端：在 `src/lib/code-intelligence.test.ts` 同级或新建测试，验证 `computeNetInput` 对 DeepSeek 和 Anthropic 两种 provider 的计算正确性
- [x] 7.3b 前端：验证 `computeCost` 对 DeepSeek v4-flash 的计算正确性（用实测数据 input=564k, cacheRead=488.7k, output=7.1k → actualCost≈0.10, noCacheCost≈0.58, savings≈0.48）
- [x] 7.3c 前端：验证 `getModelPricing` 对无价格模型返回 null，`CostEstimateRow` 不渲染
- [x] 7.4 前端：`pnpm typecheck` 通过
- [x] 7.5 前端：`pnpm lint` 通过
- [x] 7.6 后端：`ruff check .` 通过
- [x] 7.7 后端：`pytest` 通过
- [ ] 7.8 手动验证：用 DeepSeek agent 发一条多工具调用的消息，打开 usage badge 确认两栏分区、turn 标注、净新内容计算、ctx 拆解树均正确显示
- [ ] 7.9 手动验证：确认费用估算行显示 ¥0.XX 格式，无缓存对比和节省金额正确，与 DeepSeek 官方定价页面计算一致
