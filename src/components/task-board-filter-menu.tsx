'use client'

import { useCallback } from 'react'
import { Filter, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuLabel,
  DropdownMenuCheckboxItem,
} from '@/components/ui/dropdown-menu'
import type { TaskPriority, TaskStatus } from '@/shared/types'
import {
  TASK_BOARD_COLUMNS,
  TASK_PRIORITIES,
  TASK_STATUS_LABELS,
  TASK_PRIORITY_LABELS,
} from '@/shared/task-board-config'

export interface TaskFilter {
  statuses: Set<string>
  priorities: Set<string>
  labels: Set<string>
  assigneeAgentId: string | null
}

interface TaskBoardFilterMenuProps {
  filter: TaskFilter
  availableLabels: string[]
  assigneeOptions: { id: string; name: string }[]
  onFilterChange: (filter: TaskFilter) => void
}

export function TaskBoardFilterMenu({
  filter,
  availableLabels,
  assigneeOptions,
  onFilterChange,
}: TaskBoardFilterMenuProps) {
  const activeCount =
    (filter.statuses.size > 0 ? 1 : 0) +
    (filter.priorities.size > 0 ? 1 : 0) +
    (filter.labels.size > 0 ? 1 : 0) +
    (filter.assigneeAgentId ? 1 : 0)

  const toggleStatus = useCallback(
    (status: string) => {
      const next = new Set(filter.statuses)
      if (next.has(status)) next.delete(status)
      else next.add(status)
      onFilterChange({ ...filter, statuses: next })
    },
    [filter, onFilterChange],
  )

  const togglePriority = useCallback(
    (priority: string) => {
      const next = new Set(filter.priorities)
      if (next.has(priority)) next.delete(priority)
      else next.add(priority)
      onFilterChange({ ...filter, priorities: next })
    },
    [filter, onFilterChange],
  )

  const toggleLabel = useCallback(
    (label: string) => {
      const next = new Set(filter.labels)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      onFilterChange({ ...filter, labels: next })
    },
    [filter, onFilterChange],
  )

  const setAssignee = useCallback(
    (agentId: string | null) => {
      onFilterChange({ ...filter, assigneeAgentId: agentId })
    },
    [filter, onFilterChange],
  )

  const clearAll = useCallback(() => {
    onFilterChange({
      statuses: new Set(),
      priorities: new Set(),
      labels: new Set(),
      assigneeAgentId: null,
    })
  }, [onFilterChange])

  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger
          className="relative inline-flex h-7 items-center gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
        >
          <Filter className="size-3.5" />
          筛选
          {activeCount > 0 && (
            <Badge
              variant="secondary"
              className="ml-1 h-4 min-w-4 rounded-full px-1 text-[10px]"
            >
              {activeCount}
            </Badge>
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          {/* Status filter */}
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-[11px] text-muted-foreground">
              状态
            </DropdownMenuLabel>
            {TASK_BOARD_COLUMNS.map((col) => (
              <DropdownMenuCheckboxItem
                key={col.status}
                checked={filter.statuses.has(col.status)}
                onCheckedChange={() => toggleStatus(col.status)}
              >
                {TASK_STATUS_LABELS[col.status]}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>

          <DropdownMenuSeparator />

          {/* Priority filter */}
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-[11px] text-muted-foreground">
              优先级
            </DropdownMenuLabel>
            {TASK_PRIORITIES.map((p) => (
              <DropdownMenuCheckboxItem
                key={p}
                checked={filter.priorities.has(p)}
                onCheckedChange={() => togglePriority(p)}
              >
                {TASK_PRIORITY_LABELS[p]}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>

          {availableLabels.length > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel className="text-[11px] text-muted-foreground">
                  标签
                </DropdownMenuLabel>
                {availableLabels.map((label) => (
                  <DropdownMenuCheckboxItem
                    key={label}
                    checked={filter.labels.has(label)}
                    onCheckedChange={() => toggleLabel(label)}
                  >
                    {label}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuGroup>
            </>
          )}

          {assigneeOptions.length > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel className="text-[11px] text-muted-foreground">
                  分配给
                </DropdownMenuLabel>
                <DropdownMenuItem onClick={() => setAssignee(null)}>
                  全部
                </DropdownMenuItem>
                {assigneeOptions.map((a) => (
                  <DropdownMenuItem
                    key={a.id}
                    onClick={() => setAssignee(a.id)}
                  >
                    {a.name}
                    {filter.assigneeAgentId === a.id && ' ✓'}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </>
          )}

          {activeCount > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={clearAll} className="text-destructive">
                <X className="mr-2 size-3" />
                清除筛选
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
