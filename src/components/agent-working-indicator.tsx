'use client'

import { Clock } from 'lucide-react'
import { useElapsedTimer } from '@/lib/use-elapsed-timer'
import { getToolDisplayName } from '@/lib/tool-display'
import { AgentAvatar } from '@/components/agent-avatar'
import type { RunState } from '@/stores/app-store'
import { useAppStore, useRunPhase } from '@/stores/app-store'

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-0.5">
      <span
        className="size-1.5 rounded-full bg-primary/40"
        style={{ animation: 'typing-bounce 1.4s infinite', animationDelay: '0s' }}
      />
      <span
        className="size-1.5 rounded-full bg-primary/60"
        style={{ animation: 'typing-bounce 1.4s infinite', animationDelay: '0.15s' }}
      />
      <span
        className="size-2 rounded-full bg-primary/80"
        style={{ animation: 'typing-bounce 1.4s infinite', animationDelay: '0.3s' }}
      />
      <style>{`
        @keyframes typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
    </span>
  )
}

export function AgentWorkingIndicator({
  run,
  conversationId,
}: {
  run: RunState
  conversationId: string
}) {
  const agentsMap = useAppStore((s) => s.agents)
  const agent = run.agentId ? agentsMap[run.agentId] : null
  const isQueued = run.status === 'queued'
  const { phase, toolName } = useRunPhase(conversationId, run.id)
  const elapsed = useElapsedTimer(run.startedAt, !isQueued)

  const phaseLabel = toolName
    ? `${phase}: ${getToolDisplayName(toolName, undefined)}`
    : phase

  const name = agent?.name ?? 'Agent'

  if (isQueued) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg animate-in fade-in slide-in-from-bottom-1 opacity-60"
      >
        {agent ? (
          <div className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-card ring-2 ring-muted-foreground/40">
            <AgentAvatar agent={agent} size="md" />
          </div>
        ) : (
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-card ring-2 ring-muted-foreground/40">
            <span className="text-sm font-medium">A</span>
          </div>
        )}
        <div className="flex max-w-[80%] min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{name}</span>
            <Clock className="size-3" />
            <span>排队中</span>
          </div>
          <div className="text-xs text-muted-foreground">
            等待前面的 Agent 完成后自动开始…
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-3 rounded-lg animate-in fade-in slide-in-from-bottom-1"
    >
      {agent ? (
        <div className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-card ring-2 ring-primary/80 agent-ring-active">
          <AgentAvatar agent={agent} size="md" />
        </div>
      ) : (
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-card ring-2 ring-primary/80 agent-ring-active">
          <span className="text-sm font-medium">A</span>
        </div>
      )}
      <div className="flex max-w-[80%] min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{name}</span>
          <TypingDots />
        </div>
        <div className="text-xs text-muted-foreground">
          {phaseLabel}
          {elapsed !== null && (
            <span className="text-muted-foreground/60">
              {' · '}
              {(() => {
                const seconds = Math.floor(elapsed / 1000)
                if (seconds < 60) return `${seconds}s`
                const min = Math.floor(seconds / 60)
                const sec = seconds % 60
                return `${min}m${sec}s`
              })()}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
