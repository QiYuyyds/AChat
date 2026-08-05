'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Play,
  Square,
  Search,
  Plus,
  Columns3,
  EyeOff,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu'
import { TaskBoardColumn } from '@/components/task-board-column'
import { TaskBoardDetail } from '@/components/task-board-detail'
import { TaskBoardEditor } from '@/components/task-board-editor'
import { TaskBoardContextMenu } from '@/components/task-board-context-menu'
import { TaskBoardUndoToast } from '@/components/task-board-undo-toast'
import { TaskBoardFilterMenu, type TaskFilter } from '@/components/task-board-filter-menu'
import { TaskBoardHiddenColumns } from '@/components/task-board-hidden-columns'
import {
  TASK_BOARD_COLUMNS,
  TASK_STATUS_LABELS,
} from '@/shared/task-board-config'
import type { TaskRow, TaskStatus, TaskPriority } from '@/shared/types'
import {
  useAppStore,
  useSchedulerStatus,
} from '@/stores/app-store'
import {
  fetchTasks,
  getSchedulerStatus,
  startScheduler,
  stopScheduler,
  moveTask,
  updateTask,
  createTask,
  deleteTask,
} from '@/lib/api'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { cn } from '@/lib/utils'

const COLUMN_VIS_KEY = 'taskboard.columnVisibility'
const SHOW_EMPTY_KEY = 'taskboard.showEmptyColumns'

