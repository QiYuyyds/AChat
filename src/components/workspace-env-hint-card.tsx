'use client'

import { AlertTriangle, CheckCircle2, Loader2, Package } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { createProjectVenv, fetchWorkspaceEnvStatus, updateEnvPreference } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

/**
 * Workspace 环境提示卡片。
 *
 * 当 workspace 绑定了一个 Python 项目但没有 .venv 时，后端通过
 * `workspace_env_hint` SSE 事件通知前端显示此卡片。用户有三个选择：
 *
 * 1. 「创建 .venv」→ POST create-venv → SSE status creating → ready/failed
 * 2. 「跳过」→ PATCH env-preference=skip → 卡片消失
 * 3. 「使用系统 Python」→ PATCH env-preference=system_python → 卡片消失
 *
 * 卡片挂在 ChatPanel 的 chat tab 视图顶部，PinnedMessagesBar 之下，
 * MessageList 之上。它是 workspace 级别的一次性引导，不属于对话内容。
 */
export function WorkspaceEnvHintCard({ conversationId }: { conversationId: string }) {
  const envState = useAppStore((s) => s.workspaceEnvByConv[conversationId])
  const [skipLoading, setSkipLoading] = useState(false)

  // On mount (or when conversationId changes): fetch the env status so the
  // hint card reappears after a page refresh when the SSE hint was missed.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const status = await fetchWorkspaceEnvStatus(conversationId)
        if (cancelled) return
        // Only show the hint if the project is Python, no venv, and the user
        // hasn't made a choice yet. If the store already has a non-idle state
        // (e.g. venv creation in flight), don't overwrite it.
        const existing = useAppStore.getState().workspaceEnvByConv[conversationId]
        if (existing && existing.status !== 'idle') return
        if (
          status.language === 'python' &&
          !status.venvPresent &&
          status.envPreference === null
        ) {
          useAppStore.setState((s) => {
            s.workspaceEnvByConv[conversationId] = {
              hintVisible: true,
              status: 'idle',
            }
          })
        } else {
          // User already decided or not a Python project — clear any stale hint.
          useAppStore.setState((s) => {
            if (
              s.workspaceEnvByConv[conversationId]?.status === 'idle'
            ) {
              s.workspaceEnvByConv[conversationId] = {
                hintVisible: false,
                status: 'idle',
              }
            }
          })
        }
      } catch {
        // Env status fetch is best-effort; don't block the UI.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [conversationId])

  if (!envState?.hintVisible) return null

  const handleCreate = async () => {
    try {
      await createProjectVenv(conversationId)
      // SSE events (creating → ready/failed) drive the UI update.
    } catch {
      // The create-venv endpoint returns 202 immediately; if it fails,
      // the backend emits a 'failed' status event. Network errors are
      // unlikely here (same origin), and the user can retry.
    }
  }

  const handleSkip = async (preference: 'skip' | 'system_python') => {
    setSkipLoading(true)
    try {
      await updateEnvPreference(conversationId, preference)
      // Optimistically hide the card; the backend also suppresses future hints.
      useAppStore.setState((s) => {
        s.workspaceEnvByConv[conversationId] = {
          hintVisible: false,
          status: 'idle',
        }
      })
    } catch {
      setSkipLoading(false)
    }
  }

  const handleRetry = async () => {
    await handleCreate()
  }

  // ─── Creating state ───
  if (envState.status === 'creating') {
    return (
      <div className="flex shrink-0 items-center gap-2 border-b border-primary/20 bg-primary/5 px-3 py-2 text-sm">
        <Loader2 className="size-4 animate-spin text-primary" />
        <span className="text-muted-foreground">正在创建项目虚拟环境 (.venv)…</span>
      </div>
    )
  }

  // ─── Failed state ───
  if (envState.status === 'failed') {
    return (
      <div className="flex shrink-0 flex-col gap-2 border-b border-destructive/20 bg-destructive/5 px-3 py-2 text-sm">
        <div className="flex items-center gap-2">
          <AlertTriangle className="size-4 shrink-0 text-destructive" />
          <span className="text-destructive">虚拟环境创建失败</span>
        </div>
        {envState.error && (
          <p className="ml-6 line-clamp-2 text-xs text-muted-foreground">{envState.error}</p>
        )}
        <div className="ml-6 flex gap-2">
          <Button size="xs" variant="outline" onClick={handleRetry}>
            重试
          </Button>
          <Button
            size="xs"
            variant="ghost"
            onClick={() => handleSkip('system_python')}
            disabled={skipLoading}
          >
            使用系统 Python
          </Button>
        </div>
      </div>
    )
  }

  // ─── Ready state (brief flash before disappearing) ───
  if (envState.status === 'ready') {
    return (
      <div className="flex shrink-0 items-center gap-2 border-b border-green-500/20 bg-green-500/5 px-3 py-2 text-sm">
        <CheckCircle2 className="size-4 shrink-0 text-green-600" />
        <span className="text-muted-foreground">
          虚拟环境已创建：{envState.venvPath ?? '.venv'}
        </span>
      </div>
    )
  }

  // ─── Idle: show the hint with three options ───
  return (
    <div
      className={cn(
        'flex shrink-0 flex-col gap-2 border-b border-primary/20 bg-primary/5 px-3 py-2 text-sm',
      )}
    >
      <div className="flex items-start gap-2">
        <Package className="mt-0.5 size-4 shrink-0 text-primary" />
        <div className="flex-1">
          <p className="font-medium text-foreground">检测到 Python 项目但无虚拟环境</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Agent 运行 <code className="rounded bg-muted px-1">pip install</code> 时，
            包将安装到系统 Python。建议创建项目 <code className="rounded bg-muted px-1">.venv</code> 以隔离依赖。
          </p>
        </div>
      </div>
      <div className="ml-6 flex flex-wrap gap-2">
        <Button size="xs" onClick={handleCreate}>
          创建 .venv
        </Button>
        <Button
          size="xs"
          variant="outline"
          onClick={() => handleSkip('system_python')}
          disabled={skipLoading}
        >
          使用系统 Python
        </Button>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => handleSkip('skip')}
          disabled={skipLoading}
        >
          跳过
        </Button>
      </div>
    </div>
  )
}
