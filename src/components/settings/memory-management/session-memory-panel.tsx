'use client'

import { ChevronDown, ChevronRight, Loader2, MessageSquareText, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  type SessionMemoryItem,
  fetchSessionMemories,
} from '@/lib/api/memory'
import { cn } from '@/lib/utils'

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  if (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  ) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export function SessionMemoryPanel() {
  const [items, setItems] = useState<SessionMemoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetchSessionMemories()
      setItems(resp.items)
    } catch (err) {
      console.error('[SessionMemoryPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="cognition-fade-up flex items-center justify-between">
        <span className="text-xs tabular-nums text-muted-foreground">共 {items.length} 个会话摘要</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void load()}
          disabled={loading}
          className="h-8 gap-1 text-xs"
        >
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          刷新
        </Button>
      </div>

      {/* Timeline list */}
      {items.length > 0 && (
        <div className="relative">
          {/* Vertical timeline connector */}
          <div className="absolute left-[15px] top-2 bottom-2 w-px bg-border" />

          <div className="space-y-1.5">
            {items.map((item, index) => {
              const expanded = expandedId === item.conversationId
              return (
                <div
                  key={item.conversationId}
                  className={cn(
                    'cognition-fade-up relative overflow-hidden rounded-lg border bg-card shadow-[var(--shadow-sm)] transition-all duration-200',
                    expanded
                      ? 'border-primary/30 shadow-[var(--shadow-md)]'
                      : 'hover:border-primary/20',
                  )}
                  style={{ animationDelay: `${index * 40}ms` }}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition hover:bg-accent/50"
                    onClick={() => setExpandedId(expanded ? null : item.conversationId)}
                  >
                    {/* Timeline dot */}
                    <div
                      className={cn(
                        'relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border-2 transition-all duration-200',
                        expanded
                          ? 'border-primary bg-primary/10'
                          : 'border-border bg-background',
                      )}
                    >
                      {expanded ? (
                        <ChevronDown className="size-3 text-primary" />
                      ) : (
                        <ChevronRight className="size-3 text-muted-foreground" />
                      )}
                    </div>

                    <span className={cn(
                      'min-w-0 flex-1 truncate text-sm font-medium',
                      expanded ? 'text-primary' : 'text-foreground',
                    )}>
                      {item.title || item.conversationId}
                    </span>
                    <span className="shrink-0 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                      {formatTime(item.createdAt)}
                    </span>
                  </button>
                  {expanded && (
                    <div className="tab-content-enter border-t px-3 py-2.5 pl-12">
                      <p className="whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                        {item.summary}
                      </p>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Empty state */}
      {items.length === 0 && !loading && (
        <div className="relative flex flex-col items-center gap-3 py-16 text-center">
          <div className="cognition-ambient pointer-events-none absolute size-40 rounded-full bg-primary/8 blur-3xl" />
          <div className="relative">
            <div className="flex size-14 items-center justify-center rounded-2xl border border-border/50 bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
              <MessageSquareText className="size-6 text-muted-foreground/70 cognition-empty-float" />
            </div>
          </div>
          <div className="cognition-fade-up relative space-y-0.5">
            <p className="text-sm font-semibold text-foreground">暂无会话摘要</p>
            <p className="text-xs text-muted-foreground">会话结束后压缩的上下文摘要会出现在这里</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  )
}