export function TaskBoardView() {
  const tasks = useAppStore((s) => s.tasks)
  const taskIdsByStatus = useAppStore((s) => s.taskIdsByStatus)
  const agents = useAppStore((s) => s.agents)
  const setTasks = useAppStore((s) => s.setTasks)
  const upsertTask = useAppStore((s) => s.upsertTask)
  const removeTaskFromStore = useAppStore((s) => s.removeTask)
  const setSchedulerStatus = useAppStore((s) => s.setSchedulerStatus)
  const pushUndo = useAppStore((s) => s.pushUndo)
  const popUndo = useAppStore((s) => s.popUndo)
  const scheduler = useSchedulerStatus()

  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<TaskFilter>({
    statuses: new Set(),
    priorities: new Set(),
    labels: new Set(),
    assigneeAgentId: null,
  })
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<TaskRow | null>(null)
  const [editorDefaultStatus, setEditorDefaultStatus] = useState('backlog')

  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem(COLUMN_VIS_KEY)
      if (stored) return new Set(JSON.parse(stored))
    } catch {}
    return new Set()
  })
  const [showEmptyColumns, setShowEmptyColumns] = useState(true)

  const [contextMenuTask, setContextMenuTask] = useState<TaskRow | null>(null)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const [contextMenuPos, setContextMenuPos] = useState<{ x: number; y: number } | null>(null)

  const searchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetchTasks()
      .then(setTasks)
      .catch(() => {})
    getSchedulerStatus()
      .then((s) =>
        setSchedulerStatus(s.running, s.pendingCount, s.activeCount),
      )
      .catch(() => {})
  }, [setTasks, setSchedulerStatus])

  useGuideSideEffectRefresh('tasks', () => {
    fetchTasks()
      .then(setTasks)
      .catch(() => {})
  })

  useEffect(() => {
    localStorage.setItem(COLUMN_VIS_KEY, JSON.stringify([...hiddenColumns]))
  }, [hiddenColumns])

  useEffect(() => {
    try {
      const stored = localStorage.getItem(SHOW_EMPTY_KEY)
      if (stored !== null) setShowEmptyColumns(stored === 'true')
    } catch {}
  }, [])
  useEffect(() => {
    localStorage.setItem(SHOW_EMPTY_KEY, String(showEmptyColumns))
  }, [showEmptyColumns])

  const availableLabels = useMemo(() => {
    const set = new Set<string>()
    for (const task of Object.values(tasks)) {
      for (const label of task.labels) set.add(label)
    }
    return [...set].sort()
  }, [tasks])

  const assigneeOptions = useMemo(
    () =>
      Object.values(agents)
        .filter((a) => !a.isGuide)
        .map((a) => ({ id: a.id, name: a.name })),
    [agents],
  )

  const filteredTasksByStatus = useMemo(() => {
    const result: Record<string, TaskRow[]> = {}
    const q = searchQuery.trim().toLowerCase()

    for (const task of Object.values(tasks)) {
      if (q) {
        const matchesTitle = task.title.toLowerCase().includes(q)
        const matchesDesc = task.description.toLowerCase().includes(q)
        if (!matchesTitle && !matchesDesc) continue
      }

      if (filter.statuses.size > 0 && !filter.statuses.has(task.status)) continue
      if (filter.priorities.size > 0 && !filter.priorities.has(task.priority)) continue

      if (filter.labels.size > 0) {
        const hasAny = task.labels.some((l) => filter.labels.has(l))
        if (!hasAny) continue
      }

      if (filter.assigneeAgentId && task.assigneeAgentId !== filter.assigneeAgentId) continue

      const bucket = (result[task.status] ??= [])
      bucket.push(task)
    }

    for (const status of Object.keys(result)) {
      result[status].sort((a, b) => a.sortOrder - b.sortOrder)
    }

    return result
  }, [tasks, searchQuery, filter])

  const visibleColumns = useMemo(
    () =>
      TASK_BOARD_COLUMNS.filter((col) => {
        if (hiddenColumns.has(col.status)) return false
        if (!showEmptyColumns) {
          const count = filteredTasksByStatus[col.status]?.length ?? 0
          if (count === 0) return false
        }
        return true
      }),
    [hiddenColumns, showEmptyColumns, filteredTasksByStatus],
  )

  const hiddenColumnList = useMemo(
    () => TASK_BOARD_COLUMNS.filter((col) => hiddenColumns.has(col.status)),
    [hiddenColumns],
  )

  // ─── Actions ────────────────────────────────────────

  const handleTaskClick = useCallback((taskId: string) => {
    setSelectedTaskId(taskId)
  }, [])

  const handleTaskContextMenu = useCallback(
    (e: React.MouseEvent, taskId: string) => {
      const task = tasks[taskId]
      if (!task) return
      setContextMenuTask(task)
      setContextMenuPos({ x: e.clientX, y: e.clientY })
      setContextMenuOpen(true)
    },
    [tasks],
  )

  const handleAddTask = useCallback((status: string) => {
    setEditingTask(null)
    setEditorDefaultStatus(status)
    setEditorOpen(true)
  }, [])

  const handleEditTask = useCallback((task: TaskRow) => {
    setEditingTask(task)
    setEditorOpen(true)
    setSelectedTaskId(null)
  }, [])

  const handleHideColumn = useCallback((status: string) => {
    setHiddenColumns((prev) => new Set([...prev, status]))
  }, [])

  const handleShowColumn = useCallback((status: string) => {
    setHiddenColumns((prev) => {
      const next = new Set(prev)
      next.delete(status)
      return next
    })
  }, [])

  const handleDropTask = useCallback(
    async (taskId: string, toStatus: string, beforeTaskId: string | null) => {
      const task = tasks[taskId]
      if (!task) return

      const bucket = filteredTasksByStatus[toStatus] ?? []
      let newSortOrder: number

      if (beforeTaskId) {
        const beforeIdx = bucket.findIndex((t) => t.id === beforeTaskId)
        if (beforeIdx === 0) {
          newSortOrder = bucket[0].sortOrder / 2
        } else {
          const prevOrder = bucket[beforeIdx - 1].sortOrder
          const currOrder = bucket[beforeIdx].sortOrder
          newSortOrder = (prevOrder + currOrder) / 2
        }
      } else if (bucket.length > 0) {
        newSortOrder = bucket[bucket.length - 1].sortOrder + 1000
      } else {
        newSortOrder = Date.now()
      }

      const prevStatus = task.status
      const prevSortOrder = task.sortOrder

      try {
        const updated = await moveTask(taskId, {
          status: toStatus,
          ifVersion: task.version,
          sortOrder: Math.round(newSortOrder),
        })
        upsertTask(updated)
        pushUndo(
          `移动任务到 ${TASK_STATUS_LABELS[toStatus] ?? toStatus}`,
          async () => {
            const reverted = await moveTask(taskId, {
              status: prevStatus,
              ifVersion: updated.version,
              sortOrder: prevSortOrder,
            })
            upsertTask(reverted)
          },
        )
      } catch {
        fetchTasks().then(setTasks).catch(() => {})
      }
    },
    [tasks, filteredTasksByStatus, upsertTask, pushUndo, setTasks],
  )

  const handleMoveTask = useCallback(
    async (taskId: string, status: TaskStatus, ifVersion: number) => {
      const task = tasks[taskId]
      if (!task) return
      const prevStatus = task.status
      try {
        const updated = await moveTask(taskId, { status, ifVersion })
        upsertTask(updated)
        pushUndo(
          `移动任务到 ${TASK_STATUS_LABELS[status] ?? status}`,
          async () => {
            const reverted = await moveTask(taskId, {
              status: prevStatus,
              ifVersion: updated.version,
            })
            upsertTask(reverted)
          },
        )
      } catch {
        fetchTasks().then(setTasks).catch(() => {})
      }
    },
    [tasks, upsertTask, pushUndo, setTasks],
  )

  const handleSetPriority = useCallback(
    async (taskId: string, priority: TaskPriority, ifVersion: number) => {
      const task = tasks[taskId]
      if (!task) return
      const prevPriority = task.priority
      try {
        const updated = await updateTask(taskId, { priority, ifVersion })
        upsertTask(updated)
        pushUndo(`设置优先级为 ${priority}`, async () => {
          const reverted = await updateTask(taskId, {
            priority: prevPriority,
            ifVersion: updated.version,
          })
          upsertTask(reverted)
        })
      } catch {
        fetchTasks().then(setTasks).catch(() => {})
      }
    },
    [tasks, upsertTask, pushUndo, setTasks],
  )

  const handleDuplicate = useCallback(
    async (taskId: string) => {
      const task = tasks[taskId]
      if (!task) return
      try {
        const created = await createTask({
          title: `${task.title} (副本)`,
          description: task.description,
          status: 'backlog',
          priority: task.priority,
          labels: task.labels,
        })
        upsertTask(created)
        pushUndo('复制任务', async () => {
          await deleteTask(created.id)
        })
      } catch {
        // ignore
      }
    },
    [tasks, upsertTask, pushUndo],
  )

  const handleDelete = useCallback(
    async (taskId: string) => {
      const task = tasks[taskId]
      if (!task) return
      const prevTask = { ...task }
      try {
        await deleteTask(taskId)
        removeTaskFromStore(taskId)
        pushUndo('删除任务', async () => {
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
      } catch {
        // ignore
      }
    },
    [tasks, removeTaskFromStore, pushUndo, upsertTask],
  )

  const handleStartScheduler = useCallback(async () => {
    try {
      const result = await startScheduler({})
      setSchedulerStatus(result.running, scheduler.pendingCount, scheduler.activeCount)
    } catch {
      // ignore
    }
  }, [scheduler, setSchedulerStatus])

  const handleStopScheduler = useCallback(async () => {
    try {
      const result = await stopScheduler()
      setSchedulerStatus(result.running, 0, 0)
    } catch {
      // ignore
    }
  }, [setSchedulerStatus])

  // ─── Keyboard shortcuts ─────────────────────────────

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault()
        popUndo()
        return
      }

      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return

      if (e.key === '/') {
        e.preventDefault()
        searchInputRef.current?.focus()
        return
      }

      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault()
        handleAddTask('backlog')
        return
      }

      if (e.key === 'Escape') {
        if (selectedTaskId) {
          setSelectedTaskId(null)
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [popUndo, selectedTaskId, handleAddTask])

  // ─── Render ─────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* Main board area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Toolbar — 按功能分组 */}
        <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2.5">
          {/* 左组：搜索 + 筛选 */}
          <div className="flex items-center gap-2">
            <div className="relative w-56">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={searchInputRef}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索任务... (按 / 聚焦)"
                className="h-8 pl-8 text-xs"
              />
            </div>
            <TaskBoardFilterMenu
              filter={filter}
              availableLabels={availableLabels}
              assigneeOptions={assigneeOptions}
              onFilterChange={setFilter}
            />
          </div>

          {/* 中组：列控制 */}
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
              >
                <Columns3 className="size-3.5" />
                列
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-[11px] text-muted-foreground">
                    显示/隐藏列
                  </DropdownMenuLabel>
                  {TASK_BOARD_COLUMNS.map((col) => (
                    <DropdownMenuCheckboxItem
                      key={col.status}
                      checked={!hiddenColumns.has(col.status)}
                      onCheckedChange={() => {
                        if (hiddenColumns.has(col.status)) handleShowColumn(col.status)
                        else handleHideColumn(col.status)
                      }}
                    >
                      {col.label}
                    </DropdownMenuCheckboxItem>
                  ))}
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuCheckboxItem
                  checked={showEmptyColumns}
                  onCheckedChange={() => setShowEmptyColumns(!showEmptyColumns)}
                >
                  显示空列
                </DropdownMenuCheckboxItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* 右组：操作 */}
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" className="h-8 gap-1.5" onClick={() => handleAddTask('backlog')}>
              <Plus className="size-3.5" />
              新建任务
            </Button>

            <div className="h-4 w-px bg-border" />

            {/* 调度器控制 */}
            {scheduler.running ? (
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={handleStopScheduler}>
                <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" />
                调度中
                <span className="ml-1 text-[10px] text-muted-foreground">
                  {scheduler.activeCount} 活跃 / {scheduler.pendingCount} 待处理
                </span>
                <Square className="ml-1 size-3 text-destructive" />
              </Button>
            ) : (
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={handleStartScheduler}>
                <Play className="size-3 text-emerald-500" />
                启动调度
              </Button>
            )}
          </div>
        </div>

        {/* Hidden columns bar */}
        {hiddenColumnList.length > 0 && (
          <TaskBoardHiddenColumns
            hiddenStatuses={hiddenColumnList.map((c) => c.status)}
            taskCountsByStatus={Object.fromEntries(
              Object.entries(taskIdsByStatus).map(([k, v]) => [k, v.length]),
            )}
            onShowColumn={handleShowColumn}
          />
        )}

        {/* Board columns */}
        <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto p-3">
          {visibleColumns.map((col, i) => (
            <div
              key={col.status}
              className={cn(
                'h-full task-col-enter',
                i <= 3 && `task-col-enter-delay-${i}`,
              )}
            >
              <TaskBoardColumn
                status={col.status}
                label={col.label}
                tasks={filteredTasksByStatus[col.status] ?? []}
                onTaskClick={handleTaskClick}
                onTaskContextMenu={handleTaskContextMenu}
                onAddTask={handleAddTask}
                onHideColumn={handleHideColumn}
                onDropTask={handleDropTask}
              />
            </div>
          ))}

          {/* Empty state */}
          {visibleColumns.length === 0 && (
            <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
              <div className="text-center">
                <EyeOff className="mx-auto mb-2 size-8 opacity-30" />
                <p>所有列都已隐藏</p>
                <Button
                  variant="link"
                  size="sm"
                  onClick={() => setHiddenColumns(new Set())}
                >
                  显示所有列
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Detail panel */}
      {selectedTaskId && (
        <TaskBoardDetail
          taskId={selectedTaskId}
          onClose={() => setSelectedTaskId(null)}
          onEdit={handleEditTask}
        />
      )}

      {/* Editor dialog */}
      <TaskBoardEditor
        open={editorOpen}
        onOpenChange={setEditorOpen}
        task={editingTask}
        defaultStatus={editorDefaultStatus}
      />

      {/* Context menu */}
      <TaskBoardContextMenu
        task={contextMenuTask}
        open={contextMenuOpen}
        onOpenChange={setContextMenuOpen}
        anchorPoint={contextMenuPos}
        onMoveTask={handleMoveTask}
        onSetPriority={handleSetPriority}
        onDuplicate={handleDuplicate}
        onDelete={handleDelete}
      />

      {/* Undo toast */}
      <TaskBoardUndoToast />
    </div>
  )
}
