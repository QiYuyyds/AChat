# Frontend

## MODIFIED Requirements

### Requirement: Store reducers SHALL apply StreamEvent deterministically

Zustand reducers MUST update conversation, message, artifact, pending write, pending bash command, dispatch, and usage state from `StreamEvent` payloads.

The `useConversationUsageTotal` selector SHALL derive cumulative totals, pre-computed provider-aware values, per-style breakdowns, and last-turn snapshots from `run.usage` and `message.usage`:

- **Cumulative raw values** (for display): `inputTokens`, `outputTokens`, `cacheCreationTokens`, `cacheReadTokens` — accumulated across all runs regardless of cacheStyle.
- **Pre-computed values** (provider-aware, per-run then accumulated): `totalTokens` (sum of per-run `computeTotalTokens(cacheStyle, ...)`), `netInput` (sum of per-run `computeNetInput(cacheStyle, ...)`) — the frontend MUST NOT re-derive these from global accumulated totals using a single inference signal.
- **Per-style breakdown**: `byCacheStyle` — a record keyed by cacheStyle (`'deepseek'` / `'anthropic'` / `'none'`), each containing `{ inputTokens, cacheReadTokens, cacheCreationTokens, outputTokens }`. Used for weighted cache-hit rate and per-bucket cost estimation.
- **Last-turn snapshot**: `lastInputTokens`, `lastCacheReadTokens`, `lastOutputTokens`, `turnCount`, `lastCacheStyle` — from the most recent run with usage (by `startedAt` timestamp), subject to the existing `ctxOverride` post-compaction override logic.

Missing snapshot fields (from older runs) MUST default to 0. `turnCount` of 0 MUST cause the UI to omit the "· N 轮" label. Missing `cacheStyle` on older `RunUsage` payloads MUST be inferred via `cacheCreationTokens > 0 ? 'anthropic' : 'deepseek'` (legacy inference, matching pre-change behavior).

The `usage.ts` compute helpers SHALL all take an explicit `cacheStyle` parameter as their first argument:
- `computeTotalTokens(cacheStyle, inputTokens, outputTokens, cacheCreationTokens, cacheReadTokens)` — returns per-run total, using the run's own style.
- `computeNetInput(cacheStyle, inputTokens, cacheReadTokens, cacheCreationTokens)` — returns per-run net-new content.
- `computeLastNetInput(cacheStyle, lastInputTokens, lastCacheReadTokens)` — returns single-turn net-new content.
- `computeCost(cacheStyle, pricing, inputTokens, cacheReadTokens, cacheCreationTokens, outputTokens)` — returns per-run cost estimate.
- `computeWeightedCacheHitRate(byCacheStyle)` — new function that computes a weighted average cache-hit rate across all styles, using each style's correct denominator formula.
- `inferCacheStyle(cacheCreationTokens)` — legacy inference for backward compatibility with older payloads.

The `UsageBadge` component SHALL use `total.netInput` (pre-computed) instead of calling `computeNetInput(total.inputTokens, total.cacheReadTokens, total.cacheCreationTokens)` with a global signal. The cache-hit rate SHALL use `computeWeightedCacheHitRate(total.byCacheStyle)`. The single-turn decomposition SHALL use `total.lastCacheStyle` instead of `total.cacheCreationTokens` as the style signal. The "实际 Prompt" row SHALL use `total.totalTokens` (already per-run correct). The cost estimate SHALL sum per-bucket costs from `byCacheStyle`, with each bucket using its own primary model's pricing.

#### Scenario: `part.delta` arrives

- **WHEN** the event references an existing part
- **THEN** the store appends content to that part without reordering other parts.

#### Scenario: A failed run leaves an open tool call

- **WHEN** `run.end` arrives with `status='failed'` or `status='aborted'`
- **THEN** the store marks streaming messages from that run as terminal
- **AND** appends local error `tool_result` parts for any unmatched `tool_use` call ids.

#### Scenario: Mixed-provider conversation accumulates correctly

- **WHEN** a conversation has Run 1 (DeepSeek, cacheStyle='deepseek', input=100k, cacheRead=80k) and Run 2 (Anthropic, cacheStyle='anthropic', input=50k, cacheCreation=10k, cacheRead=5k)
- **THEN** `total.netInput` = (100k - 80k) + (50k + 10k) = 80k (per-run correct)
- **AND** `total.totalTokens` = 120k + 75k = 195k (per-run correct)
- **AND** `total.byCacheStyle.deepseek.inputTokens` = 100k
- **AND** `total.byCacheStyle.anthropic.inputTokens` = 50k
- **AND** the cache-hit rate is computed weighted across both styles, not from a single global signal

#### Scenario: Single-turn decomposition uses lastCacheStyle

- **WHEN** the most recent run is DeepSeek (cacheStyle='deepseek') but an earlier run was Anthropic
- **THEN** `total.lastCacheStyle` = 'deepseek'
- **AND** `lastNetNew` = `computeNetInput('deepseek', lastInputTokens, lastCacheReadTokens, 0)` = `lastInputTokens - lastCacheReadTokens`
- **AND** the result is correct even though `total.cacheCreationTokens > 0` from the earlier Anthropic run

#### Scenario: Legacy run without cacheStyle

- **WHEN** the frontend processes a `run.usage` payload lacking `cacheStyle`
- **THEN** `inferCacheStyle(cacheCreationTokens)` returns 'anthropic' if > 0, else 'deepseek'
- **AND** all downstream calculations use this inferred style
- **AND** the behavior is identical to the pre-change inference logic
