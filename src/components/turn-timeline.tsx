'use client'

import { ChevronDown, ChevronRight, Clock, FileCode, Terminal } from 'lucide-react'
import { useMemo, useState } from 'react'

import { formatDuration } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { TurnMetricData } from '@/shared/types'

interface TurnTimelineProps {
  turnMetrics: Record<number, TurnMetricData>
}

function formatTokens(tokens: number): string {
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`
  return String(tokens)
}

function getToolIcon(toolName: string) {
  if (toolName.startsWith('fs_') || toolName === 'write_artifact' || toolName === 'read_artifact') {
    return FileCode
  }
  if (toolName === 'bash') {
    return Terminal
  }
  return FileCode
}

export function TurnTimeline({ turnMetrics }: TurnTimelineProps) {
  const [expanded, setExpanded] = useState(false)

  const turns = useMemo(() => {
    return Object.values(turnMetrics).sort((a, b) => a.turn - b.turn)
  }, [turnMetrics])

  const totals = useMemo(() => {
    const totalTokens = turns.reduce(
      (sum, t) => sum + t.tokens.inputTokens + t.tokens.outputTokens,
      0,
    )
    const totalDuration = turns.reduce((sum, t) => sum + t.durationMs, 0)
    return { totalTokens, totalDuration, count: turns.length }
  }, [turns])

  const averages = useMemo(() => {
    if (turns.length === 0) return { avgDuration: 0, avgTokens: 0 }
    return {
      avgDuration: totals.totalDuration / turns.length,
      avgTokens: totals.totalTokens / turns.length,
    }
  }, [turns, totals])

  if (turns.length === 0) return null

  return (
    <div className="mt-2 select-none">
      {/* Collapsed summary */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex h-6 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/50"
      >
        {expanded ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronRight className="size-3" />
        )}
        <Clock className="size-3" />
        <span>
          {totals.count} turns · {formatTokens(totals.totalTokens)} tokens ·{' '}
          {formatDuration(totals.totalDuration)}
        </span>
      </button>

      {/* Expanded bubbles */}
      {expanded && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {turns.map((turn) => {
            const turnTokens = turn.tokens.inputTokens + turn.tokens.outputTokens
            const isAnomalous =
              turn.durationMs > averages.avgDuration * 2 ||
              turnTokens > averages.avgTokens * 2

            return (
              <div
                key={turn.turn}
                className={cn(
                  'flex min-w-[60px] flex-col items-center gap-0.5 rounded-md border px-2 py-1 text-[10px]',
                  isAnomalous
                    ? 'border-amber-400/50 bg-amber-500/10'
                    : 'border-border bg-muted/30',
                )}
                title={`Turn ${turn.turn}: ${turnTokens} tokens, ${turn.toolCalls.length} tool calls, ${formatDuration(turn.durationMs)}`}
              >
                <span className="font-medium text-foreground">#{turn.turn}</span>
                <span className="text-muted-foreground">{formatTokens(turnTokens)} tok</span>
                <span className="text-muted-foreground">{formatDuration(turn.durationMs)}</span>
                {turn.toolCalls.length > 0 && (
                  <div className="flex gap-0.5">
                    {turn.toolCalls.slice(0, 3).map((toolName, idx) => {
                      const Icon = getToolIcon(toolName)
                      return (
                        <Icon
                          key={idx}
                          className="size-2.5 text-muted-foreground/70"
                        />
                      )
                    })}
                    {turn.toolCalls.length > 3 && (
                      <span className="text-[9px] text-muted-foreground/70">
                        +{turn.toolCalls.length - 3}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
