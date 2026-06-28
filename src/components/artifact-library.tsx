'use client'

import { BookPlus, Check, FileText, FolderGit2, Image as ImageIcon, Layers, Loader2, Presentation, Search, Trash2 } from 'lucide-react'
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

/**
 * ArtifactLibrary — 全局产物库视图，挂在 Sidebar 内（mode='artifacts' 时显示）。
 *
 * 数据源是 /api/artifacts（轻量 meta），点击某项时按需 fetch 完整 content
 * 然后调用 openArtifactPreview 触发右侧预览。
 */
export function ArtifactLibrary({
  conversationId,
  showConversationTitle = true,
}: {
  conversationId?: string
  showConversationTitle?: boolean
}) {
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
      console.error('[ArtifactLibrary] load failed', err)
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

  const scopedItems = useMemo(
    () => (conversationId ? mergedItems.filter((a) => a.conversationId === conversationId) : mergedItems),
    [conversationId, mergedItems],
  )

  const grouped = useMemo(() => groupArtifactVersions(scopedItems), [scopedItems])

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
      console.error('[ArtifactLibrary] preview load failed', err)
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
      console.error('[ArtifactLibrary] ingest failed', err)
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
      console.error('[ArtifactLibrary] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* 搜索 */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索产物..."
            className="h-8 pl-8 text-xs"
          />
        </div>
        <div className="mt-1 text-[10px] text-muted-foreground">
          {loading ? '加载中…' : `共 ${filteredGroups.length} 组 / ${scopedItems.length} 个版本`}
        </div>
      </div>

      {/* 列表 */}
      <ScrollArea className="min-h-0 min-w-0 flex-1">
        <div className="min-w-0 space-y-1 overflow-hidden px-2 pb-2">
          {loading && mergedItems.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
              <Loader2 className="mr-2 size-3 animate-spin" /> 加载中
            </div>
          ) : filteredGroups.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground">
              {scopedItems.length === 0 || !query.trim() ? '还没有产物' : '没有匹配项'}
            </div>
          ) : (
            filteredGroups.map((group) => {
              const latest = group.latest
              const versions = group.versions
              const pendingVersionId = versions.some((a) => pendingPreviewId === a.id)
                ? pendingPreviewId
                : null
              const previewVersionId = versions.some((a) => previewArtifactId === a.id)
                ? previewArtifactId
                : null
              const selectedVersionId = pendingVersionId ?? previewVersionId ?? latest.id
              return (
                <div
                  key={group.rootId}
                  className="group min-w-0 overflow-hidden rounded-md px-2 py-2 transition hover:bg-accent"
                >
                  <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
                    <button
                      type="button"
                      onClick={() => void openPreview(latest.id)}
                      className="grid w-full min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-2 overflow-hidden text-left"
                      disabled={pendingPreviewId === latest.id}
                    >
                      <TypeIcon type={latest.type} />
                      <div className="min-w-0 overflow-hidden">
                        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-1.5 overflow-hidden">
                          <span className="block min-w-0 truncate text-xs font-medium" title={latest.title}>
                            {latest.title}
                          </span>
                          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                            v{latest.version}
                          </span>
                        </div>
                        <div className="mt-0.5 min-w-0 truncate text-[10px] text-muted-foreground">
                          <span className="font-mono">{latest.type}</span>
                          <span className="mx-1">·</span>
                          {versions.length > 1 ? `${versions.length} 个版本` : '1 个版本'}
                          <span className="mx-1">·</span>
                          {formatTime(latest.createdAt)}
                        </div>
                        {showConversationTitle && (
                          <div className="mt-0.5 min-w-0 truncate text-[10px] text-muted-foreground">
                            {latest.conversationTitle ?? '（无会话）'}
                          </div>
                        )}
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1.5 self-center">
                      {latest.type === 'document' && (
                        <IngestButton
                          status={ingestStatus[latest.id]}
                          onClick={() => void handleIngest(latest.id)}
                        />
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteTargetId(latest.id)
                        }}
                        title="删除最新版本"
                        className={cn(
                          'shrink-0 opacity-0 transition group-hover:opacity-100 hover:text-destructive',
                        )}
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  </div>

                  {versions.length > 1 && (
                    <div className="ml-6 mt-1 flex min-w-0 flex-wrap gap-1">
                      {versions.map((version) => {
                        const isVersionPending = pendingPreviewId === version.id
                        const isSelected = version.id === selectedVersionId
                        return (
                          <button
                            key={version.id}
                            type="button"
                            onClick={() => void openPreview(version.id)}
                            disabled={isVersionPending}
                            aria-pressed={isSelected}
                            title={`${version.title} · ${formatTime(version.createdAt)}`}
                            className={cn(
                              'inline-flex h-5 shrink-0 items-center gap-1 rounded border px-1.5 font-mono text-[10px] transition',
                              isSelected
                                ? 'border-primary/30 bg-primary/10 text-foreground'
                                : 'border-border/70 bg-background/60 text-muted-foreground hover:border-foreground/25 hover:text-foreground',
                            )}
                          >
                            {isVersionPending && <Loader2 className="size-2.5 animate-spin" />}
                            v{version.version}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </ScrollArea>

      {/* 删除确认 */}
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

function IngestButton({
  status,
  onClick,
}: {
  status?: 'loading' | 'done' | 'exists' | 'error'
  onClick: () => void
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
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      disabled={status === 'loading'}
      title={title}
      className={cn(
        'shrink-0 transition',
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
  const className = 'mt-0.5 size-4 shrink-0 text-muted-foreground'
  if (type === 'image') return <ImageIcon className={className} />
  if (type === 'document') return <FileText className={className} />
  if (type === 'ppt') return <Presentation className={className} />
  if (type === 'project') return <FolderGit2 className={className} />
  return <Layers className={className} />
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
