## 1. 后端 Schema — RunUsage + MessageUsage 扩展

- [x] 1.1 在 `backend/app/schemas/messages.py` 的 `RunUsage` 模型新增 `cache_style: str = Field(default='deepseek', alias='cacheStyle')` 字段
- [x] 1.2 在 `backend/app/schemas/messages.py` 的 `MessageUsage` 模型新增 `cache_style: str | None = Field(default=None, alias='cacheStyle')` 可选字段
- [x] 1.3 在 `backend/app/adapters/custom_adapter.py` 的 `_RunUsage` dataclass 新增 `cache_style: str = 'deepseek'` 字段
- [x] 1.4 在 `backend/app/adapters/custom_adapter.py` 的 `_MsgUsage` dataclass 新增 `cache_style: str | None = None` 字段
- [x] 1.5 修改 `_to_run_usage()` 函数签名，新增 `cache_style: str = 'deepseek'` 参数，填充到 `RunUsage(cache_style=cache_style)`
- [x] 1.6 新增 `backend/app/schemas/messages.py` 的 `CacheStyle` Literal 类型别名（`Literal['deepseek', 'anthropic', 'none']`）供后端复用

## 2. 后端 DB — ModelProfile 表加列

- [x] 2.1 在 `backend/app/db/models.py` 的 `ModelProfile` 模型新增 `cache_style: Mapped[str | None]` 和 `detected_cache_style: Mapped[str | None]` 两列（nullable varchar(16)）
- [x] 2.2 在 `backend/app/db/engine.py` 或迁移脚本中新增 `ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS cache_style TEXT` 和 `detected_cache_style TEXT`（幂等）
- [x] 2.3 在 `src/db/schema.ts` 的 `modelProfiles` 表定义新增 `cacheStyle: text('cache_style')` 和 `detectedCacheStyle: text('detected_cache_style')` 两列（nullable）

## 3. 后端 — cacheStyle 解析链

- [x] 3.1 新增 `backend/app/adapters/custom_adapter.py` 中的 `resolve_cache_style(provider, model_profile) -> str` 函数，实现优先级链：已知 provider 硬编码 → ModelProfile.cache_style → ModelProfile.detected_cache_style → 返回 `'deepseek'`（保守默认，等首次探测）
- [x] 3.2 新增 `detect_cache_style_from_usage(usage) -> str | None` 函数：检查 `cache_creation_input_tokens` / `cache_creation_tokens` → `'anthropic'`；`prompt_cache_hit_tokens` / `cached_tokens` → `'deepseek'`；都无 → `'none'`；usage 为 null → `None`
- [x] 3.3 在 `custom_adapter.py` 的 ReAct loop / stream 的 usage 处理点（L486-512 / L861-895 附近），首次收到 usage 时若 `cache_style` 仍是默认值且 provider 是 `openai-compatible`，调 `detect_cache_style_from_usage()` 探测，设置 `_RunUsage.cache_style`，并异步回写 `ModelProfile.detected_cache_style`
- [x] 3.4 在 `_to_run_usage()` 调用处传入解析好的 `cache_style`（或从 `_RunUsage.cache_style` 取）
- [x] 3.5 在 `backend/app/adapters/claude_adapter.py` 的 `RunUsage` 构造处（L839-848 附近），硬编码 `cache_style='anthropic'`
- [x] 3.6 在 `backend/app/adapters/codex_adapter.py` 的 `RunUsage` 构造处，设置 `cache_style`（暂定 `'deepseek'`，待实测确认）
- [x] 3.7 在 `backend/app/services/agent_runner.py` 的 `build_adapter_input` 中，将解析好的 `cache_style` 传递到 `AdapterInput`（新增 `AdapterInput.cache_style: str | None` 字段，供 adapter 读取）

## 4. 后端 — ModelProfile API + 迁移

- [x] 4.1 在 `backend/app/schemas/model_profile.py` 的 Create/Update/Out Pydantic 模型新增 `cacheStyle` 可选字段
- [x] 4.2 在 `backend/app/api/model_profiles.py` 的 create/update handler 中接受并持久化 `cache_style` 字段
- [x] 4.3 在 Out 模型中返回 `cacheStyle` 和 `detectedCacheStyle`（`detectedCacheStyle` 只读，用户不可直接设）
- [x] 4.4 验证：已知 provider 的 ModelProfile 不接受用户设置的 `cacheStyle`（忽略或报 400）
- [x] 4.5 `_migrate_agent_model_profiles` 迁移脚本不设 `cache_style` / `detected_cache_style`（默认 NULL）

## 5. 前端 — 类型定义扩展

