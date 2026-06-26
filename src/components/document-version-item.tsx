'use client'

import { ChevronDown, ChevronRight, Database, Loader2 } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { VersionRow } from '@/shared/types'

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
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', year: 'numeric' })
}

export function DocumentVersionItem({
  version,
  isLatest,
  onIngest,
}: {
  version: VersionRow
  isLatest: boolean
  onIngest?: (versionId: string) => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  const [ingesting, setIngesting] = useState(false)

  const handleIngest = async () => {
    if (!onIngest) return
    setIngesting(true)
    try {
      await onIngest(version.id)
    } finally {
      setIngesting(false)
    }
  }

  const meta = version.metadata ?? {}
  const parser = (meta.parser as string | undefined) ?? null
  const pages = (meta.pages as number | undefined) ?? null
  const textChars = (meta.textChars as number | undefined) ?? version.contentMd.length
  const filename = (meta.filename as string | undefined) ?? null

  return (
    <div className="rounded-md border border-border/60 transition hover:border-border">
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 text-muted-foreground transition hover:text-foreground"
          title={expanded ? '收起' : '展开'}
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>

        <span className="font-mono text-xs font-medium">v{version.version}</span>

        {isLatest && (
          <Badge variant="secondary" className="text-[10px]">
            最新
          </Badge>
        )}

        <span className="text-[10px] text-muted-foreground">{formatTime(version.createdAt)}</span>

        <div className="ml-auto flex items-center gap-2">
          {filename && (
            <span className="hidden truncate text-[10px] text-muted-foreground sm:inline">
              {filename}
            </span>
          )}
          {parser && (
            <Badge variant="outline" className="text-[10px]">
              {parser}
            </Badge>
          )}
          {pages != null && (
            <span className="text-[10px] text-muted-foreground">{pages} 页</span>
          )}
          <span className="text-[10px] text-muted-foreground">{textChars} 字</span>

          {onIngest && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 gap-1 px-2 text-[10px]"
              onClick={() => void handleIngest()}
              disabled={ingesting}
              title="重新入库到 RAG"
            >
              {ingesting ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Database className="size-3" />
              )}
              {ingesting ? '入库中' : '入库'}
            </Button>
          )}
        </div>
      </div>

      {/* Summary */}
      {version.summary && (
        <div className="px-3 pb-1 text-[11px] text-muted-foreground">
          {version.summary}
        </div>
      )}

      {/* Expandable content */}
      {expanded && (
        <div className="border-t border-border/40 bg-muted/20 px-3 py-2">
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-foreground/80">
            {version.contentMd}
          </pre>
        </div>
      )}
    </div>
  )
}
