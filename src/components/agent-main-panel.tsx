'use client'

import { Brain, Pencil, Plus, Search, Trash2, Users } from 'lucide-react'
import { useMemo, useState } from 'react'

import { AgentAvatar, getAgentColor } from '@/components/agent-avatar'
import { CreateAgentDialog } from '@/components/create-agent-dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { AgentRow } from '@/db/schema'
import { cn } from '@/lib/utils'
import { deleteAgent as deleteAgentAPI } from '@/lib/api'
import { useAgentList, useAppStore } from '@/stores/app-store'

type FilterKey = 'all' | 'custom' | 'local' | 'memory'

interface FilterOption {
  key: FilterKey
  label: string
  count: number
}

function isCustomAgent(agent: AgentRow): boolean {
  return agent.adapterName === 'custom'
}

const ADAPTER_LABELS: Record<string, string> = {
  'claude-code': 'Claude Code',
  'codex': 'Codex',
  'custom': 'Custom SDK',
  'mock': 'Mock',
}

export function AgentMainPanel() {
  const allAgents = useAgentList()
  const removeAgent = useAppStore((s) => s.removeAgent)

  const [formOpen, setFormOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentRow | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeFilter, setActiveFilter] = useState<FilterKey>('all')

  const agents = useMemo(
    () => allAgents.filter((a) => !a.isBuiltin),
    [allAgents],
  )

  const stats = useMemo(() => {
    const custom = agents.filter(isCustomAgent).length
    const local = agents.filter((a) => !isCustomAgent(a)).length
    const memory = agents.filter((a) => a.memoryEnabled).length
    return { total: agents.length, custom, local, memory }
  }, [agents])

  const filterOptions: FilterOption[] = useMemo(
    () => [
      { key: 'all', label: '全部', count: stats.total },
      { key: 'custom', label: '自建', count: stats.custom },
      { key: 'local', label: '本地', count: stats.local },
      { key: 'memory', label: '有记忆', count: stats.memory },
    ],
    [stats],
  )

  const filteredAgents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return agents.filter((a) => {
      const matchesText =
        !q ||
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.adapterName.toLowerCase().includes(q)

      if (!matchesText) return false

      switch (activeFilter) {
        case 'custom':
          return isCustomAgent(a)
        case 'local':
          return !isCustomAgent(a)
        case 'memory':
          return a.memoryEnabled
        default:
          return true
      }
    })
  }, [agents, searchQuery, activeFilter])

  const { customAgents, localAgents } = useMemo(() => {
    const custom = filteredAgents.filter(isCustomAgent)
    const local = filteredAgents.filter((a) => !isCustomAgent(a))
    return { customAgents: custom, localAgents: local }
  }, [filteredAgents])

  const deleteTarget = deleteTargetId ? agents.find((a) => a.id === deleteTargetId) : null

  const openCreate = () => {
    setEditingAgent(null)
    setFormOpen(true)
  }

  const openEdit = (agent: AgentRow) => {
    setEditingAgent(agent)
    setFormOpen(true)
  }

  const handleFormOpenChange = (open: boolean) => {
    setFormOpen(open)
    if (!open) setEditingAgent(null)
  }

  const confirmDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteAgentAPI(deleteTargetId)
      removeAgent(deleteTargetId)
      setDeleteSuccess(`已删除「${deleteTarget?.name ?? 'Agent'}」`)
      setDeleteTargetId(null)
      setTimeout(() => setDeleteSuccess(null), 3000)
    } catch (err) {
      console.error('[AgentMainPanel] delete failed', err)
      setDeleteError('删除失败，请稍后重试')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header */}
      <header className="agent-fade-up shrink-0 border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <h2 className="text-base font-medium">你的团队</h2>
            <span className="text-xs tabular-nums text-muted-foreground">
              {stats.total} 人
            </span>
          </div>
          <div className="flex items-center gap-4">
            <div className="hidden items-center gap-3 text-xs tabular-nums text-muted-foreground sm:flex">
              <span>自建 {stats.custom}</span>
              <span className="text-border">|</span>
              <span>本地 {stats.local}</span>
              <span className="text-border">|</span>
              <span className="flex items-center gap-1">
                <Brain className="size-3" />
                {stats.memory}
              </span>
            </div>
            <Button
              size="sm"
              className="group/add gap-1.5 overflow-hidden shadow-[var(--shadow-sm),var(--inset-hi)] transition-all duration-200 hover:shadow-[var(--shadow-md),var(--inset-hi)] hover:brightness-110 active:scale-[0.97]"
              onClick={openCreate}
            >
              <span className="relative flex size-3.5 items-center justify-center">
                <Plus className="size-3.5 transition-transform duration-300 group-hover/add:rotate-90" />
              </span>
              添加成员
            </Button>
          </div>
        </div>
      </header>

      {/* Search + Filter */}
      <div className="agent-fade-up agent-fade-up-delay-1 shrink-0 space-y-2 border-b border-border px-6 py-3">
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索成员..."
              className="pl-9"
            />
          </div>
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {filterOptions.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setActiveFilter(opt.key)}
              className={cn(
                'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-4xl px-2.5 text-xs font-medium transition-colors',
                activeFilter === opt.key
                  ? 'bg-primary/10 text-primary'
                  : 'bg-muted text-muted-foreground hover:bg-muted/70 hover:text-foreground',
              )}
            >
              {opt.label}
              <span className="tabular-nums opacity-70">{opt.count}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-4xl p-6">
          {/* Delete success toast */}
          {deleteSuccess && (
            <div
              role="status"
              aria-live="polite"
              className="agent-fade-up mb-4 flex items-center gap-2 rounded-lg bg-success/10 px-3 py-2 text-xs text-success"
            >
              <span className="size-1.5 shrink-0 rounded-full bg-success" />
              {deleteSuccess}
            </div>
          )}

          {/* Custom (SDK) Agents section */}
          {customAgents.length > 0 && (
            <RosterSection
              title="自建成员"
              count={customAgents.length}
              delayClass="agent-fade-up agent-fade-up-delay-2"
            >
              {customAgents.map((agent) => (
                <RosterRow
                  key={agent.id}
                  agent={agent}
                  onEdit={() => openEdit(agent)}
                  onDelete={() => setDeleteTargetId(agent.id)}
                />
              ))}
            </RosterSection>
          )}

          {/* Local (CLI) Agents section */}
          {localAgents.length > 0 && (
            <RosterSection
              title="本地成员"
              count={localAgents.length}
              delayClass="agent-fade-up agent-fade-up-delay-3"
            >
              {localAgents.map((agent) => (
                <RosterRow
                  key={agent.id}
                  agent={agent}
                  onEdit={() => openEdit(agent)}
                  onDelete={() => setDeleteTargetId(agent.id)}
                />
              ))}
            </RosterSection>
          )}

          {/* Empty state */}
          {filteredAgents.length === 0 && (
            <EmptyState
              title={searchQuery.trim() || activeFilter !== 'all' ? '没有匹配的成员' : '团队空空如也'}
              description={searchQuery.trim() || activeFilter !== 'all' ? '试试其他关键词或筛选条件' : '添加你的第一位搭档，开始协作'}
              showCTA={!searchQuery.trim() && activeFilter === 'all'}
              onCTA={openCreate}
            />
          )}
        </div>
      </ScrollArea>

      <CreateAgentDialog
        open={formOpen}
        onOpenChange={handleFormOpenChange}
        agent={editingAgent ?? undefined}
      />

      <AlertDialog
        open={!!deleteTargetId}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTargetId(null)
            setDeleteError(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>移除成员</AlertDialogTitle>
            <AlertDialogDescription>
              确定将「{deleteTarget?.name}」移出团队吗？已使用该成员的会话将无法继续使用它。该操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p className="text-sm text-destructive" role="alert">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive/10 text-destructive hover:bg-destructive/20"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {deleting ? '移除中...' : '移除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function RosterSection({
  title,
  count,
  delayClass,
  children,
}: {
  title: string
  count: number
  delayClass: string
  children: React.ReactNode
}) {
  return (
    <section className={cn('mb-8', delayClass)}>
      <div className="mb-1 flex items-center gap-2 px-1">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        <span className="text-xs tabular-nums text-muted-foreground/60">{count}</span>
      </div>
      <div className="divide-y divide-border">{children}</div>
    </section>
  )
}

