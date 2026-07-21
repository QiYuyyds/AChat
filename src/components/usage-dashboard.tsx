'use client'

import { BarChart3, Coins, Loader2, MessageSquare, RefreshCw, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ScrollArea } from '@/components/ui/scroll-area'
import { fetchUsageSummary, type UsageSummary } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

/**
 * UsageDashboard -- 侧栏「分析」tab 内容。
 *
 * 展示跨会话的 token 用量聚合：今日 / 本周 / 全部 + per-agent / per-model / per-conv top。
 * 数据来自 /api/usage/summary（每次 mount 拉一次；用户切回 tab 也重拉，保证 fresh）。
 */
export function UsageDashboard() {
  const [data, setData] = useState<UsageSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const setActiveConversation = useAppStore((s) => s.setActiveConversation)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const summary = await fetchUsageSummary()
      setData(summary)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  if (loading && !data) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
        <span className="text-xs text-muted-foreground">加载用量数据...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-4 text-center">
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
        <button
          type="button"
          onClick={() => void reload()}
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition hover:bg-accent"
        >
          <RefreshCw className="size-3" />
          重试
        </button>
      </div>
    )
  }

  if (!data) return null

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="space-y-3 p-3">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10">
              <BarChart3 className="size-3.5 text-primary" />
            </div>
            <span className="text-sm font-semibold">用量分析</span>
          </div>
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn('size-3', loading && 'animate-spin')} />
            刷新
          </button>
        </div>

        {/* Stat cards */}
        <div className="grid grid-cols-3 gap-2">
          <StatCard label="今日" value={data.today.totalTokens} runs={data.today.runs} />
          <StatCard label="本周" value={data.week.totalTokens} runs={data.week.runs} />
          <StatCard label="全部" value={data.allTime.totalTokens} runs={data.allTime.runs} highlight />
        </div>

        {/* Top conversations */}
        {data.topConversations.length > 0 && (
          <Section title={`Top ${Math.min(data.topConversations.length, 10)} 会话`} icon={<MessageSquare className="size-3" />}>
            {data.topConversations.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setActiveConversation(c.id)}
                className="flex w-full items-center gap-2 rounded-md border border-border/30 px-2 py-1.5 text-left transition-all duration-150 hover:border-primary/20 hover:bg-accent/50"
                title={`点击跳转 · 更新时间 ${new Date(c.updatedAt).toLocaleString('zh-CN')}`}
              >
                <span className="min-w-0 flex-1 truncate text-xs">{c.title}</span>
                <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  <Coins className="size-2.5" />
                  {formatTok(c.totalTokens)}
                </span>
              </button>
            ))}
          </Section>
        )}

        {/* Empty state */}
        {data.allTime.runs === 0 && (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <div className="flex size-12 items-center justify-center rounded-xl bg-muted/60 shadow-[var(--shadow-sm)]">
              <TrendingUp className="size-5 text-muted-foreground" />
            </div>
            <div className="space-y-0.5">
              <p className="text-sm font-medium text-foreground">还没有用量数据</p>
              <p className="text-xs text-muted-foreground">跟 Agent 聊几句就有了</p>
            </div>
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

export function StatCard({
  label,
  value,
  runs,
  highlight,
}: {
  label: string
  value: number
  runs: number
  highlight?: boolean
}) {
  return (
    <div
      className={cn(
        'rounded-lg border p-2 text-center shadow-[var(--shadow-sm)]',
        highlight ? 'border-primary/30 bg-primary/5' : 'border-border/40 bg-card',
      )}
    >
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={cn('mt-0.5 font-mono text-sm font-semibold', highlight && 'text-primary')}>
        {formatTok(value)}
      </div>
      <div className="text-[9px] text-muted-foreground">{runs} runs</div>
    </div>
  )
}

export function Section({
  title,
  icon,
  children,
}: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-border/40 bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
        {icon}
        {title}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  )
}

export function BarRow({
  label,
  value,
  runs,
  max,
}: {
  label: React.ReactNode
  value: number
  runs: number
  max: number
}) {
  const pct = max > 0 ? (value * 100) / max : 0
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-xs">{label}</span>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
          {formatTok(value)} · {runs}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary/80 to-primary/40 transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export function formatTok(n: number): string {
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}
