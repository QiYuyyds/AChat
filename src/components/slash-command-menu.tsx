'use client'

import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

export interface SlashCommandItem {
  id: string
  command: string
  label: string
  description: string
  icon: LucideIcon
  disabled?: boolean
}

interface SlashCommandMenuProps {
  commands: SlashCommandItem[]
  highlightedIndex: number
  onHighlight(index: number): void
  onSelect(command: SlashCommandItem): void
}

export function SlashCommandMenu({
  commands,
  highlightedIndex,
  onHighlight,
  onSelect,
}: SlashCommandMenuProps) {
  if (commands.length === 0) return null

  return (
    <div className="absolute bottom-full left-3 right-3 mb-2 max-h-64 overflow-y-auto rounded-md border bg-popover p-1 shadow-md">
      {commands.map((command, index) => {
        const Icon = command.icon
        return (
          <button
            key={command.id}
            type="button"
            disabled={command.disabled}
            onMouseDown={(event) => {
              event.preventDefault()
              if (!command.disabled) onSelect(command)
            }}
            onMouseEnter={() => onHighlight(index)}
            className={cn(
              'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition',
              index === highlightedIndex && 'bg-accent',
              command.disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Icon className="size-4" />
            </span>
            <span className="flex min-w-0 flex-1 items-baseline gap-2">
              <code className="shrink-0 font-mono text-xs font-medium">{command.command}</code>
              <span className="truncate text-[10px] text-muted-foreground">
                {command.description}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
