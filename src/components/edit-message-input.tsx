'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * 用户消息 inline 编辑器：textarea + 保存 / 取消按钮。
 * 用 key={messageId} 重置，避免 useEffect 里 setState 触发 react-hooks/set-state-in-effect。
 */
export function EditMessageInput({
  initial,
  submitting,
  onCommit,
  onCancel,
}: {
  initial: string
  submitting: boolean
  onCommit: (next: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    // 把光标放到末尾
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])

  const commit = () => {
    const trimmed = draft.trim()
    if (!trimmed || submitting) return
    onCommit(trimmed)
  }

  return (
    <div className="space-y-2">
      <textarea
        ref={ref}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            commit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            onCancel()
          }
        }}
        disabled={submitting}
        className={cn(
          'w-full resize-y rounded-md border border-primary/40 bg-background px-2 py-1.5 text-sm outline-none ring-2 ring-primary/30',
          'min-h-[60px]',
        )}
      />
      <div className="flex items-center justify-end gap-2 text-xs">
        <span className="mr-auto text-muted-foreground">Enter 保存并重发 · Shift+Enter 换行 · Esc 取消</span>
        <Button size="sm" variant="outline" onClick={onCancel} disabled={submitting}>
          取消
        </Button>
        <Button size="sm" onClick={commit} disabled={submitting || !draft.trim()}>
          {submitting ? '提交中…' : '保存并重发'}
        </Button>
      </div>
    </div>
  )
}
