'use client'

import { Check, Loader2, Terminal, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  approvePendingBashCommand,
  fetchPendingBashCommands,
  rejectPendingBashCommand,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore, usePendingBashCommands } from '@/stores/app-store'
import type { PendingBashCommand } from '@/shared/types'

export function PendingBashCommandsPanel({ conversationId }: { conversationId: string }) {
  const pending = usePendingBashCommands(conversationId)
  const setPendingBashCommandsForConversation = useAppStore(
    (s) => s.setPendingBashCommandsForConversation,
  )

  useEffect(() => {
    let cancelled = false
    fetchPendingBashCommands(conversationId)
      .then((list) => {
        if (!cancelled) setPendingBashCommandsForConversation(conversationId, list)
      })
      .catch((err) => {
        console.warn('[PendingBashCommandsPanel] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setPendingBashCommandsForConversation])

  if (pending.length === 0) return null

  return (
    <div className="shrink-0 space-y-2 border-t bg-orange-50/45 px-4 py-2.5 dark:bg-orange-950/10">
      {pending.map((command) => (
        <PendingBashCommandCard
          key={command.id}
          conversationId={conversationId}
          pending={command}
        />
      ))}
    </div>
  )
}

function PendingBashCommandCard({
  conversationId,
  pending,
}: {
  conversationId: string
  pending: PendingBashCommand
}) {
  const agent = useAppStore((s) => s.agents[pending.agentId])
  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)

  const handleApprove = useCallback(async () => {
    setBusy('approve')
    setError(null)
    try {
      await approvePendingBashCommand(conversationId, pending.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }, [conversationId, pending.id])

  const handleReject = useCallback(async () => {
    setBusy('reject')
    setError(null)
    try {
      await rejectPendingBashCommand(conversationId, pending.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }, [conversationId, pending.id])

  return (
    <div
      className={cn(
        'flex items-center gap-3 rounded-lg border border-orange-200 bg-card px-3 py-2 text-xs shadow-sm',
        'dark:border-orange-900/50',
      )}
    >
      <div className="flex shrink-0 items-center gap-2">
        {agent ? <AgentAvatar agent={agent} size="sm" /> : <div className="size-6 rounded-md bg-muted" />}
        <Terminal className="size-4 text-orange-600" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 font-medium">{agent?.name ?? 'Agent'}</span>
          <span className="shrink-0 text-muted-foreground">请求执行命令</span>
          <code className="truncate font-mono text-[11px]">{pending.command}</code>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
          <span className="truncate">原因：{pending.reason}</span>
          <span>·</span>
          <span className="truncate font-mono">{pending.cwd}</span>
          {error && <span className="text-destructive">· {error}</span>}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          size="sm"
          variant="ghost"
          onClick={handleReject}
          disabled={!!busy}
          className="h-7 px-2.5 text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:hover:bg-rose-950/30"
          title="拒绝"
        >
          {busy === 'reject' ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
          拒绝
        </Button>
        <Button
          size="sm"
          onClick={handleApprove}
          disabled={!!busy}
          className="h-7 bg-[#3370FF] px-2.5 text-white hover:bg-[#2860e5]"
          title="执行"
        >
          {busy === 'approve' ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
          执行
        </Button>
      </div>
    </div>
  )
}
