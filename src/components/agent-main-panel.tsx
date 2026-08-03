'use client'

import { Pencil, Plus, Search, Trash2, Users } from 'lucide-react'
import { useMemo, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { CreateAgentDialog } from '@/components/create-agent-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { AgentRow } from '@/db/schema'
import { deleteAgent as deleteAgentAPI } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAgentList, useAppStore } from '@/stores/app-store'

export function AgentMainPanel() {
  const agents = useAgentList()
  const removeAgent = useAppStore((s) => s.removeAgent)

  const [formOpen, setFormOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentRow | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const filteredAgents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return agents
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.adapterName.toLowerCase().includes(q),
    )
  }, [agents, searchQuery])

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
    try {
      await deleteAgentAPI(deleteTargetId)
      removeAgent(deleteTargetId)
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[AgentMainPanel] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b px-6 py-4">
        <h2 className="text-xl font-semibold">联系人</h2>
        <Button size="sm" className="gap-1.5 text-xs" onClick={openCreate}>
          <Plus className="size-3.5" />
          创建 Agent
        </Button>
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-6">
          {/* Search */}
          <div className="relative max-w-md">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索 Agent..."
              className="pl-10"
            />
          </div>

          {/* Section Header */}
          <div className="mt-6 flex items-center justify-between">
            <h3 className="text-sm font-medium text-muted-foreground">
              全部 <span className="ml-1 text-xs">({filteredAgents.length})</span>
            </h3>
          </div>

          {/* Grid */}
          {filteredAgents.length === 0 ? (
            <EmptyState
              title={searchQuery.trim() ? '没有匹配的 Agent' : '还没有 Agent'}
              description={searchQuery.trim() ? '试试其他关键词' : '点击右上角「创建 Agent」添加'}
            />
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filteredAgents.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  onEdit={() => openEdit(agent)}
                  onDelete={() => setDeleteTargetId(agent.id)}
                />
              ))}
            </div>
          )}
        </div>
      </ScrollArea>

      <CreateAgentDialog
        open={formOpen}
        onOpenChange={handleFormOpenChange}
        agent={editingAgent ?? undefined}
      />

      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除 Agent</DialogTitle>
            <DialogDescription>
              确定删除「{deleteTarget?.name}」吗？已使用该 Agent 的会话将无法继续使用它。该操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTargetId(null)}>
              取消
            </Button>
            <Button
              className="bg-destructive hover:bg-destructive/90"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {deleting ? '删除中...' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function AgentCard({
  agent,
  onEdit,
  onDelete,
}: {
  agent: AgentRow
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-xl border bg-card p-4 transition-all',
        'hover:border-primary/50 hover:shadow-sm',
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3">
        <AgentAvatar agent={agent} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h4 className="truncate text-sm font-medium">{agent.name}</h4>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {agent.isBuiltin && (
              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                内置
              </span>
            )}
            {agent.isOrchestrator && (
              <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] text-primary">
                Orchestrator
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Description */}
      {agent.description && (
        <p className="mt-3 line-clamp-2 text-xs text-muted-foreground">{agent.description}</p>
      )}

      {/* Adapter */}
      <div className="mt-3 flex items-center gap-1.5">
        <span className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {agent.adapterName}
        </span>
        {agent.capabilities.length > 0 && (
          <span className="text-[10px] text-muted-foreground">
            {agent.capabilities.length} 项能力
          </span>
        )}
      </div>

      {/* Hover actions */}
      <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition group-hover:opacity-100">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
          title="编辑 Agent"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Pencil className="size-3.5" />
        </button>
        {!agent.isBuiltin && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            title="删除 Agent"
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive"
          >
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
        <Users className="size-8 text-muted-foreground opacity-50" />
      </div>
      <h3 className="mt-4 text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}
