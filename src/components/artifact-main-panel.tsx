'use client'

import {
  BookPlus,
  Check,
  FileText,
  FolderGit2,
  Image as ImageIcon,
  Layers,
  Loader2,
  Package,
  Presentation,
  Search,
  Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  deleteArtifact,
  fetchArtifact,
  fetchArtifacts,
  ingestArtifactToKnowledgeBase,
  type ArtifactListItem,
} from '@/lib/api'
import { groupArtifactVersions } from '@/lib/artifact-groups'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

export function ArtifactMainPanel() {
  const [items, setItems] = useState<ArtifactListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [pendingPreviewId, setPendingPreviewId] = useState<string | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [ingestStatus, setIngestStatus] = useState<
    Record<string, 'loading' | 'done' | 'exists' | 'error'>
  >({})

  const upsertArtifact = useAppStore((s) => s.upsertArtifact)
  const openArtifactPreview = useAppStore((s) => s.openArtifactPreview)
  const previewArtifactId = useAppStore((s) => s.previewArtifactId)
  const artifactsById = useAppStore((s) => s.artifacts)
  const removeArtifact = useAppStore((s) => s.removeArtifact)
  const storeArtifacts = useAppStore((s) => s.artifacts)
  const conversations = useAppStore((s) => s.conversations)

  const refresh = async () => {
    setLoading(true)
    try {
      const list = await fetchArtifacts()
      setItems(list)
    } catch (err) {
      console.error('[ArtifactMainPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const mergedItems = useMemo(() => {
    const byId = new Map<string, ArtifactListItem>()
    for (const item of items) byId.set(item.id, item)
    for (const artifact of Object.values(storeArtifacts)) {
      const existing = byId.get(artifact.id)
      byId.set(artifact.id, {
        id: artifact.id,
        conversationId: artifact.conversationId,
        conversationTitle:
          conversations[artifact.conversationId]?.title ?? existing?.conversationTitle ?? null,
        type: artifact.type,
        title: artifact.title,
        version: artifact.version,
        parentArtifactId: artifact.parentArtifactId ?? existing?.parentArtifactId ?? null,
        createdByAgentId: artifact.createdByAgentId,
        createdAt: artifact.createdAt,
      })
    }
    return [...byId.values()].sort((a, b) => b.createdAt - a.createdAt)
  }, [conversations, items, storeArtifacts])

  const grouped = useMemo(() => groupArtifactVersions(mergedItems), [mergedItems])

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return grouped
    return grouped.filter((group) => {
      return group.versions.some((a) => {
        const hay = `${a.title} ${a.type} v${a.version} ${a.conversationTitle ?? ''}`.toLowerCase()
        return hay.includes(q)
      })
    })
  }, [grouped, query])

  const openPreview = async (id: string) => {
    if (previewArtifactId === id) return
    if (artifactsById[id]) {
      openArtifactPreview(id)
      return
    }
    setPendingPreviewId(id)
    try {
      const full = await fetchArtifact(id)
      upsertArtifact(full)
      openArtifactPreview(id)
    } catch (err) {
      console.error('[ArtifactMainPanel] preview load failed', err)
    } finally {
      setPendingPreviewId(null)
    }
  }

  const handleIngest = async (id: string) => {
    setIngestStatus((s) => ({ ...s, [id]: 'loading' }))
    try {
      let artifact = artifactsById[id]
      if (!artifact) {
        artifact = await fetchArtifact(id)
        upsertArtifact(artifact)
      }
      const res = await ingestArtifactToKnowledgeBase(artifact)
      setIngestStatus((s) => ({ ...s, [id]: res.alreadyImported ? 'exists' : 'done' }))
    } catch (err) {
      console.error('[ArtifactMainPanel] ingest failed', err)
      setIngestStatus((s) => ({ ...s, [id]: 'error' }))
    }
  }

  const deleteTarget = deleteTargetId ? mergedItems.find((a) => a.id === deleteTargetId) : null

  const confirmDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteArtifact(deleteTargetId)
      removeArtifact(deleteTargetId)
      setItems((arr) => arr.filter((a) => a.id !== deleteTargetId))
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[ArtifactMainPanel] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b px-6 py-4">
        <h2 className="text-xl font-semibold">产物库</h2>
        <span className="text-xs text-muted-foreground">
          {loading ? '加载中…' : `共 ${filteredGroups.length} 组 / ${mergedItems.length} 个版本`}
        </span>
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-6">
          {/* Search */}
          <div className="relative max-w-md">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索产物..."
              className="pl-10"
            />
          </div>

          {/* Section Header */}
          <div className="mt-6 flex items-center justify-between">
            <h3 className="text-sm font-medium text-muted-foreground">
              全部产物 <span className="ml-1 text-xs">({filteredGroups.length})</span>
            </h3>
          </div>

          {/* Grid */}
          {loading && mergedItems.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="ml-2 text-sm">加载中...</span>
            </div>
          ) : filteredGroups.length === 0 ? (
            <EmptyState
              title={query.trim() ? '没有匹配项' : '还没有产物'}
              description={query.trim() ? '试试其他关键词' : '在会话中让 Agent 生成产物后会出现在这里'}
            />
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filteredGroups.map((group) => {
                const latest = group.latest
                return (
                  <ArtifactCard
                    key={group.rootId}
                    title={latest.title}
                    type={latest.type}
                    version={latest.version}
                    versionCount={group.versions.length}
                    conversationTitle={latest.conversationTitle}
                    createdAt={latest.createdAt}
                    previewing={previewArtifactId === latest.id}
                    pending={pendingPreviewId === latest.id}
                    ingestStatus={ingestStatus[latest.id]}
                    onOpen={() => void openPreview(latest.id)}
                    onIngest={() => void handleIngest(latest.id)}
                    onDelete={() => setDeleteTargetId(latest.id)}
                    onVersionClick={(vid) => void openPreview(vid)}
                    versions={group.versions}
                    pendingPreviewId={pendingPreviewId}
                    previewArtifactId={previewArtifactId}
                  />
                )
              })}
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Delete confirmation */}
      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除产物</DialogTitle>
            <DialogDescription>
              {deleteTarget
                ? `确定删除「${deleteTarget.title}」v${deleteTarget.version} 吗？聊天里指向该版本的卡片将不再可预览。该操作不可恢复。`
                : '确定删除这个产物版本吗？该操作不可恢复。'}
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

