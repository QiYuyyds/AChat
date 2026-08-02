'use client'

import {
  Ban,
  CheckCircle2,
  Circle,
  Loader2,
  X,
  XCircle,
  AlertTriangle,
} from 'lucide-react'
import { useEffect } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { PartList } from '@/components/message-parts'
import { TurnTimeline } from '@/components/turn-timeline'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useAppStore, useSelectedTaskDetail } from '@/stores/app-store'
import type { DispatchTaskStatus } from '@/shared/types'

function StatusIcon({ status }: { status: DispatchTaskStatus }) {
  const base = 'size-3.5 shrink-0'
  if (status === 'pending') return <Circle className={cn(base, 'text-muted-foreground/40')} />
  if (status === 'running') return <Loader2 className={cn(base, 'animate-spin text-warning')} />
  if (status === 'complete') return <CheckCircle2 className={cn(base, 'text-success')} />
  if (status === 'merge_conflict') return <AlertTriangle className={cn(base, 'text-warning')} />
  if (status === 'aborted' || status === 'skipped') return <Ban className={cn(base, 'text-zinc-500')} />
  return <XCircle className={cn(base, 'text-destructive')} />
}

export function TaskDetailPanel() {
  const convId = useAppStore((s) => s.activeConversationId)
  const selectedTaskId = useAppStore((s) => s.selectedTaskId)
  const setSelectedTaskId = useAppStore((s) => s.setSelectedTaskId)
  const agents = useAppStore((s) => s.agents)

  const { task, childRunId, messages, turnMetrics, dispatch } = useSelectedTaskDetail(convId ?? '')

  useEffect(() => {
    if (!selectedTaskId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedTaskId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedTaskId, setSelectedTaskId])

  if (!selectedTaskId || !convId) return null

  if (!task || !dispatch) {
    return (
      <aside className="flex w-96 shrink-0 flex-col border-l bg-card max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full max-md:animate-in max-md:slide-in-from-right max-md:duration-200">
        <header className="flex shrink-0 items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">任务详情</span>
          <Button
            size="icon"
            variant="ghost"
            onClick={() => setSelectedTaskId(null)}
            title="关闭"
            aria-label="关闭"
          >
            <X className="size-4" />
          </Button>
        </header>
        <div className="flex flex-1 items-center justify-center p-4 text-sm text-muted-foreground">
          未找到任务 <span className="ml-1 font-mono text-xs">{selectedTaskId}</span> 的执行信息
        </div>
      </aside>
    )
  }

  const status: DispatchTaskStatus =
    dispatch.reviewStatus === 'rejected'
      ? 'skipped'
      : (dispatch.taskStatus[task.id] ?? 'pending')
  const agent = agents[task.agentId]

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l bg-card max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full max-md:animate-in max-md:slide-in-from-right max-md:duration-200">
      <header className="shrink-0 border-b px-3 py-2">
        <div className="flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="text-sm font-medium">任务详情</span>
          </div>
          <Button
            size="icon"
            variant="ghost"
            onClick={() => setSelectedTaskId(null)}
            title="关闭"
            aria-label="关闭"
          >
            <X className="size-4" />
          </Button>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <StatusIcon status={status} />
          {agent ? (
            <AgentAvatar agent={agent} size="xs" />
          ) : (
            <div className="size-5 shrink-0 rounded-full bg-muted" />
          )}
          <span className="text-sm font-medium">{agent?.name ?? task.agentId}</span>
          <span className="font-mono text-[10px] text-muted-foreground">{task.id}</span>
        </div>
        <div className="mt-1 line-clamp-3 text-xs text-muted-foreground">
          {task.task}
        </div>
      </header>

      {turnMetrics && Object.keys(turnMetrics).length > 0 && (
        <div className="shrink-0 border-b px-3 py-2">
          <TurnTimeline turnMetrics={turnMetrics} />
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-3">
          {messages.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
              {childRunId ? (
                <>
                  <Loader2 className="mr-1.5 size-3 animate-spin" />
                  等待消息…
                </>
              ) : (
                '任务尚未开始执行'
              )}
            </div>
          ) : (
            messages.map((msg) => (
              <div key={msg.id} className="rounded-md border bg-background/50 p-2">
                <PartList
                  parts={msg.parts}
                  conversationId={convId}
                  messageStatus={msg.status}
                  messageRole={msg.role}
                />
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </aside>
  )
}
