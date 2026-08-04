'use client'

import {
  BookPlus,
  Check,
  ChevronDown,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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

// ─── Types & Constants ──────────────────────────────────────────

type ArtifactTypeKey = 'image' | 'document' | 'ppt' | 'project' | 'web_app' | 'other'
type SortMode = 'latest' | 'versions' | 'type'

interface TypeMeta {
  label: string
  icon: typeof FileText
  badgeClass: string
  iconBgClass: string
  iconTextClass: string
  spanClass: string
  barColor: string
}

const TYPE_META: Record<ArtifactTypeKey, TypeMeta> = {
  image: {
    label: '图片',
    icon: ImageIcon,
    badgeClass: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
    iconBgClass: 'bg-blue-500/10',
    iconTextClass: 'text-blue-500',
    spanClass: 'row-span-2 sm:col-span-2',
    barColor: 'bg-blue-500',
  },
  document: {
    label: '文档',
    icon: FileText,
    badgeClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
    iconBgClass: 'bg-emerald-500/10',
    iconTextClass: 'text-emerald-500',
    spanClass: '',
    barColor: 'bg-emerald-500',
  },
  ppt: {
    label: 'PPT',
    icon: Presentation,
    badgeClass: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
    iconBgClass: 'bg-amber-500/10',
    iconTextClass: 'text-amber-500',
    spanClass: '',
    barColor: 'bg-amber-500',
  },
  project: {
    label: '项目',
    icon: FolderGit2,
    badgeClass: 'bg-violet-500/15 text-violet-600 dark:text-violet-400',
    iconBgClass: 'bg-violet-500/10',
    iconTextClass: 'text-violet-500',
    spanClass: 'sm:col-span-2',
    barColor: 'bg-violet-500',
  },
  web_app: {
    label: 'Web 应用',
    icon: Layers,
    badgeClass: 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-400',
    iconBgClass: 'bg-cyan-500/10',
    iconTextClass: 'text-cyan-500',
    spanClass: '',
    barColor: 'bg-cyan-500',
  },
  other: {
    label: '其他',
    icon: Layers,
    badgeClass: 'bg-muted text-muted-foreground',
    iconBgClass: 'bg-muted',
    iconTextClass: 'text-muted-foreground',
    spanClass: '',
    barColor: 'bg-muted-foreground',
  },
}

const TYPE_ORDER: ArtifactTypeKey[] = ['image', 'document', 'ppt', 'project', 'web_app', 'other']

const SORT_LABELS: Record<SortMode, string> = {
  latest: '最新优先',
  versions: '最多版本',
  type: '按类型',
}

function getTypeKey(type: string): ArtifactTypeKey {
  return (type in TYPE_META ? type : 'other') as ArtifactTypeKey
}

// ─── Main Component ──────────────────────────────────────────────

export function ArtifactMainPanel() {
  const [items, setItems] = useState<ArtifactListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<ArtifactTypeKey | 'all'>('all')
  const [sortMode, setSortMode] = useState<SortMode>('latest')
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

  const searchFiltered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return mergedItems
    return mergedItems.filter((a) => {
      const hay = `${a.title} ${a.type} v${a.version} ${a.conversationTitle ?? ''}`.toLowerCase()
      return hay.includes(q)
    })
  }, [mergedItems, query])

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of searchFiltered) {
      const key = getTypeKey(item.type)
      counts[key] = (counts[key] ?? 0) + 1
    }
    return counts
  }, [searchFiltered])

  const visibleTypes = useMemo(
    () => TYPE_ORDER.filter((key) => (typeCounts[key] ?? 0) > 0),
    [typeCounts],
  )

  useEffect(() => {
    if (typeFilter !== 'all' && !visibleTypes.includes(typeFilter)) {
      setTypeFilter('all')
    }
  }, [visibleTypes, typeFilter])

  const typeFiltered = useMemo(() => {
    if (typeFilter === 'all') return searchFiltered
    return searchFiltered.filter((a) => getTypeKey(a.type) === typeFilter)
  }, [searchFiltered, typeFilter])

  const grouped = useMemo(() => groupArtifactVersions(typeFiltered), [typeFiltered])

  const sorted = useMemo(() => {
    if (sortMode === 'latest') return grouped
    const arr = [...grouped]
    if (sortMode === 'versions') {
      arr.sort(
        (a, b) => b.versions.length - a.versions.length || b.latest.createdAt - a.latest.createdAt,
      )
    } else {
      arr.sort(
        (a, b) =>
          a.latest.type.localeCompare(b.latest.type) || b.latest.createdAt - a.latest.createdAt,
      )
    }
    return arr
  }, [grouped, sortMode])

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

  const totalCount = searchFiltered.length

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Ambient glow */}
      <div className="pointer-events-none absolute -top-20 right-0 size-64 rounded-full bg-primary/5 blur-3xl artifact-ambient" />

      {/* Header */}
      <div className="relative shrink-0 border-b px-6 py-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">产物库</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">管理所有会话中产生的产物</p>
          </div>
          <span className="text-xs text-muted-foreground">
            {loading ? '加载中…' : `${totalCount} 个产物`}
          </span>
        </div>

        {/* Type distribution bar */}
        {!loading && totalCount > 0 && (
          <div className="mt-3 flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
            {visibleTypes.map((key) => {
              const meta = TYPE_META[key]
              const count = typeCounts[key] ?? 0
              const pct = (count / totalCount) * 100
              return (
                <div
                  key={key}
                  className={cn('h-full transition-all duration-500', meta.barColor)}
                  style={{ width: `${pct}%` }}
                  title={`${meta.label}: ${count}`}
                />
              )
            })}
          </div>
        )}

        {/* Search + Sort */}
        <div className="mt-4 flex items-center gap-3">
          <div className="relative max-w-xs flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索产物..."
              className="pl-10"
            />
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="gap-1.5 text-xs" />}>
              {SORT_LABELS[sortMode]}
              <ChevronDown className="size-3" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setSortMode('latest')}>最新优先</DropdownMenuItem>
              <DropdownMenuItem onClick={() => setSortMode('versions')}>最多版本</DropdownMenuItem>
              <DropdownMenuItem onClick={() => setSortMode('type')}>按类型</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* Type filter bar */}
        {!loading && totalCount > 0 && (
          <div className="mt-3 flex items-center gap-1.5 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <FilterButton
              active={typeFilter === 'all'}
              onClick={() => setTypeFilter('all')}
              label="全部"
              count={totalCount}
            />
            {visibleTypes.map((key) => (
              <FilterButton
                key={key}
                active={typeFilter === key}
                onClick={() => setTypeFilter(key)}
                label={TYPE_META[key].label}
                count={typeCounts[key] ?? 0}
              />
            ))}
          </div>
        )}
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-6">
          {loading && mergedItems.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="ml-2 text-sm">加载中...</span>
            </div>
          ) : sorted.length === 0 ? (
            <EmptyState
              title={query.trim() ? '没有匹配项' : '还没有产物'}
              description={
                query.trim()
                  ? '试试其他关键词或切换类型筛选'
                  : '在会话中让 Agent 生成产物后会出现在这里'
              }
              hasQuery={!!query.trim()}
            />
          ) : (
            <div className="grid grid-flow-dense grid-cols-1 gap-4 auto-rows-[180px] sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {sorted.map((group, idx) => {
                const latest = group.latest
                const fullArtifact = artifactsById[latest.id]
                const thumbnailUrl =
                  fullArtifact && fullArtifact.content.type === 'image'
                    ? fullArtifact.content.url
                    : null
                const animDelay = Math.min(idx, 7)
                const animClass = `artifact-fade-up${animDelay > 0 ? `-delay-${animDelay}` : ''}`

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
                    thumbnailUrl={thumbnailUrl}
                    animClass={animClass}
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

// ─── Filter Button ───────────────────────────────────────────────

function FilterButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean
  onClick: () => void
  label: string
  count: number
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-3 text-xs font-medium transition-colors',
        active
          ? 'bg-primary text-primary-foreground'
          : 'bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {label}
      <span className="font-mono text-[10px] opacity-70">{count}</span>
    </button>
  )
}

