'use client'

import { Check, FilePlus2, FilePenLine, Loader2, X } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'

import { buildDiffStyles } from '@/components/diff-viewer-styles'
import { Button } from '@/components/ui/button'
import { approvePendingWrite, rejectPendingWrite } from '@/lib/api'
import { useAppStore, usePendingWrites } from '@/stores/app-store'

/**
 * PendingWriteDiffTab —— 中间区的「pending fs_write diff」标签页内容。
 *
 * 用 react-diff-viewer-continued 紧凑 mono 样式渲染 oldContent vs newContent，
 * 底部固定 action bar，提供应用 / 拒绝按钮。
 *
 * Pending 一旦被 resolve（SSE / 用户操作），自动关闭 tab。
 */
export function PendingWriteDiffTab({
  conversationId,
  pendingId,
}: {
  conversationId: string
  pendingId: string
}) {
  const pending = usePendingWrites(conversationId).find((p) => p.id === pendingId)
  const agent = useAppStore((s) => (pending ? s.agents[pending.agentId] : null))
  const closeFile = useAppStore((s) => s.closeFile)
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)

  // pending 不见了（已被 resolve）→ 自动关 tab
  useEffect(() => {
    if (!pending) closeFile(conversationId, `diff:${pendingId}`)
  }, [pending, closeFile, conversationId, pendingId])

  const diffStyles = useMemo(() => buildDiffStyles(isDark), [isDark])

  const handleApprove = useCallback(async () => {
    setBusy('approve')
    setError(null)
    try {
      await approvePendingWrite(conversationId, pendingId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }, [conversationId, pendingId])

  const handleReject = useCallback(async () => {
    setBusy('reject')
    setError(null)
    try {
      await rejectPendingWrite(conversationId, pendingId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }, [conversationId, pendingId])

  if (!pending) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        审批已处理，关闭中...
      </div>
    )
  }

  const isNew = pending.oldContent === null

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b bg-card/30 px-3 py-2 text-xs">
        {isNew ? (
          <FilePlus2 className="size-3.5 shrink-0 text-success" />
        ) : (
          <FilePenLine className="size-3.5 shrink-0 text-primary" />
        )}
        <code className="min-w-0 flex-1 truncate font-mono">{pending.path}</code>
        <span className="shrink-0 text-muted-foreground">
          {agent?.name ?? 'Agent'} 想{isNew ? '创建' : '修改'}
        </span>
      </div>

      {/* Diff body */}
      <div className="min-h-0 flex-1 overflow-auto bg-background pending-diff-body">
        <ReactDiffViewer
          oldValue={pending.oldContent ?? ''}
          newValue={pending.newContent}
          splitView={true}
          useDarkTheme={isDark}
          compareMethod={DiffMethod.WORDS_WITH_SPACE}
          leftTitle={isNew ? '(文件不存在)' : '当前内容'}
          rightTitle="Agent 写入后"
          styles={diffStyles}
        />
      </div>

      {/* Action bar */}
      <div className="flex shrink-0 items-center justify-between gap-3 border-t bg-card/40 px-3 py-2">
        {error ? (
          <span className="font-mono text-xs text-destructive">{error}</span>
        ) : (
          <span className="text-[11px] text-muted-foreground">
            Review 模式 — 应用前可拒绝，或直接 [拒绝] 后到对应文件 tab 自己改
          </span>
        )}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleReject}
            disabled={!!busy}
            className="h-7"
          >
            {busy === 'reject' ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <X className="mr-1 size-3.5" />
            )}
            拒绝
          </Button>
          <Button
            size="sm"
            onClick={handleApprove}
            disabled={!!busy}
            className="h-7 bg-primary text-primary-foreground hover:bg-primary/90"
          >
            {busy === 'approve' ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <Check className="mr-1 size-3.5" />
            )}
            应用
          </Button>
        </div>
      </div>
    </div>
  )
}
