'use client'

import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  type SessionMemoryItem,
  fetchSessionMemories,
} from '@/lib/api/memory'

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
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          共 {items.length} 个会话摘要
        </span>
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : null}
          刷新
        </Button>
      </div>

      <div className="flex flex-col gap-1">
        {items.map((item) => (
          <div key={item.conversationId} className="rounded-md border">
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent"
              onClick={() =>
                setExpandedId(expandedId === item.conversationId ? null : item.conversationId)
              }
            >
              {expandedId === item.conversationId ? (
                <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1 truncate font-medium">
                {item.title || item.conversationId}
              </span>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {new Date(item.createdAt * 1000).toLocaleDateString()}
              </span>
            </button>
            {expandedId === item.conversationId && (
              <div className="border-t px-3 py-2">
                <p className="whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                  {item.summary}
                </p>
              </div>
            )}
          </div>
        ))}
        {items.length === 0 && !loading && (
          <div className="py-8 text-center text-xs text-muted-foreground">
            暂无会话摘要
          </div>
        )}
      </div>
    </div>
  )
}
