'use client'

import { Ban, Check, CheckCircle2, Circle, Loader2, Network, RotateCw, X, XCircle } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { approvePendingDispatchPlan, rejectPendingDispatchPlan } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DispatchState } from '@/stores/app-store'
import { useAppStore } from '@/stores/app-store'
import type { DispatchTaskStatus } from '@/shared/types'

interface DispatchPlanCardProps {
  conversationId: string
  dispatch: DispatchState
}

export function DispatchPlanCard({ conversationId, dispatch }: DispatchPlanCardProps) {
  if (dispatch.reviewStatus === 'pending' && dispatch.pendingPlanId) {
    return (
      <DispatchPlanReviewCard
        conversationId={conversationId}
        dispatch={dispatch}
        pendingPlanId={dispatch.pendingPlanId}
      />
    )
  }

  return <DispatchPlanReadOnlyCard dispatch={dispatch} />
}

function DispatchPlanReviewCard({
  conversationId,
  dispatch,
  pendingPlanId,
}: {
  conversationId: string
  dispatch: DispatchState
  pendingPlanId: string
}) {
  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)

  const handleApprove = async () => {
    if (busy) return
    setBusy('approve')
    setError(null)
    try {
      await approvePendingDispatchPlan(conversationId, pendingPlanId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  const handleReject = async () => {
    if (busy) return
    setBusy('reject')
    setError(null)
    try {
      await rejectPendingDispatchPlan(conversationId, pendingPlanId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <Card className="overflow-hidden border-warning/40 bg-warning/10 animate-in fade-in slide-in-from-top-2 duration-300">
      <div className="space-y-3 p-3">
        <div className="flex items-center gap-2">
          <Network className="size-4 text-warning" />
          <div className="text-sm font-medium">计划待确认 · {dispatch.plan.length} 项</div>
          <div className="ml-auto flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void handleReject()}
              disabled={!!busy}
              className="h-7 px-2 text-destructive hover:bg-destructive/10"
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
              onClick={() => void handleApprove()}
              disabled={!!busy}
              className="h-7 bg-primary px-2 text-primary-foreground hover:bg-primary/90"
            >
              {busy === 'approve' ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Check className="size-3.5" />
              )}
              执行计划
            </Button>
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
            {error}
          </div>
        )}

        <PlanTaskList dispatch={dispatch} />

        <div className="text-[11px] leading-5 text-muted-foreground">
          想改计划？在下方对话框直接说，例如「把 t2 改成依赖 t1」「设计任务交给后端 agent」——Orchestrator
          会据此重排，再给你确认。
        </div>
      </div>
    </Card>
  )
}

/** 计划任务行列表（只读展示，审批卡与执行卡共用）。 */
function PlanTaskList({ dispatch }: { dispatch: DispatchState }) {
  const agents = useAppStore((s) => s.agents)
  return (
    <div className="space-y-1.5">
      {dispatch.plan.map((task, idx) => {
        const status =
          dispatch.reviewStatus === 'rejected'
            ? 'skipped'
            : (dispatch.taskStatus[task.id] ?? 'pending')
        const agent = agents[task.agentId]
        const inputRefs = task.inputs?.map((input) => `${input.fromTaskId}.${input.outputId}`) ?? []
        const outputRefs =
          task.expectedOutputs?.map((output) => `${output.id}:${output.type}`) ?? []
        const criteriaCount = task.acceptanceCriteria?.length ?? 0
        return (
          <div
            key={task.id}
            style={{ animationDelay: `${idx * 60}ms` }}
            className={cn(
              'flex items-start gap-2 rounded-md border bg-card px-2 py-1.5 text-xs',
              'animate-in fade-in slide-in-from-left-2 fill-mode-both duration-300',
              'transition-[border-color,box-shadow,background-color] duration-300',
              status === 'running' &&
                'border-warning/40 bg-warning/10 ring-2 ring-warning/30',
              status === 'complete' && 'border-emerald-200 dark:border-emerald-900/40',
              status === 'failed' && 'border-destructive/40',
              status === 'aborted' && 'border-zinc-300 bg-zinc-50/50 dark:border-zinc-700',
              status === 'skipped' && 'border-zinc-200 bg-muted/40 dark:border-zinc-800',
            )}
          >
            <StatusIcon status={status} />
            {agent ? (
              <AgentAvatar
                agent={agent}
                size="xs"
                className={cn(
                  'transition-transform',
                  status === 'running' && 'scale-110 ring-2 ring-amber-300 ring-offset-1',
                )}
              />
            ) : (
              <div className="size-5 shrink-0 rounded-full bg-muted" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-medium">{agent?.name ?? task.agentId}</span>
                <span className="font-mono text-[10px] text-muted-foreground">{task.id}</span>
                {task.dependsOn && task.dependsOn.length > 0 && (
                  <span className="text-[10px] text-muted-foreground">
                    → {task.dependsOn.join(', ')}
                  </span>
                )}
                {status === 'running' && <TypingDots />}
                {dispatch.retryInfo?.[task.id] && (
                  <RetryBadge info={dispatch.retryInfo[task.id]} />
                )}
              </div>
              <PlanTaskMarkdown>{task.task}</PlanTaskMarkdown>
              {(inputRefs.length > 0 || outputRefs.length > 0 || criteriaCount > 0) && (
                <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                  {inputRefs.length > 0 && (
                    <span className="rounded border bg-muted/40 px-1 py-0.5">
                      in {inputRefs.join(', ')}
                    </span>
                  )}
                  {outputRefs.length > 0 && (
                    <span className="rounded border bg-muted/40 px-1 py-0.5">
                      out {outputRefs.join(', ')}
                    </span>
                  )}
                  {criteriaCount > 0 && (
                    <span className="rounded border bg-muted/40 px-1 py-0.5">
                      checks {criteriaCount}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function PlanTaskMarkdown({ children }: { children: string }) {
  return (
    <div className="mt-0.5 line-clamp-2 text-muted-foreground">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children: node }) => <span>{node}</span>,
          strong: ({ children: node }) => (
            <strong className="font-semibold text-foreground/90">{node}</strong>
          ),
          em: ({ children: node }) => <em className="italic">{node}</em>,
          code: ({ children: node }) => (
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-foreground/80">
              {node}
            </code>
          ),
          a: ({ href, children: node }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline-offset-2 hover:underline"
            >
              {node}
            </a>
          ),
          ul: ({ children: node }) => <span>{node}</span>,
          ol: ({ children: node }) => <span>{node}</span>,
          li: ({ children: node }) => <span>{node} </span>,
          br: () => <span> </span>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

function DispatchPlanReadOnlyCard({ dispatch }: { dispatch: DispatchState }) {
  const total = dispatch.plan.length
  const displayStatuses = dispatch.plan.map((task) =>
    dispatch.reviewStatus === 'rejected' ? 'skipped' : (dispatch.taskStatus[task.id] ?? 'pending'),
  )
  const done = displayStatuses.filter(isTerminalStatus).length
  const allDone = total > 0 && done === total
  const hasFailed = displayStatuses.some((s) => s === 'failed' || s === 'aborted')
  const hasSkipped = displayStatuses.some((s) => s === 'skipped')
  const progress = total > 0 ? Math.round((done / total) * 100) : 0
  const rejected = dispatch.reviewStatus === 'rejected'

  return (
    <Card
      className={cn(
        'overflow-hidden border-primary/20 bg-primary/5 animate-in fade-in slide-in-from-top-2 duration-300',
        rejected && 'border-zinc-300 bg-muted/40 dark:border-zinc-800',
      )}
    >
      <div className="space-y-3 p-3">
        <div className="flex items-center gap-2">
          {rejected ? (
            <Ban className="size-4 text-zinc-500" />
          ) : (
            <Network className={cn('size-4 text-primary', !allDone && 'animate-pulse')} />
          )}
          <div className="text-sm font-medium">
            {rejected ? '计划已取消' : '任务拆解'} · {total} 项
          </div>
          <div className="ml-auto text-xs text-muted-foreground">
            {done} / {total}
          </div>
        </div>

        <div className="h-1 overflow-hidden rounded-full bg-primary/10">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-500 ease-out',
              allDone && hasFailed && 'bg-destructive',
              allDone && !hasFailed && hasSkipped && 'bg-zinc-400',
              allDone && !hasFailed && !hasSkipped && 'bg-success',
              !allDone && !rejected && 'bg-primary',
              rejected && 'bg-zinc-400',
            )}
            style={{ width: `${progress}%` }}
          />
        </div>

        <PlanTaskList dispatch={dispatch} />
      </div>
    </Card>
  )
}

function isTerminalStatus(status: DispatchTaskStatus): boolean {
  return status === 'complete' || status === 'failed' || status === 'aborted' || status === 'skipped'
}

function StatusIcon({ status }: { status: DispatchTaskStatus }) {
  const base = 'mt-0.5 size-3.5 shrink-0 transition-colors'
  if (status === 'pending') {
    return <Circle className={cn(base, 'text-muted-foreground/40')} />
  }
  if (status === 'running') {
    return <Loader2 className={cn(base, 'animate-spin text-warning')} />
  }
  if (status === 'complete') {
    return (
      <CheckCircle2
        className={cn(base, 'text-success animate-in zoom-in-50 duration-300')}
      />
    )
  }
  if (status === 'aborted' || status === 'skipped') {
    return <Ban className={cn(base, 'text-zinc-500 animate-in zoom-in-50 duration-300')} />
  }
  return <XCircle className={cn(base, 'text-destructive animate-in zoom-in-50 duration-300')} />
}

function RetryBadge({
  info,
}: {
  info: { attempt: number; maxAttempts: number; error?: string }
}) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning"
      title={info.error ?? undefined}
    >
      <RotateCw className="size-2.5 animate-spin" />
      重试 {info.attempt}/{info.maxAttempts}
    </span>
  )
}

function TypingDots() {
  return (
    <span className="ml-0.5 inline-flex items-center gap-0.5">
      <span className="size-1 animate-bounce rounded-full bg-warning [animation-delay:-0.3s]" />
      <span className="size-1 animate-bounce rounded-full bg-warning [animation-delay:-0.15s]" />
      <span className="size-1 animate-bounce rounded-full bg-warning" />
    </span>
  )
}
