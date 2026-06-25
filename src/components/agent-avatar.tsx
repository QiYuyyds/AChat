'use client'

import { cn } from '@/lib/utils'

/**
 * AgentAvatar — 统一的 Agent 头像渲染。Monogram + 哈希配色（Linear/Notion 风格）。
 *
 * 不再使用 emoji，让产品看起来不那么「AI 工具感」。
 */

interface AgentAvatarProps {
  agent: { id: string; name: string }
  size?: 'xs' | 'sm' | 'md' | 'lg'
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
  'bg-amber-600',
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

export function AgentAvatar({ agent, size = 'md', className }: AgentAvatarProps) {
  const color = getAgentColor(agent.id)
  const text = getMonogram(agent.name)

  return (
    <div
      className={cn(
        'flex shrink-0 select-none items-center justify-center rounded-full font-semibold text-white',
        SIZE_CLASS[size],
        color,
        className,
      )}
    >
      {text}
    </div>
  )
}
