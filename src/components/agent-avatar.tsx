'use client'

import { agentIconUrl, isAgentIconToken } from '@/shared/agent-icons'
import { cn } from '@/lib/utils'

/**
 * AgentAvatar — 统一的 Agent 头像渲染。
 *
 * avatar 为图标 token（icon-NN）时渲染图标库图片；否则回退到 Monogram + 哈希配色。
 */

type AgentLike = { id: string; name: string; avatar?: string | null }

interface AgentAvatarProps {
  agent: AgentLike
  size?: 'xs' | 'sm' | 'md' | 'lg'
  /** circle（默认，单头像）/ square（群聊九宫格瓦片） */
  shape?: 'circle' | 'square'
  className?: string
}

const SIZE_CLASS: Record<NonNullable<AgentAvatarProps['size']>, string> = {
  xs: 'size-5 text-[10px]',
  sm: 'size-7 text-[11px]',
  md: 'size-8 text-xs',
  lg: 'size-9 text-sm',
}

// 10 色调色板，饱和度统一，白字 contrast 足够
const PALETTE = [
  'bg-rose-500',
  'bg-orange-500',
  'bg-yellow-600',
  'bg-emerald-500',
  'bg-teal-500',
  'bg-sky-500',
  'bg-indigo-500',
  'bg-violet-500',
  'bg-fuchsia-500',
  'bg-slate-600',
]

function hashIndex(id: string, mod: number) {
  let h = 5381
  for (let i = 0; i < id.length; i++) {
    h = ((h << 5) + h + id.charCodeAt(i)) | 0
  }
  return Math.abs(h) % mod
}

export function getMonogram(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  const first = trimmed[0]
  // CJK / 韩文：1 字
  if (/[㐀-鿿가-힯]/.test(first)) {
    return first
  }
  // 英文：词首字母组合，最多 2 个
  const words = trimmed.split(/[\s\-_/]+/).filter(Boolean)
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase()
  }
  return trimmed.slice(0, 2).toUpperCase()
}

export function getAgentColor(agentId: string): string {
  return PALETTE[hashIndex(agentId, PALETTE.length)]
}

export function AgentAvatar({ agent, size = 'md', shape = 'circle', className }: AgentAvatarProps) {
  const radius = shape === 'square' ? 'rounded-[3px]' : 'rounded-full'

  if (isAgentIconToken(agent.avatar)) {
    return (
      <img
        src={agentIconUrl(agent.avatar as string)}
        alt={agent.name}
        className={cn('shrink-0 select-none object-cover', radius, SIZE_CLASS[size], className)}
      />
    )
  }

  const color = getAgentColor(agent.id)
  const text = getMonogram(agent.name)

  return (
    <div
      className={cn(
        'flex shrink-0 select-none items-center justify-center font-semibold text-white',
        radius,
        SIZE_CLASS[size],
        color,
        className,
      )}
    >
      {text}
    </div>
  )
}

const GROUP_AVATAR_MAX = 9

/**
 * ConversationAvatar — 会话头像。单聊（或仅 1 个 agent）显示单头像；
 * 群聊显示九宫格拼图，最多 9 个 agent 头像（2-4 个走 2 列，5-9 个走 3 列）。
 */
export function ConversationAvatar({
  agents,
  isGroup,
  size = 'lg',
  className,
}: {
  agents: AgentLike[]
  isGroup: boolean
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
}) {
  if (!isGroup || agents.length <= 1) {
    const only = agents[0]
    if (!only) {
      return (
        <div
          className={cn(
            'flex shrink-0 items-center justify-center rounded-full bg-muted text-sm text-muted-foreground',
            SIZE_CLASS[size],
            className,
          )}
        >
          ?
        </div>
      )
    }
    return <AgentAvatar agent={only} size={size} className={className} />
  }

  const shown = agents.slice(0, GROUP_AVATAR_MAX)
  const cols = shown.length <= 4 ? 2 : 3

  return (
    <div
      className={cn('grid shrink-0 gap-px overflow-hidden rounded-md bg-muted p-px', SIZE_CLASS[size], className)}
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {shown.map((a) => (
        <AgentAvatar
          key={a.id}
          agent={a}
          size="xs"
          shape="square"
          className="size-full rounded-[2px] text-[7px]"
        />
      ))}
    </div>
  )
}
