# Add usage analytics main panel

## Why

侧栏「分析」tab 点击后，主窗口仍是 ChatPanel，用量数据挤在 `w-60` 窄栏里以文字条（StatCard + BarRow）展示，无法呈现「每日用量趋势」。现有 `/api/usage/summary` 只聚合出 today / week / allTime 三个 bucket，没有按日维度，前端也没有图表库。用户需要点击「分析」后主窗口变成全宽的用量分析面板，用折线+柱状图展示每日 token 用量与调用次数趋势。

## What Changes

- **后端新增 `GET /api/usage/timeseries?days=N` 端点**：按日聚合 `agent_runs.usage`，返回 `[{date, inputTokens, outputTokens, cacheReadTokens, cacheCreationTokens, totalTokens, runs}]` 数组。`days` 参数默认 14，范围 1–90。与现有 `/api/usage/summary`（快照聚合）语义分离。
- **前端新增 `AnalyticsMainPanel` 组件**：点击「分析」rail 后渲染在主窗口（替代 ChatPanel），全宽展示用量分析。
  - 顶部：时间范围切换器（7 天 / 14 天 / 30 天，默认 14 天）
  - 主图：ComposedChart（柱状 = 每日 totalTokens，按 input/output/cacheRead 堆叠；折线 = 每日 runs 次数，右 Y 轴）
  - 副区：保留现有 byModel / byAgent / TopConversations 维度，从窄栏 BarRow 迁移为卡片网格
  - 顶部 StatCard 行：今日 / 本周 / 全部 三个汇总数字（数据仍来自 `/api/usage/summary`）
- **`page.tsx` 主布局切换新增 analytics 分支**：`sidebarMode === 'analytics'` 时渲染 `<AnalyticsMainPanel />`，与 `'memory'` / `'knowledge'` 同模式。
- **侧栏 `UsageDashboard` 精简保留**：窄栏仍展示 StatCard（今日/本周/全部）+ Top 10 会话快捷跳转入口，作为「快速 glance + 跳转」。移除 byModel / byAgent 文字条（已迁移到主面板）。
- **新增前端依赖 `recharts`**：shadcn/ui 官方 chart 组件基于 Recharts 封装，与现有技术栈一致。新增 `src/components/ui/chart.tsx`（shadcn chart 组件）。
- **API client 类型扩展**：`src/lib/api.ts` 新增 `UsageTimeseriesPoint` 接口 + `fetchUsageTimeseries(days)` 函数。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `frontend`: 新增「分析主面板 SHALL 在主窗口渲染用量趋势图」requirement，定义 `AnalyticsMainPanel` 组件的行为契约（时间范围切换、图表类型、数据源、副维度布局）；扩展主布局切换规则（`sidebarMode === 'analytics'` 渲染 AnalyticsMainPanel）；定义 `/api/usage/timeseries` 响应类型契约。

## Impact

- **后端**：
  - `backend/app/services/usage_summary_service.py` — 新增 `get_usage_timeseries(days)` 函数，按日聚合 `AgentRun.usage` + `AgentRun.started_at`
  - `backend/app/api/runs_misc.py` — 新增 `GET /usage/timeseries` 路由
  - `backend/app/schemas/` — 新增 `UsageTimeseriesResponse` Pydantic 模型
- **前端**：
  - `src/app/page.tsx` — 主布局切换加 `'analytics'` 分支
  - `src/components/analytics-main-panel.tsx` — 新增主面板组件
  - `src/components/usage-dashboard.tsx` — 精简（移除 byModel/byAgent Section，保留 StatCard + TopConv）
  - `src/components/ui/chart.tsx` — 新增 shadcn chart 组件（Recharts 封装）
  - `src/lib/api.ts` — 新增 `UsageTimeseriesPoint` 接口 + `fetchUsageTimeseries()`
- **依赖**：新增 `recharts`（~150KB gzipped）。shadcn chart 组件是「复制到项目」模式，不引入额外 UI 库。
- **DB**：无 schema 变更。`agent_runs.usage` JSON 列 + `agent_runs.started_at` 已有，按日聚合是查询逻辑。
- **不碰**：StreamEvent 事件协议、DB schema、认证流程、现有 `/api/usage/summary` 响应结构（仅新增端点）。
