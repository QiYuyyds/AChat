'use client'

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { ChevronDown, Folder, Box } from 'lucide-react'
import type { TaskPriority, TaskRow, TaskWorkspaceMode } from '@/shared/types'
import {
  TASK_PRIORITIES,
  TASK_PRIORITY_LABELS,
  TASK_STATUS_LABELS,
} from '@/shared/task-board-config'
import { useAppStore } from '@/stores/app-store'
import { createTask, updateTask } from '@/lib/api'

interface TaskBoardEditorProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** If provided, edit mode. If null, create mode. */
  task: TaskRow | null
  /** Default status for new tasks */
  defaultStatus: string
}

export function TaskBoardEditor({
  open,
  onOpenChange,
  task,
  defaultStatus,
}: TaskBoardEditorProps) {
  const agents = useAppStore((s) => s.agents)
  const upsertTask = useAppStore((s) => s.upsertTask)
  const pushUndo = useAppStore((s) => s.pushUndo)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<TaskPriority>('none')
  const [labels, setLabels] = useState<string[]>([])
  const [labelInput, setLabelInput] = useState('')
  const [assigneeAgentId, setAssigneeAgentId] = useState<string | null>(null)
  const [workspaceMode, setWorkspaceMode] = useState<TaskWorkspaceMode>(null)
  const [workspacePath, setWorkspacePath] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEdit = !!task

  // Reset form when opening
  useEffect(() => {
    if (!open) return
    if (task) {
      setTitle(task.title)
      setDescription(task.description)
      setPriority(task.priority)
      setLabels(task.labels)
      setAssigneeAgentId(task.assigneeAgentId)
      setWorkspaceMode(task.workspaceMode)
      setWorkspacePath(task.workspacePath ?? '')
      setDueDate(task.dueDate ?? '')
    } else {
      setTitle('')
      setDescription('')
      setPriority('none')
      setLabels([])
      setAssigneeAgentId(null)
      setWorkspaceMode(null)
      setWorkspacePath('')
      setDueDate('')
    }
    setError(null)
    setLabelInput('')
  }, [open, task])

  const handleAddLabel = useCallback(() => {
    const trimmed = labelInput.trim()
    if (trimmed && !labels.includes(trimmed)) {
      setLabels([...labels, trimmed])
    }
    setLabelInput('')
  }, [labelInput, labels])

  const handleRemoveLabel = useCallback((label: string) => {
    setLabels((prev) => prev.filter((l) => l !== label))
  }, [])

  const handleSave = useCallback(async () => {
    if (!title.trim()) {
      setError('请输入任务标题')
      return
    }

    setSaving(true)
    setError(null)

    try {
      if (isEdit && task) {
        const prevTask = { ...task }
        const updated = await updateTask(task.id, {
          title: title.trim(),
          description: description.trim(),
          priority,
          labels,
          dueDate: dueDate || null,
          ifVersion: task.version,
        })
        upsertTask(updated)
        pushUndo('编辑任务', async () => {
          const reverted = await updateTask(prevTask.id, {
            title: prevTask.title,
            description: prevTask.description,
            priority: prevTask.priority,
            labels: prevTask.labels,
            dueDate: prevTask.dueDate,
            ifVersion: updated.version,
          })
          upsertTask(reverted)
        })
      } else {
        const created = await createTask({
          title: title.trim(),
          description: description.trim(),
          status: defaultStatus,
          priority,
          labels,
          assigneeAgentId,
          workspaceMode: workspaceMode ?? undefined,
          workspacePath: workspaceMode === 'local' ? workspacePath || null : null,
          dueDate: dueDate || null,
        })
        upsertTask(created)
        pushUndo('创建任务', async () => {
          const { deleteTask } = await import('@/lib/api')
          await deleteTask(created.id)
        })
      }
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }, [
    title,
    description,
    priority,
    labels,
    dueDate,
    isEdit,
    task,
    defaultStatus,
    assigneeAgentId,
    workspaceMode,
    workspacePath,
    upsertTask,
    pushUndo,
    onOpenChange,
  ])

  const agentList = Object.values(agents).filter((a) => !a.isGuide)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑任务' : '新建任务'}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {/* Title */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              标题
            </label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="输入任务标题..."
              autoFocus
            />
          </div>

          {/* Description */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              描述
            </label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="详细描述..."
              rows={3}
            />
          </div>

          {/* Priority + Assignee */}
          <div className="flex gap-3">
            <div className="flex flex-1 flex-col gap-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                优先级
              </label>
              <DropdownMenu>
                <DropdownMenuTrigger
                  className="inline-flex h-7 items-center justify-between gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
                >
                  {TASK_PRIORITY_LABELS[priority]}
                  <ChevronDown className="size-3.5" />
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  {TASK_PRIORITIES.map((p) => (
                    <DropdownMenuItem
                      key={p}
                      onClick={() => setPriority(p)}
                    >
                      {TASK_PRIORITY_LABELS[p]}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {!isEdit && (
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  分配给
                </label>
                <DropdownMenu>
                  <DropdownMenuTrigger
                    className="inline-flex h-7 flex-1 items-center justify-between gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
                  >
                    {assigneeAgentId
                      ? agents[assigneeAgentId]?.name ?? '未知'
                      : '未分配'}
                    <ChevronDown className="size-3.5" />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => setAssigneeAgentId(null)}>
                      未分配
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuLabel>联系人</DropdownMenuLabel>
                      {agentList.map((a) => (
                        <DropdownMenuItem
                          key={a.id}
                          onClick={() => setAssigneeAgentId(a.id)}
                        >
                          {a.name}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )}
          </div>

          {/* Labels */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              标签
            </label>
            <div className="flex flex-wrap gap-1">
              {labels.map((label) => (
                <span
                  key={label}
                  className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px]"
                >
                  {label}
                  <button
                    onClick={() => handleRemoveLabel(label)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <Input
              value={labelInput}
              onChange={(e) => setLabelInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  handleAddLabel()
                }
              }}
              placeholder="输入标签后按回车..."
              className="text-xs"
            />
          </div>

          {/* Workspace binding (create mode only) */}
          {!isEdit && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                工作目录
              </label>
              <div className="flex gap-2">
                <DropdownMenu>
                  <DropdownMenuTrigger
                    className="inline-flex h-7 min-w-28 items-center justify-between gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
                  >
                    {workspaceMode === 'local' ? (
                      <>
                        <Folder className="mr-1 size-3.5" />
                        本地项目
                      </>
                    ) : workspaceMode === 'sandbox' ? (
                      <>
                        <Box className="mr-1 size-3.5" />
                        沙箱
                      </>
                    ) : (
                      '默认'
                    )}
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => setWorkspaceMode(null)}>
                      默认（沙箱）
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setWorkspaceMode('sandbox')}>
                      <Box className="mr-2 size-3.5" />
                      沙箱
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setWorkspaceMode('local')}>
                      <Folder className="mr-2 size-3.5" />
                      本地项目
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                {workspaceMode === 'local' && (
                  <Input
                    value={workspacePath}
                    onChange={(e) => setWorkspacePath(e.target.value)}
                    placeholder="本地项目绝对路径，如 D:/projects/my-app"
                    className="flex-1 text-xs"
                  />
                )}
              </div>
            </div>
          )}

          {/* Due date */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              截止日期
            </label>
            <Input
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
              className="text-xs"
            />
          </div>

          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : isEdit ? '保存' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
