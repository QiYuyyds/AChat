'use client'

import { AgentAvatar } from '@/components/agent-avatar'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

export function WaveColumnHeader({
  agentId,
  taskId,
  className,
}: {
  agentId: string
  taskId: string
  className?: string
}) {
  const agent = useAppStore((s) => s.agents[agentId])

  return (
    <div
      className={cn(
        'flex items-center gap-1.5 border-b border-border/50 pb-1.5',
        className,
      )}
    >
      {agent ? (
        <AgentAvatar agent={agent} size="xs" />
      ) : (
        <div className="size-5 shrink-0 rounded-full bg-muted" />
      )}
      <span className="truncate text-xs font-medium text-foreground">
        {agent?.name ?? 'Unknown'}
      </span>
      <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
        {taskId}
      </span>
    </div>
  )
}