function ArtifactCard({
  title,
  type,
  version,
  versionCount,
  conversationTitle,
  createdAt,
  previewing,
  pending,
  ingestStatus,
  onOpen,
  onIngest,
  onDelete,
  onVersionClick,
  versions,
  pendingPreviewId,
  previewArtifactId,
}: {
  title: string
  type: string
  version: number
  versionCount: number
  conversationTitle: string | null
  createdAt: number
  previewing: boolean
  pending: boolean
  ingestStatus?: 'loading' | 'done' | 'exists' | 'error'
  onOpen: () => void
  onIngest: () => void
  onDelete: () => void
  onVersionClick: (id: string) => void
  versions: ArtifactListItem[]
  pendingPreviewId: string | null
  previewArtifactId: string | null
}) {
  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-xl border bg-card p-4 transition-all',
        'hover:border-primary/50 hover:shadow-sm',
        previewing && 'border-primary/50 ring-1 ring-primary/20',
      )}
      onClick={onOpen}
    >
      {/* Header */}
      <div className="flex items-start gap-3">
        <TypeIcon type={type} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h4 className="truncate text-sm font-medium" title={title}>{title}</h4>
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              v{version}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <span className="font-mono">{type}</span>
            <span>·</span>
            <span>{versionCount > 1 ? `${versionCount} 个版本` : '1 个版本'}</span>
            <span>·</span>
            <span>{formatTime(createdAt)}</span>
          </div>
          {conversationTitle && (
            <div className="mt-0.5 truncate text-[10px] text-muted-foreground" title={conversationTitle}>
              {conversationTitle}
            </div>
          )}
        </div>
      </div>

      {/* Version pills */}
      {versionCount > 1 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {versions.map((v) => {
            const isPending = pendingPreviewId === v.id
            const isSelected = previewArtifactId === v.id
            return (
              <button
                key={v.id}
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onVersionClick(v.id)
                }}
                disabled={isPending}
                title={`${v.title} · ${formatTime(v.createdAt)}`}
                className={cn(
                  'inline-flex h-5 shrink-0 items-center gap-1 rounded border px-1.5 font-mono text-[10px] transition',
                  isSelected
                    ? 'border-primary/30 bg-primary/10 text-foreground'
                    : 'border-border/70 bg-background/60 text-muted-foreground hover:border-foreground/25 hover:text-foreground',
                )}
              >
                {isPending && <Loader2 className="size-2.5 animate-spin" />}
                v{v.version}
              </button>
            )
          })}
        </div>
      )}

      {/* Pending overlay */}
      {pending && (
        <div className="absolute right-2 top-2">
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Hover actions */}
      <div className="absolute right-2 top-2 flex gap-0.5 opacity-0 transition group-hover:opacity-100">
        {type === 'document' && (
          <IngestButton status={ingestStatus} onClick={(e) => { e.stopPropagation(); onIngest() }} />
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          title="删除产物"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  )
}

function IngestButton({
  status,
  onClick,
}: {
  status?: 'loading' | 'done' | 'exists' | 'error'
  onClick: (e: React.MouseEvent) => void
}) {
  const settled = status === 'done' || status === 'exists' || status === 'error'
  const title =
    status === 'done'
      ? '已加入知识库'
      : status === 'exists'
        ? '该产物已在知识库中'
        : status === 'error'
          ? '加入失败，点击重试'
          : '加入知识库'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={status === 'loading'}
      title={title}
      className={cn(
        'shrink-0 rounded p-1 transition',
        settled ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        status === 'done' || status === 'exists'
          ? 'text-success'
          : status === 'error'
            ? 'text-destructive'
            : 'hover:text-primary',
      )}
    >
      {status === 'loading' ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : status === 'done' || status === 'exists' ? (
        <Check className="size-3.5" />
      ) : (
        <BookPlus className="size-3.5" />
      )}
    </button>
  )
}

function TypeIcon({ type }: { type: string }) {
  const className = 'mt-0.5 size-8 shrink-0 text-muted-foreground'
  if (type === 'image') return <ImageIcon className={className} />
  if (type === 'document') return <FileText className={className} />
  if (type === 'ppt') return <Presentation className={className} />
  if (type === 'project') return <FolderGit2 className={className} />
  return <Layers className={className} />
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
        <Package className="size-8 text-muted-foreground opacity-50" />
      </div>
      <h3 className="mt-4 text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  if (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  ) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
