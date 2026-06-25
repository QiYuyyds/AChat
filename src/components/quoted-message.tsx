'use client'

import { CornerUpLeft, X } from 'lucide-react'

import { AgentAvatar } from '@/components/agent-avatar'
import type { MessageRow } from '@/db/schema'
import { getToolDisplayName } from '@/lib/tool-display'
import { cn } from '@/lib/utils'
import type { MessagePart } from '@/shared/types'
import { useAppStore } from '@/stores/app-store'

/**
 * 共享的「被引用消息预览」组件。
 *
 * `variant='preview'`：出现在新消息卡片内部（顶部），样式较紧凑
 * `variant='compose'`：出现在 MessageInput 顶部，带关闭按钮
 */
export function QuotedMessage({
  message,
  variant,
  onDismiss,
  onClick,
}: {
  message: MessageRow
  variant: 'preview' | 'compose'
  onDismiss?: () => void
  onClick?: () => void
}) {
  const agentsMap = useAppStore((s) => s.agents)
  const agent = message.agentId ? agentsMap[message.agentId] : null

  const summary = extractMessageSummary(message.parts)
  const speakerName = message.role === 'user' ? '用户' : agent?.name ?? 'Unknown'

  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-md border border-l-[3px] border-l-primary/50 bg-muted/40 px-2 py-1.5 text-xs',
        onClick && 'cursor-pointer transition hover:bg-muted/70 hover:border-l-primary/70',
      )}
      onClick={onClick}
    >
      {variant === 'compose' && <CornerUpLeft className="mt-0.5 size-3 shrink-0 text-muted-foreground" />}
      {agent ? (
        <AgentAvatar agent={agent} size="xs" />
      ) : (
        <div className="mt-0.5 size-4 shrink-0 rounded-full bg-muted-foreground/20" />
      )}
      <div className="min-w-0 flex-1">
        <div className="text-[10px] font-medium text-muted-foreground">回复 {speakerName}</div>
        <div className="truncate text-muted-foreground/80">{summary}</div>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onDismiss()
          }}
          className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="取消引用"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

/** 提取消息的单行预览（≤80 字符），用于 QuotedMessage / PinnedMessagesBar 等。 */
export function extractMessageSummary(parts: MessagePart[]): string {
  for (const p of parts) {
    if (p.type === 'text' && p.content) return p.content.slice(0, 80)
  }
  for (const p of parts) {
    if (p.type === 'code') return `[代码块 ${p.language || ''}]`
    if (p.type === 'tool_use') return `[调用 ${getToolDisplayName(p.toolName)}]`
    if (p.type === 'artifact_ref') return `[产物引用]`
    if (p.type === 'thinking' && p.content) return `[思考] ${p.content.slice(0, 60)}`
  }
  return '(空消息)'
}
