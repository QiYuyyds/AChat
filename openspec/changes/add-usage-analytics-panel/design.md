## Context

当前「分析」入口在侧栏图标轨（`Gauge` icon，`sidebarMode='analytics'`）。点击后 `page.tsx` 主布局走默认分支渲染 `ChatPanel`，用量数据只在侧栏 `w-60` 窄栏内的 `UsageDashboard` 组件展示——三个 StatCard（今日/本周/全部）+ BarRow 文字条（byModel / byAgent / TopConv）。

数据源：`GET /api/usage/summary` → `usage_summary_service.get_usage_summary()` 扫描全部 `AgentRun.usage` JSONB 行，聚合出 today / week / allTime 三个 bucket + byAgent / byModel / topConversations。没有按日维度。

前端 `package.json` 无任何图表库依赖。shadcn/ui 的 chart 组件（`src/components/ui/chart.tsx`）尚未引入。

约束：
- CLAUDE.md §2 锁定 shadcn/ui，不引入其他 UI 库
- §6.2 新增依赖需用户确认（本方案 Recharts 是 shadcn chart 的底层依赖，与 shadcn 生态一致）
- §3.1 五层分层：UI 不直接调 LLM，经过 L3 服务层；本变更不涉及 LLM 调用
- §3.8 RAG/记忆是可选增强——usage 分析同理，数据为空时优雅降级

## Goals / Non-Goals

**Goals:**
- 点击「分析」rail 后，主窗口（全宽）展示用量分析面板，不再是 ChatPanel
- 每日用量趋势可视化：柱状图展示每日 token 用量（按 input/output/cacheRead 堆叠），折线图叠加每日 runs 次数（右 Y 轴）
- 时间范围可切换（7d / 14d / 30d，默认 14d）
- 保留 today/week/allTime 汇总 StatCard + byModel/byAgent/TopConv 维度，迁移到主面板全宽展示
- 侧栏精简为「快速 glance + TopConv 跳转入口」

**Non-Goals:**
- 不做实时更新（面板 mount 时拉取一次，用户点刷新按钮重拉；不接 SSE 推送 usage 增量）
- 不做按 agent / model 维度筛选图表（MVP 全局聚合，后续可加 filter）
- 不做费用趋势图（费用估算仍在 `UsageBadge` 单会话级，本面板不展示每日费用）
- 不改 `/api/usage/summary` 响应结构（新增独立 timeseries 端点，向后兼容）
- 不做数据导出（CSV / JSON 下载留待后续）
- 不做 90d 以上长周期（`days` 参数上限 90，避免全表扫描性能问题）

## Decisions

### Decision 1: 图表库选 Recharts

shadcn/ui 官方 chart 组件（`chart.tsx`）是对 Recharts 的封装，提供 `ChartContainer` / `ChartTooltip` / `ChartConfig` 等工具。引入 Recharts 与 CLAUDE.md §2「不引入其他 UI 库；shadcn 是复制组件到本项目模式」一致——Recharts 是 shadcn chart 组件的底层依赖，不是独立 UI 库。

**被否决方案：**
- **纯 SVG 手写**：折线 + 堆叠柱状 + 双 Y 轴 + 坐标轴 + tooltip + 响应式，手写工作量 ~400 行且后续维护成本高。图表交互（hover tooltip、点击命中）难以做到 Recharts 的开箱即用质量。
- **Chart.js / react-chartjs-2**：生态成熟但不如 Recharts 与 shadcn 整合顺（shadcn chart 组件直接 import recharts 组件）。Canvas 渲染在 tooltip 定制上不如 SVG 灵活。
- **Visx / D3**：太底层，需要手写大量图表逻辑。

Recharts ~150KB gzipped，对本地运行的桌面端应用可接受。

### Decision 2: 新增 `/api/usage/timeseries` 端点，不扩展 summary

`/api/usage/summary` 返回的是快照聚合（today/week/allTime 三个 bucket），语义是「当前状态的汇总」。按日趋势是时间序列数据，语义不同。分离端点的好处：

1. summary 保持精简（前端 `UsageBadge` 只需快照，不需要拖一份按日数组）
2. timeseries 可独立加 `?days=N` 参数，summary 无参数
3. 后续可给 timeseries 加 `?groupBy=hour|day|week` 而不影响 summary

**被否决方案：**
- **扩展 summary 返回 `byDay` 字段**：summary 响应膨胀，且 `UsageBadge` 不需要按日数据却被迫接收。前端类型耦合。

### Decision 3: 按日聚合逻辑——本地时区 + 自然日

`AgentRun.started_at` 是毫秒时间戳（UTC）。按日聚合时：
- 取用户本地时区的自然日边界（前端发请求时带 `tz` offset 不必要——后端按服务器时区聚合即可，本地运行场景服务器=用户机）
- 每个数据点 `date` 字段用 `YYYY-MM-DD` 格式（如 `"2026-07-21"`），前端直接作为 X 轴 label
- 无数据的日期补零（避免图表断线）：从 `now - days*DAY_MS` 到 `now` 遍历，缺失日填充 `{totalTokens: 0, runs: 0, ...}`

### Decision 4: 主图用 ComposedChart（柱状堆叠 + 折线双轴）

