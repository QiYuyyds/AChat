# Add cache style declaration for cross-provider token accounting

## Why

UsageBadge's token calculations use a single binary signal (`cacheCreationTokens > 0`) to guess whether `inputTokens` includes cache-read tokens (DeepSeek/OpenAI style) or excludes them (Anthropic style). This assumption holds only when a conversation uses one provider. Now that ModelProfile allows per-message model switching, a conversation can mix providers — and the binary signal becomes meaningless on accumulated totals, producing wrong "新内容(净)", wrong cache-hit rate, wrong "实际 Prompt", and wrong cost estimates. The problem is worse for `openai-compatible` models (longcat, MiniMax, Qwen, etc.) whose cache semantics are unknown and cannot be inferred from `cacheCreationTokens`.

## What Changes

- **New `cacheStyle` field on `RunUsage`**: Every `RunUsage` payload SHALL carry a `cacheStyle: 'deepseek' | 'anthropic' | 'none'` field, set explicitly by the adapter. This replaces the `cacheCreationTokens > 0` inference signal.
- **New `cacheStyle` field on `MessageUsage`** (optional): Per-message usage MAY carry `cacheStyle`; when absent, the frontend infers from the associated run or falls back to `'deepseek'`.
- **New `cacheStyle` + `detectedCacheStyle` columns on `model_profiles` table** (**BREAKING**: new DB columns, nullable, no backfill). Allows users to declare cache semantics for `openai-compatible` models; `detectedCacheStyle` stores auto-detection results for reuse.
- **Backend cacheStyle resolution chain**: Adapters resolve `cacheStyle` per-run via a priority chain: known provider hardcode → user-declared `ModelProfile.cacheStyle` → `ModelProfile.detectedCacheStyle` → auto-detect from LLM response fields → conservative default `'deepseek'`.
- **Frontend `useConversationUsageTotal` refactor**: Accumulate `netInput` per-run (using each run's own `cacheStyle`) instead of computing it in the UI from global accumulated totals. Add `byCacheStyle` breakdown for weighted cache-hit rate and per-bucket cost estimation.
- **Frontend `usage.ts` refactor**: All `compute*` functions take an explicit `cacheStyle` parameter instead of inferring from `cacheCreationTokens > 0`.
- **Frontend `UsageBadge` refactor**: Use `total.netInput` (pre-computed) and `computeWeightedCacheHitRate(byCacheStyle)` instead of calling `computeNetInput(total.inputTokens, ...)` with a global signal. Single-turn decomposition uses `lastCacheStyle`.
- **ModelProfile UI**: When provider is `openai-compatible`, show a cache-style selector (auto / deepseek-style / anthropic-style / none). Hidden for known providers.

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `stream-events`: `RunUsageEvent` payload 新增 `cacheStyle` 字段；`MessageUsageEvent` 新增可选 `cacheStyle`。扩展「Usage events SHALL update durable accounting」requirement 以声明 cache 语义，不再从数据反推。
- `frontend`: `ConversationUsageTotal` 新增 `netInput` / `byCacheStyle` / `lastCacheStyle`；`useConversationUsageTotal` 逐 run 按各自 `cacheStyle` 计算 `netInput` 并累积；`UsageBadge` 不再用全局 `cacheCreationTokens > 0` 信号反推，改用预计算的 `total.netInput` 和分桶加权。`usage.ts` 所有 `compute*` 函数改为显式 `cacheStyle` 参数。扩展「Store reducers SHALL apply StreamEvent deterministically」requirement。
- `model-profiles`: ModelProfile 新增 `cacheStyle`（用户声明，nullable）和 `detectedCacheStyle`（自动探测回写，nullable）字段。仅对 `openai-compatible` provider 有意义；已知 provider 由 adapter 硬编码。扩展「ModelProfile SHALL be a user-scoped reusable model configuration」requirement。
- `persistence`: `model_profiles` 表新增 `cache_style` 和 `detected_cache_style` 两列（nullable varchar(16)，无默认值，不回填）。`agent_runs.usage` JSON 列自然扩展（`cacheStyle` 由 adapter 写入）。扩展「model_profiles table SHALL store per-user model configurations」requirement。
- `adapters`: CustomAdapter 新增 `cacheStyle` 解析链（provider 硬编码 → profile 声明 → 探测 → 默认）；ClaudeAdapter 硬编码 `'anthropic'`；CodexAdapter 根据 CLI 返回格式确定。`_to_run_usage()` / `_RunUsage` / `RunUsage` 构造处填充 `cacheStyle`。扩展「Adapters SHALL translate provider output to StreamEvent」requirement。

## Impact

- **后端**:
  - `backend/app/schemas/messages.py` — `RunUsage` 新增 `cache_style` 字段；`MessageUsage` 新增可选 `cache_style`
  - `backend/app/db/models.py` — `ModelProfile` 新增 `cache_style` / `detected_cache_style` 列
  - `backend/app/adapters/custom_adapter.py` — `_RunUsage` / `_to_run_usage()` 填充 `cacheStyle`；新增 cacheStyle 解析函数
  - `backend/app/adapters/claude_adapter.py` — `RunUsage` 构造处硬编码 `cache_style='anthropic'`
  - `backend/app/adapters/codex_adapter.py` — `RunUsage` 构造处填充 `cacheStyle`
  - `backend/app/services/agent_runner.py` — `build_adapter_input` 传递 resolved `cacheStyle` 到 AdapterInput
  - `backend/app/api/model_profiles.py` — CRUD 支持 `cacheStyle` 字段
- **前端**:
  - `src/shared/types.ts` — `RunUsageEvent` / `MessageUsageEvent` 新增 `cacheStyle`
  - `src/shared/usage.ts` — 所有 `compute*` 函数改为 `cacheStyle` 参数；新增 `computeWeightedCacheHitRate`
  - `src/shared/model-registry.ts` — `ModelPricing` 和 `getModelPricing` 不变（定价仍按 modelId 查）
  - `src/stores/app-store.ts` — `ConversationUsageTotal` 新增字段；`useConversationUsageTotal` 累积逻辑重构
  - `src/components/usage-badge.tsx` — 用 `total.netInput` / `byCacheStyle` / `lastCacheStyle` 替代全局信号
  - ModelProfile 编辑 UI — `openai-compatible` 时展示 cacheStyle 选择器
- **DB 迁移**: `model_profiles` 表 ALTER ADD 两列（nullable），SQLite 和 PostgreSQL 都支持
- **向后兼容**: 旧 `RunUsage` 无 `cacheStyle` → 前端 `?? inferCacheStyle(cacheCreationTokens)` 兜底，行为与当前一致