function RosterRow({
  agent,
  onEdit,
  onDelete,
}: {
  agent: AgentRow
  onEdit: () => void
  onDelete: () => void
}) {
  const accentColor = getAgentColor(agent.id)
  const adapterLabel = ADAPTER_LABELS[agent.adapterName] ?? agent.adapterName
  const visibleTools = agent.toolNames.slice(0, 3)
  const extraTools = agent.toolNames.length - visibleTools.length

  return (
    <div
      className="group relative flex cursor-pointer items-center gap-4 px-3 py-3.5 transition-colors duration-150 hover:bg-muted/30"
      onClick={onEdit}
    >
      {/* Avatar with identity ring */}
      <div className="relative shrink-0">
        <div className={cn('rounded-full ring-2 ring-offset-2 ring-offset-background', accentColor, 'ring-opacity-30')}>
          <AgentAvatar agent={agent} size="lg" />
        </div>
      </div>

      {/* Name + description */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h4 className="truncate text-sm font-medium">{agent.name}</h4>
          {agent.isOrchestrator && (
            <span className="shrink-0 rounded-4xl bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
              Orchestrator
            </span>
          )}
        </div>
        {agent.description && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {agent.description}
          </p>
        )}
      </div>

      {/* Skill chips */}
      <div className="hidden min-w-0 max-w-[280px] flex-wrap items-center gap-1 md:flex">
        {visibleTools.map((tool) => (
          <span
            key={tool}
            className="shrink-0 rounded-4xl bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
          >
            {tool}
          </span>
        ))}
        {extraTools > 0 && (
          <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/60">
            +{extraTools}
          </span>
        )}
        {agent.skillNames.length > 0 && (
          <span className="shrink-0 rounded-4xl bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            {agent.skillNames.length} 技能
          </span>
        )}
        {agent.memoryEnabled && (
          <span className="flex shrink-0 items-center gap-0.5 text-[11px] text-primary">
            <Brain className="size-3" />
            记忆
          </span>
        )}
      </div>

      {/* Adapter label */}
      <div className="hidden shrink-0 items-center lg:block">
        <span className="font-mono text-xs text-muted-foreground/60">
          {adapterLabel}
        </span>
      </div>

      {/* Actions */}
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          aria-label="编辑"
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
          className="rounded-lg p-1.5 text-muted-foreground/20 transition-colors duration-150 hover:bg-accent hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="移除"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          className="rounded-lg p-1.5 text-muted-foreground/20 transition-colors duration-150 hover:bg-destructive/10 hover:text-destructive focus-visible:text-destructive focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  )
}

function EmptyState({
  title,
  description,
  showCTA,
  onCTA,
}: {
  title: string
  description: string
  showCTA?: boolean
  onCTA?: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-border py-20 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-primary/5">
        <Users className="size-7 text-primary/60" />
      </div>
      <div className="space-y-1">
        <h3 className="text-balance text-sm font-medium">{title}</h3>
        <p className="text-pretty text-xs text-muted-foreground">{description}</p>
      </div>
      {showCTA && onCTA && (
        <Button size="sm" className="gap-1.5" onClick={onCTA}>
          <Plus className="size-3.5" />
          添加第一位搭档
        </Button>
      )}
    </div>
  )
}
