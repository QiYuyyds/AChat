'use client'

import { BellRing } from 'lucide-react'
import { useEffect } from 'react'

import { PENDING_KIND_LABELS } from '@/shared/types'
import { useAppStore, usePendingToasts, type PendingToastItem } from '@/stores/app-store'

const TOAST_TTL_MS = 10_000

/**
 * PendingToast —— 非活跃会话到达审批时的右上角提示。
 *
 * 会话名 + 类别 + N 项；点击跳转到该会话；10s 自动消失。无 toast 基建，
 * 按项目约定不为将来加抽象，只做这一个极简组件。
 */
export function PendingToast() {
  const toasts = usePendingToasts()
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-72 flex-col gap-2">
      {toasts.map((t) => (
        <PendingToastCard key={t.id} toast={t} />
      ))}
    </div>
  )
}

function PendingToastCard({ toast }: { toast: PendingToastItem }) {
  const title = useAppStore((s) => s.conversations[toast.conversationId]?.title)
  const setActiveConversation = useAppStore((s) => s.setActiveConversation)
  const dismissPendingToast = useAppStore((s) => s.dismissPendingToast)

  useEffect(() => {
    const timer = setTimeout(() => dismissPendingToast(toast.id), TOAST_TTL_MS)
    return () => clearTimeout(timer)
  }, [toast.id, dismissPendingToast])

  return (
    <button
      type="button"
      onClick={() => {
        setActiveConversation(toast.conversationId)
        dismissPendingToast(toast.id)
      }}
      className="pointer-events-auto flex items-center gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-left text-xs shadow-lg transition hover:bg-accent"
    >
      <BellRing className="size-4 shrink-0 text-warning" />
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{title ?? '会话'}</div>
        <div className="text-muted-foreground">
          {PENDING_KIND_LABELS[toast.kind]}待审批 · {toast.count} 项
        </div>
      </div>
    </button>
  )
}
