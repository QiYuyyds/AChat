'use client'

import { CheckCircle2, CircleAlert, LoaderCircle, Network, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { CodeIntelligenceSwitch } from '@/components/code-intelligence-switch'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  fetchCodeIntelligenceStatus,
  runCodeIntelligenceAction,
  type CodeIntelligenceStatusRecord,
} from '@/lib/api'
import {
  buildCodeIntelligenceDetailRows,
  getCodeIntelligenceActions,
  getCodeIntelligencePanelSummary,
  getCodeIntelligenceProgress,
  getCodeIntelligenceStatusVisual,
  getCodeIntelligenceTransitionNotice,
  isCodeIntelligenceSwitchOn,
  performCodeIntelligenceToggle,
  scheduleCodeIntelligenceNoticeDismiss,
  shouldPollCodeIntelligence,
  startCodeIntelligencePolling,
  type CodeIntelligenceNotice,
  type CodeIntelligencePanelAction,
  type CodeIntelligenceStatus,
} from '@/lib/code-intelligence'
import { cn } from '@/lib/utils'

const TONE_CLASS = {
  disabled: 'text-muted-foreground',
  building: 'text-primary',
  ready: 'text-success',
  stale: 'text-warning',
  failed: 'text-destructive',
  interrupted: 'text-destructive',
} as const

const SUMMARY_CLASS = {
  neutral: 'border-border bg-muted/60 text-muted-foreground',
  working: 'border-primary/30 bg-primary/10 text-primary',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  error: 'border-destructive/30 bg-destructive/10 text-destructive',
} as const

const ACTION_LABEL: Record<CodeIntelligencePanelAction, string> = {
  cancel: '取消任务',
  retry: '重试',
  sync: '立即同步',
  rebuild: '重建索引',
}

const ACTION_STATUS: Record<CodeIntelligencePanelAction, CodeIntelligenceStatusRecord['status']> = {
  cancel: 'cancelling',
  retry: 'preparing_runtime',
  sync: 'syncing',
  rebuild: 'rebuilding',
}

