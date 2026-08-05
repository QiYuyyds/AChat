'use client'

import {
  Ban,
  CheckCircle2,
  CheckSquare,
  CircleDashed,
  Eye,
  Inbox,
  Loader2,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useSchedulerStatus, useAppStore } from '@/stores/app-store'
import {
  TASK_BOARD_COLUMNS,
  TASK_COLUMN_ACCENTS,
} from '@/shared/task-board-config'

const COLUMN_ICONS: Record<string, LucideIcon> = {
  backlog: Inbox,
  todo: CircleDashed,
  in_progress: Loader2,
  in_review: Eye,
  done: CheckCircle2,
  blocked: Ban,
}

export function TaskSidebarNav() {
  const taskIdsByStatus = useAppStore((s) => s.taskIdsByStatus)
  const scheduler = useSchedulerStatus()

  const totalCount = Object.values(taskIdsByStatus).reduce(
    (sum, ids) => sum + ids.length,
    0,
  )

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 px-3 pt-4 pb-3">
        <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10">
          <CheckSquare className="size-3.5 text-primary" />
        </div>
        <h2 className="text-sm font-semibold">任务面板</h2>
      </div>

      {/* Summary */}
      <div className="shrink-0 px-3 pb-3">
        <p className="text-[11px] leading-4 text-muted-foreground">
          全局任务池 · {totalCount} 个任务
          {scheduler.running && (
            <span className="ml-1 text-emerald-500">· 调度器运行中</span>
          )}
        </p>
      </div>

      {/* Status overview — 带列色彩身份 */}
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-4">
        {TASK_BOARD_COLUMNS.map((col) => {
          const count = taskIdsByStatus[col.status]?.length ?? 0
          const accent = TASK_COLUMN_ACCENTS[col.status]
          const Icon = COLUMN_ICONS[col.status] ?? CircleDashed
          return (
            <div
              key={col.status}
              className="flex items-center justify-between rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                {accent && (
                  <Icon className={cn('size-3.5 shrink-0', accent.icon)} />
                )}
                <span className="text-muted-foreground">{col.label}</span>
              </div>
              <span className="font-medium tabular-nums text-foreground">{count}</span>
            </div>
          )
        })}
      </div>

      {/* Scheduler status */}
      <div className="shrink-0 border-t border-border/50 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span
            className={cn(
              'size-1.5 rounded-full',
              scheduler.running ? 'bg-emerald-500 animate-pulse' : 'bg-muted-foreground/30',
            )}
          />
          <span>
            {scheduler.running
              ? `调度中 · ${scheduler.activeCount} 活跃 / ${scheduler.pendingCount} 待处理`
              : '调度器已停止'}
          </span>
        </div>
      </div>
    </div>
  )
}
