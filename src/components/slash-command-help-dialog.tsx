'use client'

import type { SlashCommandItem } from '@/components/slash-command-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface SlashCommandHelpDialogProps {
  open: boolean
  commands: SlashCommandItem[]
  onOpenChange(open: boolean): void
}

export function SlashCommandHelpDialog({
  open,
  commands,
  onOpenChange,
}: SlashCommandHelpDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Slash 命令</DialogTitle>
          <DialogDescription>当前对话输入框支持的快捷命令。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1">
          {commands.map((command) => {
            const Icon = command.icon
            return (
              <div key={command.id} className="flex items-center gap-2 rounded-md px-2 py-1.5">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                  <Icon className="size-4" />
                </span>
                <div className="flex min-w-0 flex-1 items-baseline gap-2">
                  <code className="shrink-0 font-mono text-xs font-medium">{command.command}</code>
                  <span className="truncate text-xs text-muted-foreground">
                    {command.description}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
