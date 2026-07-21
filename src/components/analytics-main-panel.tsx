'use client'

import { BarChart3, Coins, Cpu, Loader2, MessageSquare, RefreshCw, TrendingUp, User } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
} from 'recharts'

import { ScrollArea } from '@/components/ui/scroll-area'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { formatTok } from '@/components/usage-dashboard'
import {
  fetchUsageSummary,
  fetchUsageTimeseries,
  type UsageSummary,
  type UsageTimeseriesPoint,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

const CHART_CONFIG = {
  inputTokens: { label: 'Input', color: 'var(--chart-1)' },
  outputTokens: { label: 'Output', color: 'var(--chart-2)' },
  cacheReadTokens: { label: 'Cache Read', color: 'var(--chart-3)' },
  runs: { label: 'Runs', color: 'var(--chart-4)' },
} satisfies ChartConfig

const RANGE_OPTIONS = [
  { label: '7 天', value: 7 },
  { label: '14 天', value: 14 },
  { label: '30 天', value: 30 },
] as const

function formatDateShort(dateStr: string): string {
  const [, mm, dd] = dateStr.split('-')
  return `${mm}/${dd}`
}

export function AnalyticsMainPanel() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [timeseries, setTimeseries] = useState<UsageTimeseriesPoint[]>([])
  const [days, setDays] = useState(14)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [timeseriesLoading, setTimeseriesLoading] = useState(true)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [timeseriesError, setTimeseriesError] = useState<string | null>(null)

  const setActiveConversation = useAppStore((s) => s.setActiveConversation)

  const loadSummary = useCallback(async () => {
    setSummaryLoading(true)
    setSummaryError(null)
    try {
      const data = await fetchUsageSummary()
      setSummary(data)
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : String(err))
    } finally {
      setSummaryLoading(false)
    }
  }, [])

  const loadTimeseries = useCallback(async (d: number) => {
    setTimeseriesLoading(true)
    setTimeseriesError(null)
    try {
      const data = await fetchUsageTimeseries(d)
      setTimeseries(data)
    } catch (err) {
      setTimeseriesError(err instanceof Error ? err.message : String(err))
    } finally {
      setTimeseriesLoading(false)
    }
  }, [])

  const refresh = useCallback(async () => {
    await Promise.all([loadSummary(), loadTimeseries(days)])
  }, [loadSummary, loadTimeseries, days])

  useEffect(() => {
    void Promise.all([loadSummary(), loadTimeseries(14)])
  }, [loadSummary, loadTimeseries])

  const handleRangeChange = useCallback(
    (newDays: number) => {
      setDays(newDays)
      void loadTimeseries(newDays)
    },
    [loadTimeseries],
  )

  const isEmpty = summary?.allTime.runs === 0

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* ─── Cinematic header — glass pill with ambient gradient ─── */}
      <header className="analytics-fade-up relative shrink-0 overflow-hidden border-b border-border/40 px-8 py-4">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-primary/[0.03] via-transparent to-primary/[0.03]" />
        <div className="relative flex items-center gap-3">
          <div className="relative flex size-9 items-center justify-center rounded-xl bg-primary/10">
            <div className="pointer-events-none absolute inset-0 rounded-xl bg-primary/5 blur-md" />
            <BarChart3 className="relative size-4 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="analytics-title-gradient text-xl font-bold tracking-tight">用量分析</h2>
            <p className="truncate text-xs text-muted-foreground">每日 token 用量趋势与维度分解</p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={summaryLoading || timeseriesLoading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-[var(--shadow-sm)] transition-all duration-300 hover:brightness-110 hover:shadow-[var(--shadow-md)] active:scale-95 disabled:opacity-50 disabled:hover:brightness-100"
          >
            <RefreshCw
              className={cn(
                'size-3.5',
                (summaryLoading || timeseriesLoading) && 'animate-spin',
              )}
            />
            刷新
          </button>
        </div>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-6 p-8">
          {/* ─── Hero stat cards — gapless bento, 3 cols ─── */}
          {summaryError ? (
            <div className="analytics-fade-up flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-xs text-destructive">
              <span className="flex-1">汇总数据加载失败: {summaryError}</span>
              <button
                type="button"
                onClick={() => void loadSummary()}
                className="inline-flex items-center gap-1 rounded-md border border-destructive/30 px-2 py-0.5 transition hover:bg-destructive/20"
              >
                <RefreshCw className="size-3" />
                重试
              </button>
            </div>
          ) : summaryLoading && !summary ? (
            <div className="grid grid-flow-row-dense grid-cols-3 gap-3">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-[104px] animate-pulse rounded-xl border border-border/40 bg-card"
                />
              ))}
            </div>
          ) : summary ? (
            <div className="grid grid-flow-row-dense grid-cols-3 gap-3">
              <PremiumStatCard
                label="今日"
                value={summary.today.totalTokens}
                runs={summary.today.runs}
                accentIndex={0}
                delayClass="analytics-fade-up-delay-1"
              />
              <PremiumStatCard
                label="本周"
                value={summary.week.totalTokens}
                runs={summary.week.runs}
                accentIndex={1}
                delayClass="analytics-fade-up-delay-2"
              />
              <PremiumStatCard
                label="全部"
                value={summary.allTime.totalTokens}
                runs={summary.allTime.runs}
                accentIndex={2}
                delayClass="analytics-fade-up-delay-3"
              />
            </div>
          ) : null}

          {/* ─── Trend chart — premium card with ambient radial blur ─── */}
          <div className="analytics-fade-up-delay-2 relative overflow-hidden rounded-xl border border-border/40 bg-card/50 p-5 shadow-[var(--shadow-sm)]">
            {/* Ambient radial blur — breathing glow */}
            <div className="analytics-ambient pointer-events-none absolute -left-16 top-1/2 size-48 -translate-y-1/2 rounded-full bg-primary/[0.04] blur-3xl" />
            <div className="relative">
              {/* Chart header + range switcher */}
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold tracking-tight">每日用量趋势</h3>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">堆叠 token 构成 + 调用次数</p>
                </div>
                <div className="flex items-center gap-0.5 rounded-lg border border-border/40 bg-muted/30 p-0.5">
                  {RANGE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handleRangeChange(opt.value)}
                      className={cn(
                        'rounded-md px-3 py-1 text-xs font-medium transition-all duration-300',
                        days === opt.value
                          ? 'bg-primary text-primary-foreground shadow-[var(--shadow-sm)]'
                          : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {timeseriesError ? (
                <div className="flex h-[300px] flex-col items-center justify-center gap-3 text-center">
                  <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {timeseriesError}
                  </div>
                  <button
                    type="button"
                    onClick={() => void loadTimeseries(days)}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition-all duration-300 hover:brightness-110 active:scale-95"
                  >
                    <RefreshCw className="size-3" />
                    重试
                  </button>
                </div>
              ) : timeseriesLoading ? (
                <div className="flex h-[300px] items-center justify-center">
                  <Loader2 className="size-5 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <>
                  <ChartContainer config={CHART_CONFIG} className="h-[300px] w-full">
                    <ComposedChart data={timeseries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" opacity={0.4} />
                      <XAxis
                        dataKey="date"
                        tickFormatter={formatDateShort}
                        tickLine={false}
                        axisLine={false}
                        tickMargin={8}
                        minTickGap={24}
                      />
                      <YAxis
                        yAxisId="left"
                        tickFormatter={(v: number) => formatTok(v)}
                        tickLine={false}
                        axisLine={false}
                        width={48}
                      />
                      <YAxis
                        yAxisId="right"
                        orientation="right"
                        tickLine={false}
                        axisLine={false}
                        width={32}
                      />
                      <ChartTooltip
                        content={
                          <ChartTooltipContent
                            labelFormatter={(_, payload) => {
                              const date = payload?.[0]?.payload?.date as string | undefined
                              return date ? formatDateShort(date) : ''
                            }}
                          />
                        }
                      />
                      <Bar yAxisId="left" dataKey="inputTokens" stackId="tokens" barSize={14} fill="var(--color-inputTokens)" radius={[0, 0, 0, 0]} />
                      <Bar yAxisId="left" dataKey="outputTokens" stackId="tokens" barSize={14} fill="var(--color-outputTokens)" radius={[0, 0, 0, 0]} />
                      <Bar yAxisId="left" dataKey="cacheReadTokens" stackId="tokens" barSize={14} fill="var(--color-cacheReadTokens)" radius={[3, 3, 0, 0]} />
                      <Line yAxisId="right" dataKey="runs" stroke="var(--color-runs)" strokeWidth={2} dot={false} />
                    </ComposedChart>
                  </ChartContainer>

                  {/* Legend */}
                  <div className="mt-3 flex items-center justify-center gap-5 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <span className="size-2.5 rounded-sm" style={{ backgroundColor: 'var(--chart-1)' }} />
                      Input
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-2.5 rounded-sm" style={{ backgroundColor: 'var(--chart-2)' }} />
                      Output
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-2.5 rounded-sm" style={{ backgroundColor: 'var(--chart-3)' }} />
                      Cache Read
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-2.5 rounded-full" style={{ backgroundColor: 'var(--chart-4)' }} />
                      Runs (右轴)
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* ─── Dimensions — gapless bento, 2 cols (byModel + byAgent) ─── */}
          {summary && !isEmpty && (
            <div className="grid grid-flow-row-dense grid-cols-2 gap-4">
              {/* By Model */}
              {summary.byModel.length > 0 && (
                <div className="analytics-fade-up-delay-3 group overflow-hidden rounded-xl border border-border/40 bg-card p-4 shadow-[var(--shadow-sm)] transition-all duration-500 hover:border-primary/20 hover:shadow-[var(--shadow-md)]">
                  <div className="mb-3 flex items-center gap-2">
                    <Cpu className="size-3.5 text-primary/60" />
                    <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">按 Model</h3>
                  </div>
                  <div className="space-y-2.5">
                    {summary.byModel.map((m, i) => {
                      const pct = summary.byModel[0].totalTokens > 0
                        ? (m.totalTokens * 100) / summary.byModel[0].totalTokens
                        : 0
                      return (
                        <div key={m.model}>
                          <div className="flex items-baseline justify-between gap-2">
                            <code className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">{m.model}</code>
                            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                              {formatTok(m.totalTokens)} · {m.runs}
                            </span>
                          </div>
                          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                            <div
                              className="analytics-bar-grow h-full rounded-full bg-gradient-to-r from-primary/70 via-primary/50 to-primary/30 transition-all duration-500"
                              style={{ width: `${pct}%`, animationDelay: `${i * 80}ms` }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* By Agent */}
              {summary.byAgent.length > 0 && (
                <div className="analytics-fade-up-delay-4 group overflow-hidden rounded-xl border border-border/40 bg-card p-4 shadow-[var(--shadow-sm)] transition-all duration-500 hover:border-primary/20 hover:shadow-[var(--shadow-md)]">
                  <div className="mb-3 flex items-center gap-2">
                    <User className="size-3.5 text-primary/60" />
                    <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">按 Agent</h3>
                  </div>
                  <div className="space-y-2.5">
                    {summary.byAgent.map((a, i) => {
                      const pct = summary.byAgent[0].totalTokens > 0
                        ? (a.totalTokens * 100) / summary.byAgent[0].totalTokens
                        : 0
                      return (
                        <div key={a.agentId}>
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="min-w-0 flex-1 truncate text-xs text-foreground">{a.name}</span>
                            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                              {formatTok(a.totalTokens)} · {a.runs}
                            </span>
                          </div>
                          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                            <div
                              className="analytics-bar-grow h-full rounded-full bg-gradient-to-r from-chart-2/70 via-chart-2/50 to-chart-2/30 transition-all duration-500"
                              style={{ width: `${pct}%`, animationDelay: `${i * 80}ms` }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ─── Top conversations — premium clickable card grid ─── */}
          {summary && !isEmpty && summary.topConversations.length > 0 && (
            <div className="analytics-fade-up-delay-4">
              <div className="mb-3 flex items-center gap-2">
                <MessageSquare className="size-3.5 text-primary/60" />
                <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  Top {Math.min(summary.topConversations.length, 10)} 会话
                </h3>
              </div>
              <div className="grid grid-flow-row-dense grid-cols-2 gap-2">
                {summary.topConversations.map((c, i) => {
                  const isLastOdd =
                    i === summary.topConversations.length - 1 &&
                    summary.topConversations.length % 2 === 1
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setActiveConversation(c.id)}
                      className={cn(
                        'group overflow-hidden rounded-lg border border-border/30 bg-card px-3 py-2.5 text-left transition-all duration-500 hover:border-primary/30 hover:bg-accent/30 hover:shadow-[var(--shadow-sm)]',
                        isLastOdd && 'col-span-2',
                      )}
                      title={`点击跳转 · 更新时间 ${new Date(c.updatedAt).toLocaleString('zh-CN')}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground transition-colors duration-300 group-hover:text-primary">
                          {c.title}
                        </span>
                        <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary/8 px-1.5 py-0.5 font-mono text-[10px] text-primary transition-all duration-300 group-hover:bg-primary/15">
                          <Coins className="size-2.5" />
                          {formatTok(c.totalTokens)}
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* ─── Empty state — cinematic ambient ─── */}
          {summary && isEmpty && (
            <div className="analytics-fade-up flex flex-col items-center gap-4 py-16 text-center">
              <div className="relative">
                <div className="analytics-ambient pointer-events-none absolute inset-0 rounded-full bg-primary/10 blur-2xl" />
                <div className="relative flex size-16 items-center justify-center rounded-2xl border border-border/40 bg-card shadow-[var(--shadow-sm)]">
                  <TrendingUp className="size-6 text-muted-foreground" />
                </div>
              </div>
              <div className="space-y-1">
                <p className="text-base font-semibold text-foreground">还没有用量数据</p>
                <p className="text-xs text-muted-foreground">跟 Agent 聊几句就有了</p>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

/* ─── Premium StatCard — editorial scale, hover physics, ambient wash ─── */

const ACCENT_GRADIENTS = [
  'from-chart-1/8 to-transparent',
  'from-chart-2/8 to-transparent',
  'from-chart-4/8 to-transparent',
] as const

function PremiumStatCard({
  label,
  value,
  runs,
  accentIndex,
  delayClass,
}: {
  label: string
  value: number
  runs: number
  accentIndex: number
  delayClass: string
}) {
  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-xl border border-border/40 bg-card p-5 shadow-[var(--shadow-sm)] transition-all duration-700 hover:border-primary/25 hover:shadow-[var(--shadow-md)]',
        delayClass,
      )}
    >
      {/* Ambient gradient wash — accent per card */}
      <div
        className={cn(
          'pointer-events-none absolute -right-6 -top-6 size-24 rounded-full bg-gradient-to-br opacity-60 blur-2xl transition-opacity duration-700 group-hover:opacity-100',
          ACCENT_GRADIENTS[accentIndex] ?? ACCENT_GRADIENTS[0],
        )}
      />
      {/* Label */}
      <div className="relative text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </div>
      {/* Large editorial number */}
      <div className="analytics-number-in relative mt-2 font-mono text-3xl font-bold tracking-tight text-foreground">
        {formatTok(value)}
      </div>
      {/* Runs sub-label */}
      <div className="relative mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="size-1 rounded-full bg-primary/50" />
        {runs} runs
      </div>
    </div>
  )
}
