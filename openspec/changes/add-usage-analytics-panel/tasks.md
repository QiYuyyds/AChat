# Implementation Tasks

## 1. 后端 timeseries 端点

- [x] 1.1 在 `backend/app/services/usage_summary_service.py` 新增 `get_usage_timeseries(days: int)` 函数：扫描 `AgentRun.usage` + `AgentRun.started_at`，按自然日聚合，返回 `[{date, inputTokens, outputTokens, cacheReadTokens, cacheCreationTokens, totalTokens, runs}]` 升序数组。无数据日补零（从 `now - days*DAY_MS` 到 `now` 遍历填充）。
- [x] 1.2 在 `backend/app/schemas/` 新增 `UsageTimeseriesPoint` + `UsageTimeseriesResponse` Pydantic 模型（camelCase alias），`UsageTimeseriesResponse` 含 `points: list[UsageTimeseriesPoint]` 字段
- [x] 1.3 在 `backend/app/api/runs_misc.py` 新增 `GET /usage/timeseries` 路由，`days` query 参数默认 14、范围 1–90（超界 clamp），调 `get_usage_timeseries` 并通过 schema round-trip 输出 camelCase JSON
- [x] 1.4 后端测试：新增 `backend/tests/test_usage_timeseries.py`，验证空数据返回全零数组、多日聚合正确、`days` 参数 clamp 逻辑、日期升序

## 2. 前端依赖与 chart 组件

- [x] 2.1 `pnpm add recharts` 安装 Recharts 依赖
- [x] 2.2 通过 `npx shadcn@latest add chart` 引入 `src/components/ui/chart.tsx`（shadcn chart 组件封装），确认与现有 shadcn 组件风格一致
- [x] 2.3 验证 Recharts 与 React 19 兼容（`pnpm typecheck` 无 peer dependency 报错；若有警告尝试 `pnpm add recharts --legacy-peer-deps`）

## 3. 前端 API client 类型

- [x] 3.1 在 `src/lib/api.ts` 新增 `UsageTimeseriesPoint` 接口（`date: string` + `UsageBucket` 全部字段）
- [x] 3.2 在 `src/lib/api.ts` 新增 `fetchUsageTimeseries(days: number): Promise<UsageTimeseriesPoint[]>` 函数，调 `GET /api/usage/timeseries?days=N`

## 4. 前端 AnalyticsMainPanel 组件

- [x] 4.1 新建 `src/components/analytics-main-panel.tsx`，组件骨架：`useState` 管理 `summary` / `timeseries` / `days`（默认 14）/ `loading` / `error` 三个独立状态
- [x] 4.2 mount 时 `Promise.all([fetchUsageSummary(), fetchUsageTimeseries(14)])` 并行加载，两个请求的 loading/error 状态独立管理
- [x] 4.3 顶部 StatCard 行：复用 `usage-dashboard.tsx` 的 `StatCard` 组件（导出或内联），展示今日 / 本周 / 全部 三个汇总卡片
- [x] 4.4 时间范围切换器：三个按钮（7 天 / 14 天 / 30 天），当前选中高亮；切换时只调 `fetchUsageTimeseries(newDays)`，不重发 summary
- [x] 4.5 主趋势图：用 Recharts `ComposedChart` + `Bar`（stacked: inputTokens / outputTokens / cacheReadTokens 三层）+ `Line`（runs，`yAxisId="right"` 右轴），X 轴 `MM/DD` 格式，`ChartTooltip` 显示当日完整明细
- [x] 4.6 副区卡片网格：「按 Model」「按 Agent」Section（复用 `BarRow` 组件，全宽展示）+「Top 会话」列表（点击 `setActiveConversation` 跳转）
- [x] 4.7 刷新按钮：同时重发 summary + timeseries 两个请求
- [x] 4.8 独立 loading/error 状态：timeseries 失败时图表区显示 error + 重试按钮，StatCard/副区仍正常；summary 失败时图表仍正常，StatCard 区显示 error
- [x] 4.9 空数据状态：`allTime.runs === 0` 时图表全零 + byModel/byAgent 隐藏 + TopConv 显示空状态文案

## 5. 主布局切换

- [x] 5.1 在 `src/app/page.tsx` 主布局条件中新增 `'analytics'` 分支：`sidebarMode === 'analytics' ? <AnalyticsMainPanel /> : ...`，与 `'memory'` / `'knowledge'` 同模式

## 6. 侧栏 UsageDashboard 精简

- [x] 6.1 在 `src/components/usage-dashboard.tsx` 移除「按 Model」和「按 Agent」两个 Section（已迁移到主面板）
- [x] 6.2 保留 StatCard 行（今日/本周/全部）+ Top 会话列表（点击跳转）+ 空数据状态
- [x] 6.3 确认精简后的 `UsageDashboard` 在 `w-60` 窄栏中布局正常，不出现内容溢出或空白

## 7. 测试与验证

- [x] 7.1 后端：`ruff check .` 通过
- [x] 7.2 后端：`pytest` 通过（含新增的 `test_usage_timeseries.py`）
- [x] 7.3 前端：`pnpm typecheck` 通过
- [x] 7.4 前端：`pnpm lint` 通过
- [ ] 7.5 手动验证：点击「分析」rail，主窗口渲染 AnalyticsMainPanel（非 ChatPanel），侧栏显示精简版 UsageDashboard
- [ ] 7.6 手动验证：图表显示每日柱状（堆叠 input/output/cacheRead）+ 折线（runs 右轴），hover tooltip 显示当日明细
- [ ] 7.7 手动验证：切换 7d/14d/30d 时间范围，图表正确刷新，StatCard 不闪烁（summary 不重发）
- [ ] 7.8 手动验证：无数据用户（新注册）打开分析面板，图表全零 + 空状态文案，无报错
- [ ] 7.9 手动验证：Top 会话列表点击可跳转到对应会话（`setActiveConversation` 生效）