- [x] 5.1 在 `src/shared/types.ts` 新增 `export type CacheStyle = 'deepseek' | 'anthropic' | 'none'`
- [x] 5.2 在 `src/shared/types.ts` 的 `RunUsageEvent` 接口新增 `cacheStyle?: CacheStyle`
- [x] 5.3 在 `src/shared/types.ts` 的 `MessageUsageEvent` 接口新增 `cacheStyle?: CacheStyle | null`
- [x] 5.4 在 `src/db/schema.ts` 的 `RunUsage` 接口新增 `cacheStyle?: CacheStyle`
- [x] 5.5 在 `src/db/schema.ts` 的 `MessageUsage` 接口新增 `cacheStyle?: CacheStyle | null`
- [x] 5.6 在 `src/db/schema.ts` 的 `ModelProfileRow` 类型新增 `cacheStyle?: string | null` 和 `detectedCacheStyle?: string | null`
- [x] 5.7 在 `src/shared/types.ts` 的 `ModelProfile` 接口新增 `cacheStyle: CacheStyle | null` 和 `detectedCacheStyle: CacheStyle | null`

## 6. 前端 — usage.ts 函数签名重构

- [x] 6.1 修改 `computeTotalTokens` 签名为 `(cacheStyle: CacheStyle, inputTokens, outputTokens, cacheCreationTokens, cacheReadTokens)`，用 switch 替代 `if (cacheCreationTokens > 0)`
- [x] 6.2 修改 `computeNetInput` 签名为 `(cacheStyle: CacheStyle, inputTokens, cacheReadTokens, cacheCreationTokens)`，用 switch
- [x] 6.3 修改 `computeLastNetInput` 签名为 `(cacheStyle: CacheStyle, lastInputTokens, lastCacheReadTokens)`，用 switch
- [x] 6.4 修改 `computeCost` 签名为 `(cacheStyle: CacheStyle, pricing, inputTokens, cacheReadTokens, cacheCreationTokens, outputTokens)`，内部调 `computeNetInput(cacheStyle, ...)`
- [x] 6.5 修改 `computeMessageTotalTokens` 签名为 `(cacheStyle: CacheStyle, inputTokens, outputTokens, cacheReadTokens)`，用 switch 替代 `modelProvider` 判断
- [x] 6.6 新增 `inferCacheStyle(cacheCreationTokens: number): CacheStyle` — `> 0 ? 'anthropic' : 'deepseek'`（向后兼容旧数据）
- [x] 6.7 新增 `computeWeightedCacheHitRate(byCacheStyle: Record<CacheStyle, { inputTokens: number; cacheReadTokens: number; cacheCreationTokens: number }>): number` — 逐 style 用各自正确分母公式算命中率后加权平均

## 7. 前端 — ConversationUsageTotal + store 累积重构

- [x] 7.1 在 `src/stores/app-store.ts` 的 `ConversationUsageTotal` 接口新增 `netInput: number`、`byCacheStyle: Record<CacheStyle, { inputTokens: number; cacheReadTokens: number; cacheCreationTokens: number; outputTokens: number }>`、`lastCacheStyle: CacheStyle`
- [x] 7.2 在 `useConversationUsageTotal` 的 Phase 1（runs 循环）中，逐 run 用 `u.cacheStyle ?? inferCacheStyle(u.cacheCreationTokens)` 解析 style，调 `computeTotalTokens(style, ...)` 和 `computeNetInput(style, ...)` 后累加到 `result.totalTokens` 和 `result.netInput`
- [x] 7.3 在 Phase 1 循环中，按 style 分桶累加到 `result.byCacheStyle[style]`
- [x] 7.4 在 Phase 1 的 lastInputTs 更新点，同步设置 `result.lastCacheStyle = style`
- [x] 7.5 在 Phase 2（messages 循环）中，从关联 run 的 cacheStyle 查（`runs` map 里查 runId），查不到 fallback `inferCacheStyle(u.cacheReadTokens > 0 ? 1 : 0)`；调 `computeMessageTotalTokens(style, ...)` 和 `computeNetInput(style, ...)` 后累加
- [x] 7.6 初始化 `result.byCacheStyle` 的三个桶为全零

## 8. 前端 — UsageBadge UI 重构

