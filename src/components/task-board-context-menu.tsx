'use client'

import { useCallback } from 'react'
import { Copy, Trash2, ArrowRight } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { TaskRow, TaskStatus, TaskPriority } from '@/shared/types'
import {
  TASK_BOARD_COLUMNS,
  TASK_PRIORITIES,
  TASK_STATUS_LABELS,
  TASK_PRIORITY_LABELS,
  NO_DROP_STATUSES,
} from '@/shared/task-board-config'

interface TaskBoardContextMenuProps {
  task: TaskRow | null
  open: boolean
  onOpenChange: (open: boolean) => void
  anchorPoint: { x: number; y: number } | null
  onMoveTask: (taskId: string, status: TaskStatus, ifVersion: number) => void
  onSetPriority: (taskId: string, priority: TaskPriority, ifVersion: number) => void
  onDuplicate: (taskId: string) => void
  onDelete: (taskId: string) => void
}

export function TaskBoardContextMenu({
  task,
  open,
  onOpenChange,
  anchorPoint,
  onMoveTask,
  onSetPriority,
  onDuplicate,
  onDelete,
}: TaskBoardContextMenuProps) {
  const handleMove = useCallback(
    (status: string) => {
      if (!task) return
      onMoveTask(task.id, status as TaskStatus, task.version)
      onOpenChange(false)
    },
    [task, onMoveTask, onOpenChange],
  )

  const handlePriority = useCallback(
    (priority: string) => {
      if (!task) return
      onSetPriority(task.id, priority as TaskPriority, task.version)
      onOpenChange(false)
    },
    [task, onSetPriority, onOpenChange],
  )

  const handleDuplicate = useCallback(() => {
    if (!task) return
    onDuplicate(task.id)
    onOpenChange(false)
  }, [task, onDuplicate, onOpenChange])

  const handleDelete = useCallback(() => {
    if (!task) return
    onDelete(task.id)
    onOpenChange(false)
  }, [task, onDelete, onOpenChange])

  if (!task) return null

  // Use a virtual trigger positioned at the anchor point
  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger
        className="fixed size-0"
        style={{
          left: anchorPoint?.x ?? 0,
          top: anchorPoint?.y ?? 0,
        }}
        aria-hidden
      />
      <DropdownMenuContent align="start" side="bottom" sideOffset={0}>
        {/* Move to submenu */}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <ArrowRight className="mr-2 size-3.5" />
            移动到
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {TASK_BOARD_COLUMNS.map((col) => (
              <DropdownMenuItem
                key={col.status}
                onClick={() => handleMove(col.status)}
                disabled={col.status === task.status || NO_DROP_STATUSES.has(col.status)}
              >
                {TASK_STATUS_LABELS[col.status]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        {/* Priority submenu */}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <span className="mr-2 text-xs">⚡</span>
            优先级
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {TASK_PRIORITIES.map((p) => (
              <DropdownMenuItem
                key={p}
                onClick={() => handlePriority(p)}
                disabled={p === task.priority}
              >
                {TASK_PRIORITY_LABELS[p]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        <DropdownMenuSeparator />

        <DropdownMenuItem onClick={handleDuplicate}>
          <Copy className="mr-2 size-3.5" />
          复制任务
        </DropdownMenuItem>

        <DropdownMenuItem onClick={handleDelete} className="text-destructive">
          <Trash2 className="mr-2 size-3.5" />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
