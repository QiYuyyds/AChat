'use client'

import { CornerDownLeft } from 'lucide-react'

import { AgentAvatar } from '@/components/agent-avatar'
import { cn } from '@/lib/utils'
import type { SearchHit } from '@/shared/types'

function formatTime(ts: number): string {
  const d = new Date(ts)
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay
    ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export interface SearchResultItemProps {
  hit: SearchHit
  active: boolean
  onClick: () => void
}

export function SearchResultItem({ hit, active, onClick }: SearchResultItemProps) {
  const sender = hit.role === 'user' ? '你' : hit.agentName ?? 'Agent'

  return (
    <li
      role="option"
      aria-selected={active}
      className={cn(
        'group flex cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 transition-colors',
        active ? 'bg-accent' : 'hover:bg-accent/60',
      )}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onClick()
      }}
    >
      {hit.agentId ? (
        <AgentAvatar agent={{ id: hit.agentId, name: hit.agentName ?? 'Agent' }} size="md" />
      ) : (
        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-primary/10 text-xs font-medium text-primary">
          你
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs">
          <span className="truncate font-medium text-foreground">{hit.conversationTitle}</span>
          <span className="shrink-0 rounded bg-muted px-1.5 py-px text-[10px] text-muted-foreground">
            {sender}
          </span>
          <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
            {formatTime(hit.createdAt)}
          </span>
        </div>
        <p
          className="search-snippet mt-1 line-clamp-2 text-sm leading-relaxed text-muted-foreground"
          // Safe: snippetHtml is from user's own message content (server-generated);
          // <mark> tags are produced by FTS5 snippet() with controlled delimiters.
          dangerouslySetInnerHTML={{ __html: hit.snippetHtml }}
        />
      </div>

      <CornerDownLeft
        className={cn(
          'mt-1 size-3.5 shrink-0 text-muted-foreground transition-opacity',
          active ? 'opacity-100' : 'opacity-0',
        )}
        aria-hidden
      />
    </li>
  )
}
