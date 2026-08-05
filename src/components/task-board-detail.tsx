'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  Calendar,
  Folder,
  Box,
  MessageSquare,
  Send,
  X,
  Trash2,
  Pencil,
  ExternalLink,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Play,
  User,
  Clock,
  BellRing,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import {
  TASK_PRIORITY_LABELS,
  TASK_PRIORITY_DOT_COLORS,
  TASK_PRIORITY_BADGE_BG,
  TASK_PRIORITY_TEXT_COLORS,
  TASK_BOARD_COLUMNS,
} from '@/shared/task-board-config'
import { cn } from '@/lib/utils'
import type { TaskCommentRow, TaskRow } from '@/shared/types'
import {
  useAppStore,
  useTaskComments,
  usePendingWrites,
  usePendingBashCommands,
  usePendingQuestions,
} from '@/stores/app-store'
import {
  fetchTaskComments,
  addTaskComment,
  deleteTask,
  moveTask,
} from '@/lib/api'
import { AgentAvatar } from '@/components/agent-avatar'

interface TaskBoardDetailProps {
  taskId: string | null
  onClose: () => void
  onEdit: (task: TaskRow) => void
}

export function TaskBoardDetail({ taskId, onClose, onEdit }: TaskBoardDetailProps) {
  const task = useAppStore((s) => (taskId ? s.tasks[taskId] ?? null : null))
  const agents = useAppStore((s) => s.agents)
  const comments = useTaskComments(taskId)
  const setTaskComments = useAppStore((s) => s.setTaskComments)
  const addTaskCommentToStore = useAppStore((s) => s.addTaskComment)
  const removeTask = useAppStore((s) => s.removeTask)
  const upsertTask = useAppStore((s) => s.upsertTask)
  const pushUndo = useAppStore((s) => s.pushUndo)
  const setActiveConversation = useAppStore((s) => s.setActiveConversation)
  const setSidebarMode = useAppStore((s) => s.setSidebarMode)

  const [commentText, setCommentText] = useState('')
  const [loadingComments, setLoadingComments] = useState(false)

  const convId = task?.conversationId ?? null
  const pendingWrites = usePendingWrites(convId)
  const pendingBash = usePendingBashCommands(convId)
  const pendingQuestions = usePendingQuestions(convId)
  const pendingCount =
    pendingWrites.length + pendingBash.length + pendingQuestions.length

  useEffect(() => {
    if (!taskId) return
    setLoadingComments(true)
    fetchTaskComments(taskId)
      .then((cmts) => setTaskComments(taskId, cmts))
      .catch(() => {})
      .finally(() => setLoadingComments(false))
  }, [taskId, setTaskComments])

  const handleAddComment = useCallback(async () => {
    if (!taskId || !commentText.trim()) return
    try {
      const comment = await addTaskComment(taskId, { body: commentText.trim() })
      addTaskCommentToStore(taskId, comment)
      setCommentText('')
    } catch {
      // ignore
    }
  }, [taskId, commentText, addTaskCommentToStore])

  const handleDelete = useCallback(async () => {
    if (!taskId || !task) return
    const prevTask = { ...task }
    try {
      await deleteTask(taskId)
      removeTask(taskId)
      pushUndo('删除任务', async () => {
        const { createTask } = await import('@/lib/api')
        const recreated = await createTask({
          title: prevTask.title,
          description: prevTask.description,
          status: prevTask.status,
          priority: prevTask.priority,
          labels: prevTask.labels,
          assigneeAgentId: prevTask.assigneeAgentId,
        })
        upsertTask(recreated)
      })
      onClose()
    } catch {
      // ignore
    }
  }, [taskId, task, removeTask, pushUndo, upsertTask, onClose])

  const handleStatusChange = useCallback(
    async (newStatus: string) => {
      if (!task) return
      try {
        const updated = await moveTask(task.id, {
          status: newStatus,
          ifVersion: task.version,
        })
        upsertTask(updated)
      } catch {
        // ignore
      }
    },
    [task, upsertTask],
  )

  const handleOpenConversation = useCallback(() => {
    if (task?.conversationId) {
      setActiveConversation(task.conversationId)
      setSidebarMode('conversations')
    }
  }, [task?.conversationId, setActiveConversation, setSidebarMode])

  if (!task) return null

  const assignee = task.assigneeAgentId ? agents[task.assigneeAgentId] : null

  return (
    <div className="task-detail-enter flex h-full w-80 flex-col border-l border-border bg-card">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between px-4 py-3">
        <h3 className="text-sm font-semibold">任务详情</h3>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-xs" onClick={() => onEdit(task)}>
            <Pencil className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon-xs" onClick={handleDelete}>
            <Trash2 className="size-3.5 text-destructive" />
          </Button>
          <Button variant="ghost" size="icon-xs" onClick={onClose}>
            <X className="size-3.5" />
          </Button>
        </div>
      </div>

      <Separator />

      {/* Content */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-3">
        {/* 标题 */}
        <h2 className="text-base font-medium leading-6 text-foreground">
          {task.title}
        </h2>

        {/* 状态分段控制器 + 优先级 badge */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {/* 分段控制器替代原生 <select> */}
          <div className="inline-flex rounded-lg border border-border bg-muted/30 p-0.5">
            {TASK_BOARD_COLUMNS.map((col) => (
              <button
                key={col.status}
                onClick={() => handleStatusChange(col.status)}
                className={cn(
                  'rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
                  task.status === col.status
                    ? 'bg-card text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {col.label}
              </button>
            ))}
          </div>
        </div>

        {/* 优先级 + 执行状态 */}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[10px] font-medium',
              TASK_PRIORITY_BADGE_BG[task.priority] ?? 'bg-muted',
              TASK_PRIORITY_TEXT_COLORS[task.priority] ?? 'text-muted-foreground',
            )}
          >
            <span className={cn('size-1.5 rounded-full', TASK_PRIORITY_DOT_COLORS[task.priority])} />
            {TASK_PRIORITY_LABELS[task.priority]}
          </span>

          {task.status === 'in_progress' && (
            <span className="flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-600">
              <Loader2 className="size-3 animate-spin" />
              执行中
            </span>
          )}
          {task.status === 'blocked' && (
            <span className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-0.5 text-[10px] text-destructive">
              <AlertCircle className="size-3" />
              已阻塞
            </span>
          )}
          {task.status === 'in_review' && (
            <span className="flex items-center gap-1 rounded-md bg-yellow-500/10 px-2 py-0.5 text-[10px] text-yellow-600">
              <CheckCircle2 className="size-3" />
              待评审
            </span>
          )}
        </div>

        {/* 描述 */}
        {task.description && (
          <div className="mt-4">
            <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">描述</p>
            <p className="mt-1.5 text-xs leading-5 text-foreground">
              {task.description}
            </p>
          </div>
        )}

        {/* 标签 */}
        {task.labels.length > 0 && (
          <div className="mt-3">
            <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">标签</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {task.labels.map((label) => (
                <span
                  key={label}
                  className="rounded-full bg-muted/80 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 元数据 — 分组卡片布局 */}
        <div className="mt-4 grid grid-cols-2 gap-2">
          {/* 分配给 */}
          <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
            <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              <User className="size-2.5" />
              分配给
            </p>
            <div className="mt-1.5 flex items-center gap-1.5">
              {assignee && <AgentAvatar agent={assignee} size="xs" />}
              <span className="text-xs font-medium text-foreground">
                {assignee?.name ?? '未分配'}
              </span>
            </div>
          </div>

          {/* 创建者 */}
          <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
            <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              <User className="size-2.5" />
              创建者
            </p>
            <p className="mt-1.5 text-xs font-medium text-foreground">
              {task.creatorName}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {task.creatorType === 'user' ? '用户' : 'Agent'}
            </p>
          </div>

          {/* 工作目录 */}
          {task.workspaceMode && (
            <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                {task.workspaceMode === 'local' ? <Folder className="size-2.5" /> : <Box className="size-2.5" />}
                工作目录
              </p>
              <p className="mt-1.5 truncate text-xs font-medium text-foreground">
                {task.workspaceMode === 'local' ? (task.workspacePath ?? '本地项目') : '沙箱'}
              </p>
            </div>
          )}

          {/* 截止日期 */}
          {task.dueDate && (
            <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                <Calendar className="size-2.5" />
                截止日期
              </p>
              <p className="mt-1.5 text-xs font-medium text-foreground">
                {task.dueDate}
              </p>
            </div>
          )}

          {/* 失败次数 */}
          {task.failureCount > 0 && (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-2.5">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-destructive">
                <AlertCircle className="size-2.5" />
                失败次数
              </p>
              <p className="mt-1.5 text-xs font-medium text-destructive">
                {task.failureCount}
              </p>
            </div>
          )}

          {/* 创建时间 */}
          <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
            <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              <Clock className="size-2.5" />
              创建于
            </p>
            <p className="mt-1.5 text-[11px] font-medium text-foreground">
              {new Date(task.createdAt).toLocaleDateString()}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {new Date(task.createdAt).toLocaleTimeString()}
            </p>
          </div>
        </div>

        {/* 待审批提醒 */}
        {task.conversationId && pendingCount > 0 && (
          <div
            className="mt-3 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5"
            role="alert"
          >
            <BellRing className="size-3.5 shrink-0 animate-pulse text-amber-600" />
            <div className="flex-1">
              <p className="text-[11px] font-medium text-amber-700 dark:text-amber-400">
                有 {pendingCount} 项待处理审批/提问
              </p>
              <p className="text-[10px] text-amber-600/70 dark:text-amber-500/70">
                Agent 正在等待你的响应
              </p>
            </div>
          </div>
        )}

        {/* 执行对话链接 */}
        {task.conversationId && (
          <Button
            variant={pendingCount > 0 ? 'default' : 'outline'}
            size="sm"
            className={cn(
              'mt-3 h-8 gap-1.5',
              pendingCount > 0 && 'animate-pulse',
            )}
            onClick={handleOpenConversation}
          >
            <Play className="size-3 text-primary" />
            {pendingCount > 0 ? `处理待审批 (${pendingCount})` : '查看执行对话'}
            <ExternalLink className="size-3" />
          </Button>
        )}

        <Separator className="my-4" />

        {/* 评论区 */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <MessageSquare className="size-3.5 text-muted-foreground" />
            <span className="text-[11px] font-medium text-muted-foreground">
              评论 ({comments.length})
            </span>
          </div>

          {/* 评论列表 */}
          <div className="flex flex-col gap-2">
            {loadingComments && (
              <p className="text-[11px] text-muted-foreground">加载中...</p>
            )}
            {comments.map((comment: TaskCommentRow) => (
              <div
                key={comment.id}
                className="rounded-lg border border-border/40 bg-muted/20 p-2.5"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-medium text-foreground">
                    {comment.authorName}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {new Date(comment.createdAt).toLocaleString()}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-4 text-foreground">
                  {comment.body}
                </p>
              </div>
            ))}
            {!loadingComments && comments.length === 0 && (
              <p className="text-[11px] text-muted-foreground">暂无评论</p>
            )}
          </div>

          {/* 添加评论 */}
          <div className="flex gap-1.5">
            <Input
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void handleAddComment()
                }
              }}
              placeholder="添加评论..."
              className="h-8 text-xs"
            />
            <Button
              variant="outline"
              size="icon-sm"
              onClick={handleAddComment}
              disabled={!commentText.trim()}
            >
              <Send className="size-3" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