export function CodeIntelligenceControl({ conversationId }: { conversationId: string }) {
  const [status, setStatus] = useState<CodeIntelligenceStatusRecord | null>(null)
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<CodeIntelligenceNotice | null>(null)
  const previousStatus = useRef<CodeIntelligenceStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    setStatus(null)
    setActionError(null)
    setNotice(null)
    previousStatus.current = null
    fetchCodeIntelligenceStatus(conversationId)
      .then((next) => {
        if (!cancelled) setStatus(next)
      })
      .catch((error) => {
        console.warn('[CodeIntelligence] fetch status failed', error)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId])

  useEffect(() => {
    const current = (status?.status ?? 'disabled') as CodeIntelligenceStatus
    if (!shouldPollCodeIntelligence(open, current)) return

    let cancelled = false
    const stop = startCodeIntelligencePolling(async () => {
      try {
        const next = await fetchCodeIntelligenceStatus(conversationId)
        if (!cancelled) setStatus(next)
      } catch (error) {
        if (!cancelled) console.warn('[CodeIntelligence] poll status failed', error)
      }
    })
    return () => {
      cancelled = true
      stop()
    }
  }, [conversationId, open, status?.status])
  const statusValue = status?.status

  useEffect(() => {
    if (!statusValue) return
    const current = statusValue as CodeIntelligenceStatus
    const nextNotice = getCodeIntelligenceTransitionNotice(previousStatus.current, current)
    previousStatus.current = current
    if (!nextNotice) return
    setNotice(nextNotice)
  }, [statusValue])

  useEffect(() => {
    if (!notice) return
    return scheduleCodeIntelligenceNoticeDismiss(() => setNotice(null))
  }, [notice])

  const visual = getCodeIntelligenceStatusVisual(
    (status?.status ?? 'disabled') as CodeIntelligenceStatus,
  )
  const Icon = visual.spinning ? LoaderCircle : Network
  const progress = getCodeIntelligenceProgress(
    (status?.status ?? 'disabled') as CodeIntelligenceStatus,
    status?.phase ?? null,
    status?.progressPercent ?? null,
  )
  const summary = getCodeIntelligencePanelSummary(
    (status?.status ?? 'disabled') as CodeIntelligenceStatus,
  )
  const rows = status ? buildCodeIntelligenceDetailRows(status) : []
  const switchOn = status ? isCodeIntelligenceSwitchOn(status) : false
  const actions = getCodeIntelligenceActions(
    (status?.status ?? 'disabled') as CodeIntelligenceStatus,
  )

  const toggleEnabled = async () => {
    if (!status || pending) return
    setActionError(null)
    const result = await performCodeIntelligenceToggle({
      enabled: status.enabled,
      confirm: (message) => window.confirm(message),
      run: async (action) => runCodeIntelligenceAction(conversationId, action),
      onPendingChange: setPending,
    })
    if (result.error) {
      setActionError(result.error)
      return
    }
    if (!result.cancelled) {
      setStatus((current) => current ? {
        ...current,
        enabled: result.enabled,
        status: result.enabled ? 'preparing_runtime' : 'disabled',
        phase: result.enabled ? '正在准备运行时' : null,
        progressPercent: result.enabled ? 0 : null,
        error: null,
      } : current)
    }
  }

  const runAction = async (action: CodeIntelligencePanelAction) => {
    if (!status || pending) return
    setPending(true)
    setActionError(null)
    try {
      await runCodeIntelligenceAction(conversationId, action)
      setStatus((current) => current ? {
        ...current,
        status: ACTION_STATUS[action],
        phase: ACTION_LABEL[action],
        progressPercent: action === 'cancel' ? null : 0,
        error: null,
      } : current)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error))
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={visual.label}
              title={visual.label}
              className={TONE_CLASS[visual.tone]}
            />
          }
        >
          <Icon className={cn('size-4', visual.spinning && 'animate-spin')} />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[min(calc(100vw-2rem),22rem)] gap-0 p-0">
          <div className="flex items-center justify-between border-b px-3 py-2.5">
            <span className="text-sm font-medium">源码智能</span>
            <CodeIntelligenceSwitch
              checked={switchOn}
              disabled={!status || pending}
              label="源码智能"
              onClick={() => void toggleEnabled()}
            />
          </div>
          {!progress.active && <div className="border-b px-3 py-2.5">
            <div
              className={cn(
                'flex items-center gap-2 rounded-md border px-2.5 py-2 text-xs font-medium',
                SUMMARY_CLASS[summary.tone],
              )}
            >
              {summary.tone === 'error' ? (
                <CircleAlert className="size-4 shrink-0" />
              ) : summary.tone === 'success' ? (
                <CheckCircle2 className="size-4 shrink-0" />
              ) : (
                <Icon className={cn('size-4 shrink-0', progress.active && 'animate-spin')} />
              )}
              <span>{summary.label}</span>
            </div>
          </div>}
          {actionError && (
            <div className="border-b bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {actionError}
            </div>
          )}
          {status?.error && (
            <div className="border-b px-3 py-2.5">
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-2.5 py-2 text-xs leading-5 text-destructive">
                {status.error}
              </div>
            </div>
          )}
          {progress.active && (
            <div className="space-y-1.5 border-b px-3 py-2.5" role="status">
              <div className="flex items-center justify-between text-xs">
                <span>{progress.label}</span>
                <span className="font-medium text-primary">{progress.percent}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-300"
                  style={{ width: `${progress.percent}%` }}
                />
              </div>
            </div>
          )}
          {status ? (
            <dl className="space-y-2 px-3 py-2.5 text-xs">
              {rows.map((row) => (
                <div key={row.label} className="grid grid-cols-[4.5rem_1fr] gap-2">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd
                    className={cn(
                      'min-w-0 break-words text-right',
                      row.label === '错误' && status.error && 'text-destructive',
                    )}
                  >
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="px-3 py-4 text-center text-xs text-muted-foreground">正在读取状态…</div>
          )}
          {actions.length > 0 && (
            <div className="flex flex-wrap justify-end gap-2 border-t px-3 py-2.5">
              {actions.map((action) => (
                <Button
                  key={action}
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={() => void runAction(action)}
                >
                  {ACTION_LABEL[action]}
                </Button>
              ))}
            </div>
          )}
        </PopoverContent>
      </Popover>
      {notice && (
        <div
          role={notice.tone === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={cn(
            'fixed left-1/2 top-4 z-[200] flex w-[min(calc(100vw-2rem),28rem)] -translate-x-1/2 items-start gap-3 rounded-xl border-2 bg-background px-4 py-3 text-sm shadow-xl animate-in fade-in-0 slide-in-from-top-2',
            notice.tone === 'success'
              ? 'border-success/50 bg-success/10 text-success'
              : 'border-destructive/50 bg-destructive/10 text-destructive',
          )}
        >
          {notice.tone === 'success' ? (
            <CheckCircle2 className="mt-0.5 size-5 shrink-0" />
          ) : (
            <CircleAlert className="mt-0.5 size-5 shrink-0" />
          )}
          <div className="min-w-0 flex-1">
            <div className="font-semibold">{notice.message}</div>
            <div className="mt-0.5 text-xs opacity-80">
              {notice.tone === 'success'
                ? '代码关系图现在可以使用'
                : '请打开源码智能面板查看详情并重试'}
            </div>
          </div>
          <button
            type="button"
            aria-label="关闭通知"
            onClick={() => setNotice(null)}
            className="rounded-md p-1 opacity-70 transition hover:bg-black/5 hover:opacity-100"
          >
            <X className="size-4" />
          </button>
        </div>
      )}
    </>
  )
}
