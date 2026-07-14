'use client'

import { useElapsedTimer } from '@/lib/use-elapsed-timer'
import { getToolDisplayName } from '@/lib/tool-display'
import { AgentAvatar } from '@/components/agent-avatar'
import type { RunState } from '@/stores/app-store'
import { useAppStore, useRunPhase } from '@/stores/app-store'

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-0.5">
      <span
        className="size-1.5 rounded-full bg-primary/60"
        style={{ animation: 'typing-bounce 1.4s infinite', animationDelay: '0s' }}
      />
      <span
        className="size-1.5 rounded-full bg-primary/60"
        style={{ animation: 'typing-bounce 1.4s infinite', animationDelay: '0.15s' }}
      />
      <span
        className="size-1.5 rounded-full bg-primary/60"
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
  const { phase, toolName } = useRunPhase(conversationId, run.id)
  const elapsed = useElapsedTimer(run.startedAt, true)

  const phaseLabel = toolName
    ? `${phase}: ${getToolDisplayName(toolName, undefined)}`
    : phase

  const name = agent?.name ?? 'Agent'

  return (
    <div
      className="flex items-center gap-3 rounded-lg animate-in fade-in slide-in-from-bottom-1"
    >
      {agent ? (
        <div className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-card ring-2 ring-primary animate-pulse">
          <AgentAvatar agent={agent} size="md" />
        </div>
      ) : (
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-card ring-2 ring-primary animate-pulse">
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