```
┌─────────────────────────────────────────────────────────┐
│  每日用量趋势                          [7d] [14d] [30d]  │
├─────────────────────────────────────────────────────────┤
│  tokens                                                 │
│  500k ┤                              ╭───╮              │
│       │      ┌─┐        ┌─┐         │     │     ┌─┐    │
│  250k ┤      │ │ ┌─┐    │ │ ┌─┐    │     │     │ │    │
│       │      │ │ │ │    │ │ │ │    │     │ ╭───╯ │    │
│    0  ┴─────┘─┘─┘─┴─────┘─┘─┘─┴─────┘─────┴─┘─────┴─────┤
│           7/15 7/16 7/17 7/18 7/19 7/20 7/21            │
│  runs  12 ┤         ╭──╮               ╭──╮             │
│       6   ┤    ╭──╮ │  │        ╭──╮   │  │   ╭──╮      │
│       0   ┴────╯  ╰─╯  ╰────────╯  ╰───╯  ╰──╯  ╰───────│
└─────────────────────────────────────────────────────────┘
  ■ input  ■ output  ■ cacheRead  ─ runs(右轴)
```

- 柱状：堆叠 `inputTokens` / `outputTokens` / `cacheReadTokens`（三层，颜色区分）
- 折线：`runs` 次数，右 Y 轴（独立刻度，避免 token 数量级淹没 runs）
- X 轴：日期 `MM/DD` 格式
- Tooltip：hover 显示当日完整明细（input/output/cacheRead/total/runs）

用 Recharts `ComposedChart` + `Bar` (stacked) + `Line` (yAxisId="right")。

### Decision 5: 侧栏精简策略——保留 StatCard + TopConv，移除 byModel/byAgent

侧栏 `w-60`（240px）的空间适合「快速 glance」：
- 保留三个 StatCard（今日/本周/全部）——用户不需要点开主面板就能看一眼总量
- 保留 Top 10 会话列表——作为「最耗 token 的会话」快捷跳转入口（点击 `setActiveConversation`）
- 移除 byModel / byAgent BarRow——这些维度在主面板有更丰富的展示空间

主面板副区用卡片网格展示 byModel / byAgent，每个卡片含名称 + token 数 + runs + 进度条（复用 `BarRow` 组件但全宽展示）。

### Decision 6: 数据加载策略——并行双请求

`AnalyticsMainPanel` mount 时并行发起两个请求：
```
Promise.all([
  fetchUsageSummary(),         // → StatCard + byModel + byAgent + TopConv
  fetchUsageTimeseries(days),  // → 每日趋势数据
])
```

切换时间范围时只重发 timeseries 请求（summary 不变）。刷新按钮两个都重发。

loading / error 状态分两个区域独立处理：图表区显示 spinner / 重试按钮，StatCard 区显示骨架屏——避免一个请求失败导致整个面板空白。

### Decision 7: shadcn chart 组件引入方式

通过 `npx shadcn@latest add chart` 引入 `src/components/ui/chart.tsx`。该组件封装了 Recharts 的 `ChartContainer`（响应式容器）+ `ChartTooltip`（统一 tooltip 样式）+ `ChartConfig`（颜色 / label 配置）。与现有 shadcn 组件（button / dialog / popover 等）同模式——复制到项目，可自由修改。

需要手动 `pnpm add recharts` 安装底层依赖（shadcn CLI 不自动装 recharts）。

## Risks / Trade-offs

- **[Recharts 包体积 ~150KB]** → 本地运行的桌面端应用，非 CDN 公网，体积可接受。Next.js 16 的 turbopack 会 tree-shake未使用的 Recharts 组件。
- **[全表扫描 `agent_runs` 性能]** → `get_usage_timeseries` 扫描全部 run 行（与 `get_usage_summary` 同模式）。run 行数量在本地单用户场景下增长缓慢（日均 < 100 runs）。若后续性能成为问题，可加 `started_at` 索引或限制 `days` 参数扫描范围。当前 `days` 上限 90 防止无界扫描。
- **[时区漂移]** → 后端按服务器时区聚合自然日。本地运行场景服务器=用户机，时区一致。若后续部署到云端，需改为带 `tz` 参数。MVP 不处理。
- **[空数据日补零 vs 跳过]** → 补零（`{totalTokens: 0, runs: 0}`）保证图表连续不断线。跳过会导致 X 轴日期缺失，视觉误导（看起来像那天没数据 vs 那天用量为 0）。补零更符合直觉。
- **[双 Y 轴可读性]** → token 数（万级）和 runs 次数（个位~十位）量级差异大，双 Y 轴是标准做法。Tooltip 会同时显示两个值，避免读图歧义。
- **[侧栏与主面板数据重复请求]** → 侧栏 `UsageDashboard` 和主面板 `AnalyticsMainPanel` 都调 `/api/usage/summary`。两个组件各自 mount 时各发一次请求。可接受——summary 是轻量聚合，且两个组件很少同时 mount（侧栏在 analytics 模式下仍可见，但主面板是 analytics）。后续可用 SWR / react-query 去重，MVP 不引入。
- **[Recharts 与 React 19 兼容性]** → Recharts 2.x 已支持 React 19。若遇到 peer dependency 警告，`pnpm add recharts --legacy-peer-deps` 或检查 Recharts 最新版本。

## Migration Plan

无 DB 迁移、无破坏性变更。

部署顺序：
1. 后端新增 timeseries 端点（独立部署不影响现有功能）
2. 前端 `pnpm add recharts` + 引入 shadcn chart 组件
3. 新增 `AnalyticsMainPanel` 组件 + `page.tsx` 布局切换
4. 精简侧栏 `UsageDashboard`

回滚策略：前端改动可单独 revert（`page.tsx` 布局切换 + 新组件）。后端新端点是无害的附加路由，无需回滚。Recharts 依赖可通过 `pnpm remove recharts` 清理。