// ─── Artifact Card ───────────────────────────────────────────────

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
  thumbnailUrl,
  animClass,
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
  thumbnailUrl: string | null
  animClass: string
  onOpen: () => void
  onIngest: () => void
  onDelete: () => void
  onVersionClick: (id: string) => void
  versions: ArtifactListItem[]
  pendingPreviewId: string | null
  previewArtifactId: string | null
}) {
  const typeKey = getTypeKey(type)
  const meta = TYPE_META[typeKey]
  const isImage = typeKey === 'image'
  const Icon = meta.icon

  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col overflow-hidden rounded-xl border bg-card transition-all duration-300',
        'hover:scale-[1.02] hover:border-primary/40 hover:shadow-md',
        previewing && 'border-primary/50 ring-1 ring-primary/20',
        meta.spanClass,
        animClass,
      )}
      onClick={onOpen}
    >
      {/* Image background (for image type) */}
      {isImage && (
        <>
          <div className="absolute inset-0 bg-gradient-to-br from-blue-500/10 via-muted to-muted/50" />
          {thumbnailUrl && (
            <div
              className="absolute inset-0 bg-cover bg-center transition-transform duration-700 group-hover:scale-105"
              style={{ backgroundImage: `url(${thumbnailUrl})` }}
            />
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-black/10 to-transparent" />
        </>
      )}

      {/* Content */}
      <div className={cn('relative flex h-full flex-col p-4', isImage && 'justify-end')}>
        {/* Header */}
        <div className="flex items-start gap-2.5">
          {!isImage && (
            <div
              className={cn(
                'flex size-9 shrink-0 items-center justify-center rounded-lg transition-transform duration-300 group-hover:scale-110',
                meta.iconBgClass,
              )}
            >
              <Icon className={cn('size-5', meta.iconTextClass)} />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <h4
                className={cn('truncate text-sm font-medium', isImage && 'text-white')}
                title={title}
              >
                {title}
              </h4>
              <span
                className={cn(
                  'shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px]',
                  isImage ? 'bg-white/20 text-white' : 'bg-muted text-muted-foreground',
                )}
              >
                v{version}
              </span>
            </div>
            <div
              className={cn(
                'mt-1 flex items-center gap-1.5 text-[10px]',
                isImage ? 'text-white/80' : 'text-muted-foreground',
              )}
            >
              <span className={cn('rounded px-1 py-0.5 text-[9px] font-medium', meta.badgeClass)}>
                {meta.label}
              </span>
              <span>·</span>
              <span>{versionCount > 1 ? `${versionCount} 个版本` : '1 个版本'}</span>
              {!isImage && (
                <>
                  <span>·</span>
                  <span>{formatTime(createdAt)}</span>
                </>
              )}
            </div>
            {conversationTitle && (
              <div
                className={cn(
                  'mt-0.5 truncate text-[10px]',
                  isImage ? 'text-white/70' : 'text-muted-foreground',
                )}
                title={conversationTitle}
              >
                {conversationTitle}
              </div>
            )}
            {isImage && (
              <div className="mt-0.5 text-[10px] text-white/60">{formatTime(createdAt)}</div>
            )}
          </div>
        </div>

        {/* Version pills */}
        {versionCount > 1 && (
          <div className={cn('flex flex-wrap gap-1', isImage ? 'mt-2' : 'mt-auto pt-3')}>
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
                    isImage
                      ? isSelected
                        ? 'border-white/50 bg-white/25 text-white'
                        : 'border-white/20 bg-black/20 text-white/70 hover:border-white/40 hover:text-white'
                      : isSelected
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
      </div>

      {/* Pending overlay */}
      {pending && (
        <div className="absolute right-2 top-2 z-10">
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Hover actions */}
      <div className="absolute right-2 top-2 z-10 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {type === 'document' && (
          <IngestButton
            status={ingestStatus}
            onClick={(e) => {
              e.stopPropagation()
              onIngest()
            }}
          />
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          title="删除产物"
          className={cn(
            'rounded p-1 transition',
            isImage
              ? 'bg-black/30 text-white/80 hover:bg-black/50 hover:text-white'
              : 'text-muted-foreground hover:bg-accent hover:text-destructive',
          )}
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  )
}

// ─── Ingest Button ───────────────────────────────────────────────

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

// ─── Empty State ─────────────────────────────────────────────────

function EmptyState({
  title,
  description,
  hasQuery,
}: {
  title: string
  description: string
  hasQuery: boolean
}) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="relative">
        <div className="absolute inset-0 rounded-3xl bg-primary/5 blur-2xl artifact-ambient" />
        <div className="relative flex size-20 items-center justify-center rounded-3xl border border-border/50 bg-gradient-to-br from-muted to-muted/50">
          <Package className="size-10 text-muted-foreground/40 artifact-empty-float" />
        </div>
      </div>
      <h3 className="mt-6 text-base font-medium">{title}</h3>
      <p className="mt-2 max-w-xs text-sm text-muted-foreground">{description}</p>
      {!hasQuery && (
        <p className="mt-1 text-xs text-muted-foreground/60">
          Agent 产出的代码、文档、图片等会自动收集到这里
        </p>
      )}
    </div>
  )
}

// ─── Utilities ───────────────────────────────────────────────────

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
