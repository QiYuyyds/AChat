'use client'

import { useCallback, useState } from 'react'
import {
  Ban,
  CheckCircle2,
  CircleDashed,
  Eye,
  EyeOff,
  Inbox,
  Loader2,
  Plus,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { TaskRow } from '@/shared/types'
import { TaskBoardCard } from '@/components/task-board-card'
import { TASK_COLUMN_ACCENTS, NO_DROP_STATUSES } from '@/shared/task-board-config'

const ADD_ALLOWED_STATUSES = new Set(['backlog', 'todo'])

const COLUMN_ICONS: Record<string, LucideIcon> = {
  backlog: Inbox,
  todo: CircleDashed,
  in_progress: Loader2,
  in_review: Eye,
  done: CheckCircle2,
  blocked: Ban,
}

interface TaskBoardColumnProps {
  status: string
  label: string
  tasks: TaskRow[]
  onTaskClick: (taskId: string) => void
  onTaskContextMenu: (e: React.MouseEvent, taskId: string) => void
  onAddTask: (status: string) => void
  onHideColumn: (status: string) => void
  onDropTask: (taskId: string, toStatus: string, beforeTaskId: string | null) => void
}

export function TaskBoardColumn({
  status,
  label,
  tasks,
  onTaskClick,
  onTaskContextMenu,
  onAddTask,
  onHideColumn,
  onDropTask,
}: TaskBoardColumnProps) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [dropPosition, setDropPosition] = useState<'top' | 'bottom' | null>(null)
  const [dropBeforeId, setDropBeforeId] = useState<string | null>(null)

  const accent = TASK_COLUMN_ACCENTS[status] ?? TASK_COLUMN_ACCENTS.backlog
  const Icon = COLUMN_ICONS[status] ?? CircleDashed

  const noDrop = NO_DROP_STATUSES.has(status)

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      if (noDrop) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      if (!isDragOver) setIsDragOver(true)

      const target = e.currentTarget as HTMLElement
      const cards = Array.from(
        target.querySelectorAll<HTMLElement>('[data-task-card]'),
      )

      let foundBefore: string | null = null
      let position: 'top' | 'bottom' = 'bottom'

      for (const card of cards) {
        const rect = card.getBoundingClientRect()
        const midY = rect.top + rect.height / 2
        if (e.clientY < midY) {
          foundBefore = card.dataset.taskId ?? null
          position = 'top'
          break
        }
      }

      if (!foundBefore && cards.length > 0) {
        position = 'bottom'
      }

      setDropPosition(position)
      setDropBeforeId(foundBefore)
    },
    [isDragOver, noDrop],
  )

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    const relatedTarget = e.relatedTarget as Node | null
    if (relatedTarget && e.currentTarget.contains(relatedTarget)) return
    setIsDragOver(false)
    setDropPosition(null)
    setDropBeforeId(null)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      if (noDrop) return
      e.preventDefault()
      const taskId = e.dataTransfer.getData('text/task-id')
      if (taskId) {
        onDropTask(taskId, status, dropBeforeId)
      }
      setIsDragOver(false)
      setDropPosition(null)
      setDropBeforeId(null)
    },
    [status, dropBeforeId, onDropTask, noDrop],
  )

  return (
    <div
      className={cn(
        'group flex h-full min-w-[240px] max-w-[300px] flex-col overflow-hidden rounded-xl border border-border/40',
        'transition-colors duration-200',
        isDragOver ? cn('border-primary/40 bg-primary/5', accent.glow) : 'bg-muted/20',
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* 顶部色彩细线 */}
      <div className={cn('h-0.5 w-full', accent.bar)} />

      {/* 列头 */}
      <div className={cn(
        'flex shrink-0 items-center justify-between px-3 py-2.5',
        accent.headerBg,
      )}>
        <div className="flex items-center gap-2">
          <Icon className={cn('size-3.5 shrink-0', accent.icon)} />
          <span className="text-xs font-semibold tracking-tight text-foreground">{label}</span>
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
            {tasks.length}
          </span>
        </div>
        <div className="flex items-center gap-0.5 opacity-40 transition-opacity group-hover:opacity-100">
          {ADD_ALLOWED_STATUSES.has(status) && (
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onAddTask(status)}
              title="添加任务"
            >
              <Plus className="size-3" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => onHideColumn(status)}
            title="隐藏列"
          >
            <EyeOff className="size-3" />
          </Button>
        </div>
      </div>

      {/* 卡片区 */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2.5 pb-2.5 pt-1">
        {/* 顶部放置指示器 */}
        {isDragOver && dropPosition === 'top' && dropBeforeId && (
          <div className="h-0.5 rounded-full bg-primary/60" />
        )}

        {tasks.map((task) => (
          <div key={task.id} data-task-card={task.id} data-task-id={task.id} className="task-fade-up">
            <TaskBoardCard
              task={task}
              onClick={onTaskClick}
              onContextMenu={onTaskContextMenu}
            />
            {isDragOver && dropBeforeId === task.id && dropPosition === 'top' && (
              <div className="mt-2 h-0.5 rounded-full bg-primary/60" />
            )}
          </div>
        ))}

        {/* 底部放置指示器 */}
        {isDragOver && (!dropBeforeId || dropPosition === 'bottom') && (
          <div className="h-0.5 rounded-full bg-primary/60" />
        )}

        {/* 空状态 */}
        {tasks.length === 0 && !isDragOver && ADD_ALLOWED_STATUSES.has(status) && (
          <button
            onClick={() => onAddTask(status)}
            className="flex items-center justify-center rounded-lg border border-dashed border-border/40 py-6 text-[11px] text-muted-foreground transition-colors hover:border-border hover:text-foreground"
          >
            <Plus className="mr-1 size-3" />
            添加任务
          </button>
        )}
      </div>
    </div>
  )
}
