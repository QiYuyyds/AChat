'use client'

import {
  Ban,
  CheckCircle2,
  CircleDashed,
  Eye,
  EyeOff,
  Inbox,
  Loader2,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { TASK_STATUS_LABELS, TASK_COLUMN_ACCENTS } from '@/shared/task-board-config'

const COLUMN_ICONS: Record<string, LucideIcon> = {
  backlog: Inbox,
  todo: CircleDashed,
  in_progress: Loader2,
  in_review: Eye,
  done: CheckCircle2,
  blocked: Ban,
}

interface TaskBoardHiddenColumnsProps {
  hiddenStatuses: string[]
  taskCountsByStatus: Record<string, number>
  onShowColumn: (status: string) => void
}

export function TaskBoardHiddenColumns({
  hiddenStatuses,
  taskCountsByStatus,
  onShowColumn,
}: TaskBoardHiddenColumnsProps) {
  if (hiddenStatuses.length === 0) return null

  return (
    <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5">
      <span className="text-[11px] text-muted-foreground">隐藏的列</span>
      <div className="h-3 w-px bg-border" />
      {hiddenStatuses.map((status) => {
        const accent = TASK_COLUMN_ACCENTS[status]
        const Icon = COLUMN_ICONS[status] ?? CircleDashed
        return (
          <Button
            key={status}
            variant="ghost"
            size="xs"
            onClick={() => onShowColumn(status)}
            className="h-6 gap-1.5"
          >
            {accent
              ? <Icon className={cn('size-3 shrink-0', accent.icon)} />
              : <CircleDashed className="size-3 shrink-0" />}
            {TASK_STATUS_LABELS[status] ?? status}
            <span className="text-[10px] tabular-nums text-muted-foreground">
              {taskCountsByStatus[status] ?? 0}
            </span>
            <EyeOff className="size-3 text-muted-foreground" />
          </Button>
        )
      })}
    </div>
  )
}
