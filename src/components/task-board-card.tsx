'use client'

import { useCallback, useState } from 'react'
import { MessageSquare, Paperclip, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TaskRow } from '@/shared/types'
import {
  TASK_PRIORITY_BAR_COLORS,
  TASK_PRIORITY_DOT_COLORS,
  TASK_PRIORITY_LABELS,
} from '@/shared/task-board-config'
import { useAppStore } from '@/stores/app-store'
import { AgentAvatar } from '@/components/agent-avatar'

interface TaskBoardCardProps {
  task: TaskRow
  onClick: (taskId: string) => void
  onContextMenu: (e: React.MouseEvent, taskId: string) => void
}

export function TaskBoardCard({ task, onClick, onContextMenu }: TaskBoardCardProps) {
  const agents = useAppStore((s) => s.agents)
  const commentCount = useAppStore(
    (s) => (s.taskComments[task.id]?.length ?? 0),
  )
  const [isDragging, setIsDragging] = useState(false)

  const assignee = task.assigneeAgentId ? agents[task.assigneeAgentId] : null

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/task-id', task.id)
      e.dataTransfer.setData('text/task-status', task.status)
      setIsDragging(true)
    },
    [task.id, task.status],
  )

  const handleDragEnd = useCallback(() => {
    setIsDragging(false)
  }, [])

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onClick={() => onClick(task.id)}
      onContextMenu={(e) => {
        e.preventDefault()
        onContextMenu(e, task.id)
      }}
      className={cn(
        'group relative cursor-pointer overflow-hidden rounded-xl border border-border/50 bg-card p-3',
        'shadow-[var(--shadow-sm)] transition-all duration-200',
        'hover:border-border hover:shadow-[var(--shadow-md)] hover:-translate-y-px',
        'active:translate-y-0 active:scale-[0.99]',
        isDragging && 'opacity-40',
      )}
    >
      {/* 优先级顶部色条 */}
      <div
        className={cn(
          'absolute inset-x-0 top-0 h-0.5',
          TASK_PRIORITY_BAR_COLORS[task.priority] ?? 'bg-transparent',
        )}
      />

      {/* 标题 */}
      <p className="line-clamp-2 text-[13px] font-medium leading-5 text-foreground">
        {task.title}
      </p>

      {/* 描述预览 */}
      {task.description && (
        <p className="mt-1 line-clamp-1 text-[11px] leading-4 text-muted-foreground">
          {task.description}
        </p>
      )}

      {/* 标签 */}
      {task.labels.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {task.labels.slice(0, 3).map((label) => (
            <span
              key={label}
              className="rounded-full bg-muted/80 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
            >
              {label}
            </span>
          ))}
          {task.labels.length > 3 && (
            <span className="text-[10px] text-muted-foreground">
              +{task.labels.length - 3}
            </span>
          )}
        </div>
      )}

      {/* 底部信息栏 */}
      <div className="mt-2.5 flex items-center justify-between border-t border-border/30 pt-2">
        <div className="flex items-center gap-2">
          {/* 优先级 */}
          {task.priority !== 'none' && (
            <span className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
              <span className={cn('size-1.5 rounded-full', TASK_PRIORITY_DOT_COLORS[task.priority])} />
              {TASK_PRIORITY_LABELS[task.priority]}
            </span>
          )}

          {/* 评论数 */}
          {commentCount > 0 && (
            <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground">
              <MessageSquare className="size-3" />
              {commentCount}
            </span>
          )}

          {/* 失败次数 */}
          {task.failureCount > 0 && (
            <span className="flex items-center gap-0.5 text-[10px] font-medium text-destructive">
              <AlertTriangle className="size-3" />
              {task.failureCount}
            </span>
          )}
        </div>

        {/* 分配者头像 */}
        {assignee && (
          <AgentAvatar agent={assignee} size="xs" />
        )}
      </div>

      {/* 对话链接指示器 */}
      {task.conversationId && (
        <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <Paperclip className="size-3 text-muted-foreground" />
        </div>
      )}
    </div>
  )
}
