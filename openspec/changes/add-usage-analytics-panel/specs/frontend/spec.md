# Frontend

## MODIFIED Requirements

### Requirement: Store reducers SHALL apply StreamEvent deterministically

Zustand reducers MUST update conversation, message, artifact, pending write, pending bash command, dispatch, and usage state from `StreamEvent` payloads.

The `useConversationUsageTotal` selector SHALL derive both cumulative totals (for the "session累计" panel section) and last-turn snapshots (for the "最近一次调用" panel section) from `run.usage` and `message.usage`:

- **Cumulative**: `inputTokens`, `outputTokens`, `cacheCreationTokens`, `cacheReadTokens`, `totalTokens`, `byAgent`, `byModel`, `byAgentDetail`, `runCount` — accumulated across all runs in the conversation.
- **Last-turn snapshot**: `lastInputTokens`, `lastCacheReadTokens`, `lastOutputTokens`, `turnCount` — from the most recent run with usage (by `startedAt` timestamp), subject to the existing `ctxOverride` post-compaction override logic.

Missing snapshot fields (from older runs) MUST default to 0. `turnCount` of 0 MUST cause the UI to omit the "· N 轮" label rather than display "· 0 轮".

The `UsageBadge` component SHALL render two visually distinct sections rather than a flat list:

1. **「累计（跨 N 轮）」section**: displays cumulative billing-relevant values. The row formerly labeled "新 Input" SHALL be relabeled "新内容(净)" and MUST display net-new input tokens computed provider-aware:
   - DeepSeek-style (`cacheCreationTokens == 0`): `inputTokens - cacheReadTokens`
   - Anthropic-style (`cacheCreationTokens > 0`): `inputTokens + cacheCreationTokens`
   This ensures the row always represents "tokens billed at 1× input rate".
2. **「最近一次调用（第 N 轮）」section**: displays the context window row with decomposition. The "当前 ctx" row MUST show `lastInputTokens / contextWindow (pct%)` with progress bar, and SHALL expand a sub-tree showing `lastCacheReadTokens` (cache-reused) and net-new content (`lastInputTokens - lastCacheReadTokens` for DeepSeek; `lastInputTokens + lastCacheCreationTokens - lastCacheReadTokens` for Anthropic, but since `lastCacheCreationTokens` is not exposed at run level, use the cumulative provider signal).

The "累计" section SHALL include a cost estimate row at the bottom when model pricing is available. The `model-registry.ts` `KNOWN_MODELS` table SHALL carry an optional `pricing` field per model with `currency`, `inputCacheHit`, `inputCacheMiss`, and `output` rates (all per 1M tokens). A `getModelPricing(provider, modelId)` function SHALL return `ModelPricing | null`. When pricing is available for the conversation's primary model (the model with the highest cumulative token count in `byModel`), the cost estimate row MUST display:
- Actual cost: `cacheRead × hitPrice + netNew × missPrice + output × outPrice` (provider-aware, same net-new formula as the "新内容(净)" row)
- "Without cache" comparison: `(cacheRead + netNew) × missPrice + output × outPrice`
- Savings amount and percentage
- Currency symbol (¥ for CNY, $ for USD)

When pricing is not available (`getModelPricing` returns null), the cost estimate row MUST be omitted entirely (graceful degradation).

The top header SHALL display `runCount` as "N 次响应" and, when `turnCount > 0`, append "· M 轮" where M is the last run's `turnCount`.

#### Scenario: `part.delta` arrives
- **WHEN** the event references an existing part
- **THEN** the store appends content to that part without reordering other parts.

#### Scenario: A failed run leaves an open tool call
- **WHEN** `run.end` arrives with `status='failed'` or `status='aborted'`
- **THEN** the store marks streaming messages from that run as terminal
- **AND** appends local error `tool_result` parts for any unmatched `tool_use` call ids.

#### Scenario: Multi-turn run displays two sections
- **WHEN** a run with `turnCount=7` completes and the user opens the usage badge
- **THEN** the header shows "1 次响应 · 7 轮"
- **AND** the "累计" section shows cumulative values with "新内容(净)" = net new input
- **AND** the "最近一次调用" section shows "当前 ctx" with cache-hit and net-new decomposition.

#### Scenario: DeepSeek net-new input excludes cache hit
- **WHEN** the provider is DeepSeek (`cacheCreationTokens == 0`) and cumulative `inputTokens=564k`, `cacheReadTokens=488.7k`
- **THEN** "新内容(净)" displays `564k - 488.7k = 75.3k`
- **AND** the tooltip "按正常 input 单价 (1×) 计费" is accurate.

#### Scenario: Anthropic net-new input includes cache creation
- **WHEN** the provider is Anthropic (`cacheCreationTokens > 0`) and cumulative `inputTokens=9k`, `cacheCreationTokens=5k`
- **THEN** "新内容(净)" displays `9k + 5k = 14k`
- **AND** the tooltip "按正常 input 单价 (1×) 计费" is accurate (cache creation billed at 1.25× is shown separately).

#### Scenario: Old run without snapshot fields
- **WHEN** the last run's `usage` JSON lacks `lastCacheReadTokens` / `turnCount`
- **THEN** the context window row shows `lastInputTokens` without decomposition sub-tree
- **AND** the header omits the "· N 轮" label.

