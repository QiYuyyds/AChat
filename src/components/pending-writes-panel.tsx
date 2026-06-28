'use client'

import { Check, FilePlus2, FilePenLine, Loader2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  approvePendingWrite as approveApi,
  fetchPendingWrites,
  rejectPendingWrite as rejectApi,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore, usePendingWrites } from '@/stores/app-store'
import type { PendingWrite } from '@/shared/types'

const DIFF_TAB_PREFIX = 'diff:'

/**
 * PendingWritesPanel —— 对话区底部（在 MessageInput 上方）的待审批 fs_write 列表。
 *
 * 每条 pending 渲染一张紧凑卡片：路径 / +M-N 行 / 三个按钮。
 * 「查看更改」打开中间区的 diff tab；「应用」「拒绝」直接走 API。
 *
 * Mount 时拉一次兜底（HMR / 刷新场景），其它时候由 SSE 推。
 */
export function PendingWritesPanel({ conversationId }: { conversationId: string }) {
  const pending = usePendingWrites(conversationId)
  const setPendingWritesForConversation = useAppStore((s) => s.setPendingWritesForConversation)

  useEffect(() => {
    let cancelled = false
    fetchPendingWrites(conversationId)
      .then((list) => {
        if (!cancelled) setPendingWritesForConversation(conversationId, list)
      })
      .catch((err) => {
        console.warn('[PendingWritesPanel] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setPendingWritesForConversation])

  if (pending.length === 0) return null

  return (
    <div className="shrink-0 space-y-2 border-t bg-warning/10 px-4 py-2.5">
      {pending.map((p) => (
        <PendingWriteCard key={p.id} conversationId={conversationId} pending={p} />
      ))}
    </div>
  )
}

function PendingWriteCard({
  conversationId,
  pending,
}: {
  conversationId: string
  pending: PendingWrite
}) {
  const agent = useAppStore((s) => s.agents[pending.agentId])
  const openFile = useAppStore((s) => s.openFile)
  const setActiveTab = useAppStore((s) => s.setActiveTab)
  const activeTab = useAppStore((s) => s.activeTabByConv[conversationId] ?? 'chat')

  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)

  const isNew = pending.oldContent === null
  const tabId = `${DIFF_TAB_PREFIX}${pending.id}`
  const isViewing = activeTab === tabId

  const { added, removed } = useMemo(() => countDiffLines(pending.oldContent, pending.newContent), [
    pending.oldContent,
    pending.newContent,
  ])

  const openDiff = () => {
    openFile(conversationId, tabId)
    setActiveTab(conversationId, tabId)
  }

  const handleApprove = useCallback(async () => {
    setBusy('approve')
    setError(null)
    try {
      await approveApi(conversationId, pending.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
    // 成功后 SSE 会移除，组件随之卸载，不需要 reset busy
  }, [conversationId, pending.id])

  const handleReject = useCallback(async () => {
    setBusy('reject')
    setError(null)
    try {
      await rejectApi(conversationId, pending.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }, [conversationId, pending.id])

  return (
    <div
      className={cn(
        'flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-xs shadow-sm transition',
        isViewing && 'border-primary/50 ring-1 ring-primary/20',
      )}
    >
      <div className="flex shrink-0 items-center gap-2">
        {agent ? (
          <AgentAvatar agent={agent} size="sm" />
        ) : (
          <div className="size-6 rounded-md bg-muted" />
        )}
        {isNew ? (
          <FilePlus2 className="size-4 text-success" />
        ) : (
          <FilePenLine className="size-4 text-primary" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 font-medium">{agent?.name ?? 'Agent'}</span>
          <span className="shrink-0 text-muted-foreground">想{isNew ? '创建' : '修改'}</span>
          <code className="truncate font-mono text-[11px]">{pending.path}</code>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
          {added > 0 && <span className="font-mono text-success">+{added}</span>}
          {removed > 0 && <span className="font-mono text-destructive">−{removed}</span>}
          {added === 0 && removed === 0 && <span>无内容变化</span>}
          <span>·</span>
          <span>等待审批</span>
          {error && <span className="text-destructive">· {error}</span>}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          size="sm"
          variant={isViewing ? 'default' : 'outline'}
          onClick={openDiff}
          className="h-7 px-2.5"
        >
          {isViewing ? '查看中' : '查看更改'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleReject}
          disabled={!!busy}
          className="h-7 px-2.5 text-destructive hover:bg-destructive/10"
          title="拒绝"
        >
          {busy === 'reject' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <X className="size-3.5" />
          )}
          拒绝
        </Button>
        <Button
          size="sm"
          onClick={handleApprove}
          disabled={!!busy}
          className="h-7 bg-primary px-2.5 text-primary-foreground hover:bg-primary/90"
          title="应用"
        >
          {busy === 'approve' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Check className="size-3.5" />
          )}
          应用
        </Button>
      </div>
    </div>
  )
}

function countDiffLines(oldContent: string | null, newContent: string): { added: number; removed: number } {
  const oldLines = oldContent === null ? [] : oldContent.split('\n')
  const newLines = newContent.split('\n')
  // 粗略统计：差集计数；够看小变更，复杂场景以 diff viewer 为准
  const oldSet = new Map<string, number>()
  for (const l of oldLines) oldSet.set(l, (oldSet.get(l) ?? 0) + 1)
  let added = 0
  for (const l of newLines) {
    const c = oldSet.get(l) ?? 0
    if (c > 0) oldSet.set(l, c - 1)
    else added++
  }
  let removed = 0
  for (const c of oldSet.values()) removed += c
  return { added, removed }
}

/** 给外部判断 tab id 是否是 pending diff（避免硬编码 prefix 字符串）。 */
export function isDiffTabId(tabId: string): boolean {
  return tabId.startsWith(DIFF_TAB_PREFIX)
}

export function diffTabPendingId(tabId: string): string {
  return tabId.slice(DIFF_TAB_PREFIX.length)
}
