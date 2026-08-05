'use client'

import { Eye } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { TASK_STATUS_LABELS, TASK_COLUMN_ACCENTS } from '@/shared/task-board-config'

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
        return (
          <Button
            key={status}
            variant="ghost"
            size="xs"
            onClick={() => onShowColumn(status)}
            className="h-6 gap-1.5"
          >
            {accent && <span className={cn('size-1.5 rounded-full', accent.dot)} />}
            {TASK_STATUS_LABELS[status] ?? status}
            <span className="text-[10px] tabular-nums text-muted-foreground">
              {taskCountsByStatus[status] ?? 0}
            </span>
            <Eye className="size-3 text-muted-foreground" />
          </Button>
        )
      })}
    </div>
  )
}
