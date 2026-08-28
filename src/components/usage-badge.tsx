'use client'

import { Clock, Coins, Cpu, TrendingUp, Users } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { getModelLimits, getModelPricing } from '@/shared/model-registry'
import {
  computeCacheHitRate,
  computeCost,
  computeLastNetInput,
  computeWeightedCacheHitRate,
  type CacheStyleBucket,
} from '@/shared/usage'
import type { CacheStyle } from '@/shared/types'
import { useAppStore, useConversationUsageTotal, type AgentUsageDetail } from '@/stores/app-store'

/**
 * UsageBadge —— ChatPanel header 里的 token 用量徽章。
 *
 * 显示「Σ N.Nk tok」（该会话累计），hover/click 展开 popover 看：
 *   1. 总览：input/output/cache 拆分 + cache 命中率 + ctx 大小
 *   2. 按 Agent：每个 agent 独立卡片，显示各自的 token 明细 + cache 命中率
 *   3. 按 Model：按模型聚合
 *
 * 没用量时不渲染（首次进入会话之前没数据）。
 */
export function UsageBadge({ conversationId }: { conversationId: string }) {
  const total = useConversationUsageTotal(conversationId)
  const agents = useAppStore((s) => s.agents)
  const modelProfiles = useAppStore((s) => s.modelProfiles)

  if (total.runCount === 0) return null

  const cacheHitRate = computeWeightedCacheHitRate(total.byCacheStyle)
  const hasCacheData = total.cacheReadTokens > 0 || total.runCount > 1

  // 取本会话内 contextWindow 最大的 model profile 作为可见上限。
  const contextWindow = (() => {
    const profiles = Object.values(modelProfiles)
    if (profiles.length === 0) return 0
    let maxCtx = 0
    for (const p of profiles) {
      const limits = getModelLimits(p.provider, p.modelId)
      if (limits.effectiveContextWindow > maxCtx) maxCtx = limits.effectiveContextWindow
    }
    return maxCtx
  })()

  const agentEntries = Object.entries(total.byAgentDetail).sort(
    (a, b) => b[1].totalTokens - a[1].totalTokens,
  )
  const hasMultipleAgents = agentEntries.length > 1
  const hasSubagentTokens = agentEntries.some(([, d]) => d.subagentTokens > 0)
  const showAgentDetails = hasMultipleAgents || hasSubagentTokens

  const netInput = total.netInput
  const hasDecomposition = total.lastCacheReadTokens > 0 || total.turnCount > 0
  const lastNetNew = computeLastNetInput(
    total.lastCacheStyle,
    total.lastInputTokens,
    total.lastCacheReadTokens,
  )

  return (
    <Popover>
      <PopoverTrigger
        className={cn(
          'inline-flex shrink-0 items-center gap-1.5 rounded-lg border bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground shadow-[var(--shadow-sm)] transition hover:border-primary/30 hover:bg-muted hover:text-foreground hover:shadow-md',
        )}
        title="点击查看 token 用量明细"
      >
        <Coins className="size-3 text-primary/70" />
        <span>{formatTok(total.totalTokens)}</span>
      </PopoverTrigger>
      <PopoverContent className="w-96 max-h-[70vh] overflow-y-auto p-3 text-xs" align="end">
        <div className="mb-2.5 flex items-center justify-between border-b pb-2">
          <div className="flex items-center gap-1.5">
            <Coins className="size-3.5 text-primary" />
            <span className="font-medium">本会话 token 累计</span>
          </div>
          <span className="text-[10px] text-muted-foreground">
            {total.runCount} 次响应{total.turnCount > 0 ? ` · ${total.turnCount} 轮` : ''}
            {hasCacheData && cacheHitRate > 0 && (
              <span className="ml-1 text-emerald-600 dark:text-emerald-400">· 缓存 {Math.round(cacheHitRate)}%</span>
            )}
          </span>
        </div>

        {/* ── 累计（跨 N 轮）── */}
        <div className="space-y-1">
          <div className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
            <TrendingUp className="size-2.5" />
            累计{total.turnCount > 0 ? `（跨 ${total.turnCount} 轮）` : ''}
          </div>
          <RowWithHint
            label="新内容(净)"
            value={netInput}
            highlight
            tip="按正常 input 单价 (1×) 计费（累计 input 扣除缓存复用）"
          />
          <RowWithHint
            label="Output"
            value={total.outputTokens}
            highlight
            tip="按 output 单价计费 (通常 4-5× input)"
          />
          {total.cacheCreationTokens > 0 && (
            <RowWithHint
              label="Cache 写入"
              value={total.cacheCreationTokens}
              dim
              tip="按 1.25× input 单价计费 (略贵)"
            />
          )}
          <RowWithHint
            label="Cache 命中"
            value={total.cacheReadTokens}
            tip="按 0.1× input 单价计费 (便宜 90%)"
            className={total.cacheReadTokens > 0 ? 'text-emerald-600' : 'text-muted-foreground'}
          />
          <div className="my-1 border-t" />
          <Row
            label="实际 Prompt"
            value={total.totalTokens}
            bold
            hint="逐 run 按 cacheStyle 正确累加"
          />
          <CostEstimateRow
            byModel={total.byModel}
            byCacheStyle={total.byCacheStyle}
          />
          {/* Cache 命中率 — provider-aware formula + visual bar */}
          {hasCacheData && (
            <CacheHitRateRow rate={cacheHitRate} cacheReadTokens={total.cacheReadTokens} />
          )}
        </div>

        {/* ── 最近一次调用（第 N 轮）── */}
        <div className="mt-3 space-y-1.5 rounded-lg border border-border/40 bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
          <div className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
            <Clock className="size-2.5" />
            最近一次调用{total.turnCount > 0 ? `（第 ${total.turnCount} 轮）` : ''}
          </div>
          {contextWindow > 0 ? (
            <ContextRow
              used={total.lastInputTokens}
              ceiling={contextWindow}
              lastCacheReadTokens={total.lastCacheReadTokens}
              hasDecomposition={hasDecomposition}
              netNew={lastNetNew}
            />
          ) : (
            <Row
              label="当前 ctx"
              value={total.lastInputTokens}
              dim
              hint="最近一次 prompt 大小（单轮，非累计）"
            />
          )}
        </div>

        <div className="mt-2 rounded-md bg-muted/20 px-2.5 py-1.5 text-[10px] leading-relaxed text-muted-foreground">
          累计栏为跨 N 轮的计费维度；单次栏为最近一次调用的 ctx 快照。Pin 消息可避免被预算自动截断。
        </div>

        {/* ── 按 Agent 独立卡片 ── */}
        {showAgentDetails && (
          <div className="mt-3 space-y-2 rounded-lg border border-border/40 bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
            <div className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
              <Users className="size-2.5" />
              按 Agent 明细
            </div>
            {agentEntries.map(([agentId, d]) => (
              <AgentUsageCard
                key={agentId}
                agentName={agents[agentId]?.name ?? agentId}
                model={d.model}
                detail={d}
              />
            ))}
          </div>
        )}

        {/* ── 按 Model ── */}
        {Object.keys(total.byModel).length > 0 && (
          <div className="mt-3 rounded-lg border border-border/40 bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
              <Cpu className="size-2.5" />
              按 Model
            </div>
            {Object.entries(total.byModel)
              .sort((a, b) => b[1] - a[1])
              .map(([modelId, n]) => (
                <Row key={modelId} label={<code className="font-mono">{modelId}</code>} value={n} />
              ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

/** 单个 agent 的 token 用量卡片：名称 + 明细 + cache 命中率进度条。 */
function AgentUsageCard({
  agentName,
  model,
  detail: d,
}: {
  agentName: string
  model?: string
  detail: AgentUsageDetail
}) {
  const rate = computeCacheHitRateFromDetail(d)
  const pct = Math.round(rate)
  const tone = pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'low'
  const barColor =
    tone === 'good' ? 'bg-emerald-500' : tone === 'warn' ? 'bg-amber-500' : 'bg-red-500'

  return (
    <div className="rounded-lg border border-border/40 bg-card p-2 shadow-[var(--shadow-sm)]">
      {/* 标题行：agent 名 + 总 token + 响应数 */}
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <div className="min-w-0">
          <span className="truncate font-medium">{agentName}</span>
          {model && (
            <code className="ml-1.5 text-[9px] text-muted-foreground/70">{model}</code>
          )}
        </div>
        <span className="shrink-0 font-mono text-muted-foreground">
          {formatTok(d.totalTokens)}
          <span className="ml-1 text-[10px]">· {d.runCount} 次</span>
        </span>
      </div>

      {/* 明细行 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
        <span className="flex justify-between">
          <span>Input</span>
          <span className="font-mono">{formatTok(d.inputTokens)}</span>
        </span>
        <span className="flex justify-between">
          <span>Output</span>
          <span className="font-mono">{formatTok(d.outputTokens)}</span>
        </span>
        {d.cacheCreationTokens > 0 && (
          <span className="flex justify-between">
            <span>Cache 写</span>
            <span className="font-mono">{formatTok(d.cacheCreationTokens)}</span>
          </span>
        )}
        {d.cacheReadTokens > 0 && (
          <span className="flex justify-between text-emerald-600 dark:text-emerald-400">
            <span>Cache 命中</span>
            <span className="font-mono">{formatTok(d.cacheReadTokens)}</span>
          </span>
        )}
      </div>

      {/* Cache 命中率进度条 */}
      {d.cacheReadTokens > 0 && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={cn('h-full rounded-full transition-all duration-300', barColor)}
              style={{ width: `${Math.max(pct, 2)}%` }}
            />
          </div>
          <span className="shrink-0 font-mono text-[10px]">{pct}%</span>
        </div>
      )}

      {/* Subagent token roll-up annotation */}
      {d.subagentTokens > 0 && (
        <div className="mt-1 text-[10px] text-muted-foreground/80">
          含 subagent: {formatTok(d.subagentTokens)} · {d.subagentRunCount} 次
        </div>
      )}
    </div>
  )
}

function Row({
  label,
  value,
  highlight,
  bold,
  dim,
  className,
  hint,
}: {
  label: React.ReactNode
  value: number
  highlight?: boolean
  bold?: boolean
  dim?: boolean
  className?: string
  hint?: string
}) {
  return (
    <div
      className={cn(
        'flex items-baseline justify-between gap-3',
        dim && 'text-muted-foreground',
        className,
      )}
    >
      <span className={cn('truncate', bold && 'font-medium')}>{label}</span>
      <span className={cn('shrink-0 font-mono', bold && 'font-semibold')}>
        {formatTok(value)}
        {hint && <span className="ml-1 text-[10px] text-muted-foreground">({hint})</span>}
        {highlight && value === 0 && <span className="ml-1 text-muted-foreground">—</span>}
      </span>
    </div>
  )
}

/** 带 tooltip 的版本，hover 显示计费速率说明 */
function RowWithHint({
  label,
  value,
  tip,
  ...rest
}: {
  label: React.ReactNode
  value: number
  tip: string
  highlight?: boolean
  bold?: boolean
  dim?: boolean
  className?: string
}) {
  return (
    <div title={tip} className="cursor-help">
      <Row label={label} value={value} {...rest} />
    </div>
  )
}

/** 1234 → "1.2k"；1234567 → "1.23M"；< 1000 → 原样 */
function formatTok(n: number): string {
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

/** AgentUsageCard 专用的 cache 命中率：从 detail 的 cacheStyle 调 computeCacheHitRate */
function computeCacheHitRateFromDetail(d: AgentUsageDetail): number {
  return computeCacheHitRate(d.cacheStyle, d.inputTokens, d.cacheCreationTokens, d.cacheReadTokens)
}

/** 费用格式化：CNY ¥ 2 位小数，USD $ 4 位小数（单次会话通常 < $0.01）。 */
function formatCost(cost: number, currency: 'CNY' | 'USD'): string {
  if (currency === 'CNY') return `¥${cost.toFixed(2)}`
  return `$${cost.toFixed(4)}`
}

/**
 * 费用估算行：取 byModel 中 token 用量最大的模型作为主模型，查定价后展示：
 * - 估算费用 ¥X.XX
 * - 无缓存 ¥Y.YY
 * - 省 ¥Z.ZZ (NN%)
 * 无价格数据时整行不渲染（优雅降级）。
 */
function CostEstimateRow({
  byModel,
  byCacheStyle,
}: {
  byModel: Record<string, number>
  byCacheStyle: Record<CacheStyle, CacheStyleBucket>
}) {
  const entries = Object.entries(byModel).sort((a, b) => b[1] - a[1])
  const primaryModel = entries[0]?.[0]
  if (!primaryModel) return null

  const pricing = getModelPricing(null, primaryModel)
  if (!pricing) return null

  let actualCost = 0
  let noCacheCost = 0
  for (const style of ['deepseek', 'anthropic', 'none'] as CacheStyle[]) {
    const bucket = byCacheStyle[style]
    if (!bucket) continue
    const est = computeCost(
      style,
      pricing,
      bucket.inputTokens,
      bucket.cacheReadTokens,
      bucket.cacheCreationTokens,
      bucket.outputTokens,
    )
    actualCost += est.actualCost
    noCacheCost += est.noCacheCost
  }
  const savings = noCacheCost - actualCost
  const savingsPct = noCacheCost > 0 ? (savings / noCacheCost) * 100 : 0
  const currency = pricing.currency

  return (
    <div className="mt-1 space-y-0.5 rounded-md bg-muted/20 px-2 py-1.5" title={`按 ${primaryModel} 官方定价估算（单价 per 1M tokens）`}>
      <div className="flex items-baseline justify-between gap-3 font-medium">
        <span>估算费用</span>
        <span className="font-mono">{formatCost(actualCost, currency)}</span>
      </div>
      <div className="flex items-baseline justify-between gap-3 text-muted-foreground">
        <span className="text-[10px]">无缓存</span>
        <span className="font-mono text-[10px]">{formatCost(noCacheCost, currency)}</span>
      </div>
      {savings > 0 && (
        <div className="flex items-baseline justify-between gap-3 text-emerald-600 dark:text-emerald-400">
          <span className="text-[10px]">省</span>
          <span className="font-mono text-[10px]">
            {formatCost(savings, currency)} ({savingsPct.toFixed(0)}%)
          </span>
        </div>
      )}
    </div>
  )
}

/** Cache 命中率行：展示百分比 + 进度条 + 颜色 + 节省估算。 */
function CacheHitRateRow({ rate, cacheReadTokens }: { rate: number; cacheReadTokens: number }) {
  const pct = Math.round(rate)
  const tone = pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'low'
  const toneColor =
    tone === 'good' ? 'text-emerald-600 dark:text-emerald-400'
      : tone === 'warn' ? 'text-amber-600 dark:text-amber-400'
        : 'text-red-600 dark:text-red-400'
  const barColor =
    tone === 'good' ? 'bg-emerald-500'
      : tone === 'warn' ? 'bg-amber-500'
        : 'bg-red-500'
  // 节省估算：cacheRead tokens 按 90% 折扣省下的 input 计费量
  const savedK = Math.round((cacheReadTokens * 0.9) / 1000)

  return (
    <div className="space-y-1" title="缓存命中率：被缓存复用的 token 占总输入的比例">
      <div className="flex items-baseline justify-between gap-3">
        <span className={cn('truncate', toneColor)}>Cache 命中率</span>
        <span className={cn('shrink-0 font-mono', toneColor)}>
          {pct}%
          {savedK > 0 && (
            <span className="ml-1 text-[10px] text-muted-foreground">
              (省 ~{savedK}k 计费)
            </span>
          )}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn('h-full rounded-full transition-all duration-300', barColor)}
          style={{ width: `${Math.max(pct, 2)}%` }}
        />
      </div>
    </div>
  )
}

/** 当前 ctx 行的特殊版本：展示「used / ceiling (pct%)」+ 进度条 + 颜色 + 拆解子树。 */
function ContextRow({
  used,
  ceiling,
  lastCacheReadTokens,
  hasDecomposition,
  netNew,
}: {
  used: number
  ceiling: number
  lastCacheReadTokens: number
  hasDecomposition: boolean
  netNew: number
}) {
  const hasData = used > 0
  const pct = hasData ? Math.min(100, (used / ceiling) * 100) : 0
  const tone = pct < 50 ? 'normal' : pct < 80 ? 'warn' : 'danger'
  const toneColor =
    tone === 'danger' ? 'text-red-600 dark:text-red-400'
      : tone === 'warn' ? 'text-amber-600 dark:text-amber-400'
        : 'text-muted-foreground'
  const gradientSize = hasData && pct > 0 ? `${10000 / pct}% 100%` : '100% 100%'
  const cachePct = used > 0 ? Math.round((lastCacheReadTokens / used) * 100) : 0

  return (
    <div className="space-y-1" title="最近一次 prompt 大小（单轮，非累计）/ 模型 contextWindow 上限">
      <div className="flex items-baseline justify-between gap-3">
        <span className={cn('truncate', toneColor)}>当前 ctx</span>
        <span className={cn('shrink-0 font-mono', toneColor)}>
          {hasData ? formatTok(used) : '—'} / {formatTok(ceiling)}
          {hasData && ` (${pct.toFixed(0)}%)`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            backgroundImage: 'linear-gradient(90deg, #3370FF 0%, #F59E0B 68%, #EF4444 100%)',
            backgroundSize: gradientSize,
          }}
        />
      </div>
      {hasDecomposition && (
        <div className="pl-2 text-[10px] text-muted-foreground">
          <div className="flex items-baseline justify-between gap-3">
            <span>├ 缓存命中</span>
            <span className="font-mono">
              ~{formatTok(lastCacheReadTokens)}
              {cachePct > 0 && <span className="ml-1">({cachePct}%)</span>}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <span>└ 新内容</span>
            <span className="font-mono">~{formatTok(netNew)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