#### Scenario: DeepSeek cost estimate with cache savings
- **WHEN** the primary model is `deepseek-v4-flash` with pricing `inputCacheHit=0.02, inputCacheMiss=1, output=2` (CNY/1M)
- **AND** cumulative `inputTokens=564k`, `cacheReadTokens=488.7k`, `outputTokens=7.1k`
- **THEN** the cost estimate row shows actual cost `¥0.10` (488.7k×0.02 + 75.3k×1 + 7.1k×2, all per 1M)
- **AND** shows "without cache" comparison `¥0.58`
- **AND** shows savings `¥0.48 (83%)`.

#### Scenario: Model without pricing data
- **WHEN** the primary model has no `pricing` entry in `KNOWN_MODELS`
- **THEN** the cost estimate row is not rendered
- **AND** no crash or placeholder text is shown.

## ADDED Requirements

### Requirement: Analytics main panel SHALL render usage timeseries in main window

When `sidebarMode === 'analytics'`, the root layout (`src/app/page.tsx`) MUST render `<AnalyticsMainPanel />` in the main window area instead of `<ChatPanel />`. This follows the same pattern as `'memory'` → `<MemoryMainPanel />` and `'knowledge'` → `<KnowledgeMainPanel />`.

The `AnalyticsMainPanel` component MUST fetch usage data from two endpoints in parallel on mount:
- `GET /api/usage/summary` — for StatCards (today/week/allTime) + byModel + byAgent + topConversations
- `GET /api/usage/timeseries?days=N` — for the daily trend chart, where N is the currently selected range

The panel MUST provide a time range switcher with three options: 7 days, 14 days (default), 30 days. Switching the range MUST re-fetch only the timeseries endpoint; the summary data MUST NOT be re-fetched on range switch.

The timeseries endpoint MUST return an array of daily buckets sorted ascending by date:
```
[{date: "2026-07-21", inputTokens, outputTokens, cacheReadTokens, cacheCreationTokens, totalTokens, runs}, ...]
```
Days with no usage data MUST be filled with zero-value buckets (`{totalTokens: 0, runs: 0, inputTokens: 0, ...}`) to ensure chart continuity. The `days` query parameter MUST accept 1–90; out-of-range values MUST be clamped to the nearest bound.

The main trend chart MUST be a composed chart (Recharts `ComposedChart`) with:
- Stacked bars for daily token breakdown: `inputTokens` (bottom), `outputTokens` (middle), `cacheReadTokens` (top), using distinct colors
- A line for daily `runs` count on a right-side Y axis (independent scale from tokens)
- X axis labels in `MM/DD` format
- An interactive tooltip showing the full daily breakdown on hover

The panel MUST display a top row of three StatCards (今日 / 本周 / 全部) from the summary endpoint, reusing the existing `StatCard` component.

The panel MUST display secondary sections for "按 Model" and "按 Agent" dimensions below the chart, reusing the existing `BarRow` component in a full-width card layout. The "Top 会话" section from the summary endpoint MUST render as clickable list items that call `setActiveConversation` on click (same behavior as the sidebar version).

The sidebar `UsageDashboard` component (in `w-60` narrow column) MUST be simplified to retain only the StatCard row + Top conversations list. The "按 Model" and "按 Agent" sections MUST be removed from the sidebar version (they are displayed in the main panel instead).

Loading and error states MUST be handled independently for the chart area and the StatCard/dimensions area: if the timeseries request fails, the StatCards and dimensions MUST still render; if the summary request fails, the chart MUST still render with an error placeholder in the StatCard area.

#### Scenario: User clicks analytics rail
- **WHEN** the user clicks the "分析" rail button (`sidebarMode` set to `'analytics'`)
- **THEN** the main window renders `<AnalyticsMainPanel />` instead of `<ChatPanel />`
- **AND** the sidebar shows the simplified `UsageDashboard` (StatCards + Top conversations only).

#### Scenario: Panel loads with default 14-day range
- **WHEN** `AnalyticsMainPanel` mounts
- **THEN** it fetches `/api/usage/summary` and `/api/usage/timeseries?days=14` in parallel
- **AND** the chart renders 14 daily bars (zero-filled for days with no data)
- **AND** the StatCards show today/week/allTime totals.

#### Scenario: User switches time range
- **WHEN** the user clicks "7 天" / "14 天" / "30 天" switcher
- **THEN** only `/api/usage/timeseries?days=N` is re-fetched
- **AND** the summary data (StatCards, byModel, byAgent, TopConv) remains unchanged
- **AND** the chart re-renders with the new range.

#### Scenario: Timeseries request fails
- **WHEN** `/api/usage/timeseries` returns an error
- **THEN** the chart area shows an error message with a retry button
- **AND** the StatCards and byModel/byAgent/TopConv sections still render from summary data.

#### Scenario: No usage data exists
- **WHEN** the user has no agent runs yet
- **THEN** the chart renders with all-zero bars
- **AND** the StatCards show 0 tokens / 0 runs
- **AND** the byModel / byAgent sections are hidden (empty)
- **AND** the Top conversations section shows an empty state message.

#### Scenario: Days parameter out of range
- **WHEN** `/api/usage/timeseries?days=0` or `?days=200` is requested
- **THEN** the server clamps `days` to 1 or 90 respectively
- **AND** returns the corresponding number of daily buckets.

#### Scenario: Daily bar tooltip
- **WHEN** the user hovers over a daily bar in the trend chart
- **THEN** a tooltip shows the date, inputTokens, outputTokens, cacheReadTokens, totalTokens, and runs for that day.
