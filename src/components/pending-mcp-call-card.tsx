'use client'

import { Check, Loader2, Plug, ShieldAlert, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  approvePendingMcpCall as approveApi,
  fetchPendingMcpCalls,
  rejectPendingMcpCall as rejectApi,
} from '@/lib/api'
import { useAppStore, usePendingMcpCalls } from '@/stores/app-store'
import type { PendingMcpCall } from '@/shared/types'

/**
 * PendingMcpCallsPanel — 对话区底部（在 PendingWritesPanel 上方）的待审批 MCP 工具调用列表。
 *
 * trust='ask' 的 MCP server 工具首次调用时触发。每条渲染一张紧凑卡片：
 * 工具名 / 参数预览 / Approve / Reject。
 *
 * Mount 时拉一次兜底（HMR / 刷新场景），其它时候由 SSE 推。
 */
export function PendingMcpCallsPanel({ conversationId }: { conversationId: string }) {
  const pending = usePendingMcpCalls(conversationId)
  const setPendingMcpCallsForConversation = useAppStore((s) => s.setPendingMcpCallsForConversation)

  useEffect(() => {
    let cancelled = false
    fetchPendingMcpCalls(conversationId)
      .then((list) => {
        if (!cancelled) setPendingMcpCallsForConversation(conversationId, list)
      })
      .catch((err) => {
        console.warn('[PendingMcpCallsPanel] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setPendingMcpCallsForConversation])

  if (pending.length === 0) return null

  return (
    <div className="shrink-0 space-y-2 border-t bg-warning/10 px-4 py-2.5">
      {pending.map((p) => (
        <PendingMcpCallCard key={p.id} conversationId={conversationId} pending={p} />
      ))}
    </div>
  )
}

function PendingMcpCallCard({
  conversationId,
  pending,
}: {
  conversationId: string
  pending: PendingMcpCall
}) {
  const agent = useAppStore((s) => s.agents[pending.agentId])

  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)

  const handleApprove = useCallback(async () => {
    setBusy('approve')
    setError(null)
    try {
      await approveApi(conversationId, pending.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
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

  const argsPreview = formatArgsPreview(pending.args)

  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-xs shadow-sm">
      <div className="flex shrink-0 items-center gap-2">
        {agent ? (
          <AgentAvatar agent={agent} size="sm" />
        ) : (
          <div className="size-6 rounded-md bg-muted" />
        )}
        <Plug className="size-4 text-primary" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 font-medium">{agent?.name ?? 'Agent'}</span>
          <span className="shrink-0 text-muted-foreground">想调用 MCP 工具</span>
          <code className="truncate font-mono text-[11px]">{pending.toolName}</code>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
          {argsPreview && (
            <span className="truncate font-mono" title={argsPreview}>
              {argsPreview}
            </span>
          )}
          <span>·</span>
          <span className="inline-flex items-center gap-0.5">
            <ShieldAlert className="size-2.5" />
            trust: {pending.serverTrust}
          </span>
          <span>·</span>
          <span>等待审批</span>
          {error && <span className="text-destructive">· {error}</span>}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
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
          title="批准"
        >
          {busy === 'approve' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Check className="size-3.5" />
          )}
          批准
        </Button>
      </div>
    </div>
  )
}

function formatArgsPreview(args: Record<string, unknown>): string {
  const entries = Object.entries(args)
  if (entries.length === 0) return ''
  const parts = entries.slice(0, 3).map(([k, v]) => {
    const val = typeof v === 'string' ? v : JSON.stringify(v)
    const truncated = val.length > 40 ? val.slice(0, 40) + '…' : val
    return `${k}: ${truncated}`
  })
  if (entries.length > 3) parts.push(`…+${entries.length - 3}`)
  return parts.join(', ')
}
