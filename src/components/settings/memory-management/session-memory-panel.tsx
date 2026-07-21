'use client'

import { ChevronDown, ChevronRight, Loader2, MessageSquareText } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  type SessionMemoryItem,
  fetchSessionMemories,
} from '@/lib/api/memory'
import { cn } from '@/lib/utils'

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
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">共 {items.length} 个会话摘要</span>
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : null}
          刷新
        </Button>
      </div>

      {/* Accordion list */}
      <div className="flex flex-col gap-2">
        {items.map((item) => {
          const expanded = expandedId === item.conversationId
          return (
            <div
              key={item.conversationId}
              className={cn(
                'overflow-hidden rounded-lg border bg-card shadow-[var(--shadow-sm)] transition-all duration-150',
                expanded ? 'border-primary/30 shadow-[var(--shadow-md)]' : 'hover:border-primary/20',
              )}
            >
              <button
                type="button"
                className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition hover:bg-accent/50"
                onClick={() => setExpandedId(expanded ? null : item.conversationId)}
              >
                {expanded ? (
                  <ChevronDown className="size-3.5 shrink-0 text-primary" />
                ) : (
                  <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                )}
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                  {item.title || item.conversationId}
                </span>
                <span className="shrink-0 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {new Date(item.createdAt * 1000).toLocaleDateString()}
                </span>
              </button>
              {expanded && (
                <div className="border-t px-3 py-2.5 animate-in fade-in-0 slide-in-from-top-1 duration-200">
                  <p className="whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                    {item.summary}
                  </p>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Empty state */}
      {items.length === 0 && !loading && (
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-muted/60 shadow-[var(--shadow-sm)]">
            <MessageSquareText className="size-5 text-muted-foreground" />
          </div>
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-foreground">暂无会话摘要</p>
            <p className="text-xs text-muted-foreground">会话结束后压缩的上下文摘要会出现在这里</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  )
}
