'use client'

import { BarChart3, Coins, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ScrollArea } from '@/components/ui/scroll-area'
import { fetchUsageSummary, type UsageBucket, type UsageSummary } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

/**
 * UsageDashboard —— 侧栏「分析」tab 内容。
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
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        加载用量数据...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-4 text-center text-xs">
        <div className="text-destructive">{error}</div>
        <button
          type="button"
          onClick={() => void reload()}
          className="rounded-md border px-2 py-1 hover:bg-accent"
        >
          重试
        </button>
      </div>
    )
  }

  if (!data) return null

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="space-y-4 p-3 text-xs">
        <header className="flex items-center justify-between border-b pb-2">
          <span className="flex items-center gap-1.5 font-medium">
            <BarChart3 className="size-3.5" />
            用量分析
          </span>
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading}
            className="font-mono text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {loading ? '…' : '刷新'}
          </button>
        </header>

        {/* 时间桶 */}
        <Section title="按时间">
          <BucketRow label="今日" b={data.today} />
          <BucketRow label="本周" b={data.week} />
          <BucketRow label="全部" b={data.allTime} bold />
        </Section>

        {/* 按 Model */}
        {data.byModel.length > 0 && (
          <Section title="按 Model">
            {data.byModel.map((m) => (
              <BarRow
                key={m.model}
                label={<code className="font-mono">{m.model}</code>}
                value={m.totalTokens}
                runs={m.runs}
                max={data.byModel[0].totalTokens}
              />
            ))}
          </Section>
        )}

        {/* 按 Agent */}
        {data.byAgent.length > 0 && (
          <Section title="按 Agent">
            {data.byAgent.map((a) => (
              <BarRow
                key={a.agentId}
                label={a.name}
                value={a.totalTokens}
                runs={a.runs}
                max={data.byAgent[0].totalTokens}
              />
            ))}
          </Section>
        )}

        {/* Top 会话 */}
        {data.topConversations.length > 0 && (
          <Section title={`Top ${Math.min(data.topConversations.length, 10)} 会话`}>
            {data.topConversations.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setActiveConversation(c.id)}
                className="flex w-full items-baseline justify-between gap-2 rounded px-1 py-0.5 text-left transition hover:bg-accent"
                title={`点击跳转 · 更新时间 ${new Date(c.updatedAt).toLocaleString('zh-CN')}`}
              >
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
                <span className="shrink-0 font-mono text-muted-foreground">
                  <Coins className="mr-1 inline size-3" />
                  {formatTok(c.totalTokens)}
                </span>
              </button>
            ))}
          </Section>
        )}

        {data.allTime.runs === 0 && (
          <div className="rounded-md border border-dashed bg-muted/30 px-3 py-6 text-center text-xs text-muted-foreground">
            还没有用量数据 —— 跟 Agent 聊几句就有了
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      <div className="space-y-1">{children}</div>
    </div>
  )
}

function BucketRow({
  label,
  b,
  bold,
}: {
  label: string
  b: UsageBucket
  bold?: boolean
}) {
  return (
    <div className={cn('flex items-baseline justify-between gap-2', bold && 'font-medium')}>
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono">
        {formatTok(b.totalTokens)}
        <span className="ml-1 text-[10px] text-muted-foreground">
          {b.runs > 0 ? `· ${b.runs} run` : ''}
        </span>
      </span>
    </div>
  )
}

function BarRow({
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
    <div className="space-y-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span className="shrink-0 font-mono text-muted-foreground">
          {formatTok(value)}
          <span className="ml-1 text-[10px]">· {runs}</span>
        </span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-[#3370FF]/70 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function formatTok(n: number): string {
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}