- [x] 8.1 将 `computeCacheHitRate` 函数从 `usage-badge.tsx` 移到 `usage.ts`，改签名为 `(cacheStyle: CacheStyle, inputTokens, cacheCreationTokens, cacheReadTokens)`；新增 `computeWeightedCacheHitRate` 用于跨 style 加权
- [x] 8.2 在 `UsageBadge` 中，将 `const netInput = computeNetInput(total.inputTokens, ...)` 替换为 `const netInput = total.netInput`
- [x] 8.3 在 `UsageBadge` 中，将 `cacheHitRate = computeCacheHitRate(total.inputTokens, ...)` 替换为 `cacheHitRate = computeWeightedCacheHitRate(total.byCacheStyle)`
- [x] 8.4 在 `UsageBadge` 的「实际 Prompt」行，将 `total.cacheCreationTokens > 0 ? ... : total.inputTokens` 替换为直接用 `total.totalTokens`（逐 run 正确累加）
- [x] 8.5 在 `UsageBadge` 中，将 `lastNetNew = computeLastNetInput(total.lastInputTokens, total.lastCacheReadTokens, total.cacheCreationTokens)` 替换为 `computeLastNetInput(total.lastCacheStyle, total.lastInputTokens, total.lastCacheReadTokens)`
- [x] 8.6 重构 `CostEstimateRow`：按 `byCacheStyle` 分桶，每桶取该桶用量最大的 modelId 查 `getModelPricing`，各自调 `computeCost(style, pricing, ...)` 后求和；货币取主桶（用量最大的桶）的货币
- [x] 8.7 `AgentUsageCard` 组件中的 `computeCacheHitRate` 调用也改为传入 `cacheStyle` 参数（从 `AgentUsageDetail` 取或 `inferCacheStyle`）

## 9. 前端 — AgentUsageDetail 扩展

- [x] 9.1 在 `src/stores/app-store.ts` 的 `AgentUsageDetail` 接口新增 `cacheStyle: CacheStyle` 字段（记录该 agent 最近一次使用的 cache style）
- [x] 9.2 在 Phase 1 累积 `byAgentDetail` 时，设置 `d.cacheStyle = style`（每次更新）

## 10. 前端 — ModelProfile UI

- [x] 10.1 在 ModelProfile 创建/编辑表单中，当 `provider === 'openai-compatible'` 时展示 cacheStyle 选择器（radio: 自动探测 / deepseek-style / anthropic-style / none）
- [x] 10.2 选择器旁显示 `detectedCacheStyle` 提示（如"上次自动检测: deepseek"），仅当 `detectedCacheStyle` 非 null 时展示
- [x] 10.3 非 `openai-compatible` provider 时隐藏选择器
- [x] 10.4 `CreateModelProfileBody` / `UpdateModelProfileBody` 类型新增 `cacheStyle?: CacheStyle | null`

## 11. 测试

- [x] 11.1 后端：新增 `backend/tests/test_cache_style.py`，测试 `resolve_cache_style()` 优先级链（已知 provider 硬编码 → 用户声明 → 探测结果 → 默认）
- [x] 11.2 后端：测试 `detect_cache_style_from_usage()` 对各种 usage 字段组合的探测结果
- [x] 11.3 后端：测试 `_to_run_usage()` 正确填充 `cache_style` 字段
- [x] 11.4 后端：测试旧 `agent_runs.usage` JSON（无 `cacheStyle`）反序列化时 `cache_style` 默认为 `'deepseek'`
- [x] 11.5 前端：测试 `computeTotalTokens` / `computeNetInput` 对三种 cacheStyle 的计算正确性
- [x] 11.6 前端：测试 `inferCacheStyle()` 向后兼容（`cacheCreationTokens > 0` → `'anthropic'`，否则 `'deepseek'`）
- [x] 11.7 前端：测试 `computeWeightedCacheHitRate` 对混合 style 的加权计算正确性
- [x] 11.8 前端：测试 `useConversationUsageTotal` 对混合 provider 会话的累积结果（netInput / byCacheStyle / lastCacheStyle 均正确）

## 12. 验证

- [x] 12.1 前端 `pnpm typecheck` 通过
- [x] 12.2 前端 `pnpm lint` 通过（仅 pre-existing 警告）
- [x] 12.3 后端 `ruff check .` 通过（仅 pre-existing 警告）
- [x] 12.4 后端 `pytest` 通过（25/25 cache_style tests）
- [ ] 12.5 手动验证：用 DeepSeek profile 发消息 → 切到 Anthropic profile 发消息 → 打开 UsageBadge 确认 netInput / cacheHitRate / 费用均正确（不出现虚高）
- [ ] 12.6 手动验证：创建 `openai-compatible` ModelProfile（如 longcat），首次发消息后检查 `detected_cache_style` 被回写
- [ ] 12.7 手动验证：ModelProfile 编辑 UI 在 `openai-compatible` 时展示 cacheStyle 选择器，已知 provider 时隐藏
