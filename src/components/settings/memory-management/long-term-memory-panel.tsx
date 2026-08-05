'use client'

import {
  Brain,
  CalendarDays,
  FileText,
  Filter,
  Folder,
  Hash,
  Info,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Star,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  type MemoryFileItem,
  type ProactiveTopic,
  deleteMemoryFile,
  fetchMemoryFiles,
  fetchProactiveTopics,
  readMemoryFile,
  searchMemoryFiles,
  triggerAutoDream,
  writeMemoryFile,
} from '@/lib/api/memory'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { cn } from '@/lib/utils'

const BUCKETS = ['all', 'procedure', 'wiki', 'daily'] as const
type BucketFilter = (typeof BUCKETS)[number]

const BUCKET_CONFIG: Record<
  string,
  { label: string; dot: string; badge: string; bar: string; border: string; borderHover: string }
> = {
  procedure: {
    label: '经验',
    dot: 'bg-blue-500',
    badge: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    bar: 'bg-blue-500',
    border: 'border-blue-500/35',
    borderHover: 'hover:border-blue-500/60',
  },
  wiki: {
    label: '知识',
    dot: 'bg-emerald-500',
    badge: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    bar: 'bg-emerald-500',
    border: 'border-emerald-500/35',
    borderHover: 'hover:border-emerald-500/60',
  },
  daily: {
    label: '日常',
    dot: 'bg-amber-500',
    badge: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    bar: 'bg-amber-500',
    border: 'border-amber-500/35',
    borderHover: 'hover:border-amber-500/60',
  },
}

type BucketCfg = {
  label: string
  dot: string
  badge: string
  bar: string
  border: string
  borderHover: string
}

function getBucketConfig(bucket: string): BucketCfg {
  return (
    BUCKET_CONFIG[bucket] ?? {
      label: bucket,
      dot: 'bg-zinc-400',
      badge: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-400',
      bar: 'bg-zinc-400',
      border: 'border-border',
      borderHover: 'hover:border-primary/30',
    }
  )
}

const MACHINE_NAME_RE = /^session_[A-Za-z0-9_-]+$/i
const PLACEHOLDER_DESC_RE = /^(Memory from conversation\b|Memory card\b)/i

/** 从正文预览里抽第一条可读事实（去掉 markdown 列表符） */
function firstFactFromPreview(preview: string): string {
  for (const raw of preview.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith('##')) continue
    if (line.startsWith('---')) continue
    if (line.startsWith('*Source') || line.startsWith('- *Source')) continue
    const fact = line.replace(/^[-*]\s+/, '').trim()
    if (fact) return fact
  }
  return ''
}

function isMachineName(name: string): boolean {
  return MACHINE_NAME_RE.test(name.trim())
}

function isPlaceholderDescription(description: string): boolean {
  return PLACEHOLDER_DESC_RE.test(description.trim())
}

const TITLE_MAX = 18

function shortTitle(text: string, max = TITLE_MAX): string {
  const t = text.trim()
  if (t.length <= max) return t
  // 尽量在标点/空格处截断，避免生硬半句
  const slice = t.slice(0, max)
  const cut = Math.max(
    slice.lastIndexOf('，'),
    slice.lastIndexOf('。'),
    slice.lastIndexOf('；'),
    slice.lastIndexOf('、'),
    slice.lastIndexOf(' '),
    slice.lastIndexOf('：'),
    slice.lastIndexOf(':'),
  )
  const base = cut >= Math.floor(max * 0.5) ? slice.slice(0, cut) : slice
  return base.replace(/[，。；、:\s]+$/u, '')
}

/** 卡片标题：机器名时用正文首条事实兜底；统一短标题，避免省略号拖尾 */
function cardTitle(item: MemoryFileItem): string {
  const name = item.name?.trim() || ''
  if (name && !isMachineName(name)) return shortTitle(name)
  const fromBody = firstFactFromPreview(item.bodyPreview || '')
  if (fromBody) return shortTitle(fromBody)
  return shortTitle(name || '未命名记忆')
}

/** description 优先（跳过占位文案），否则 bodyPreview */
function cardPreview(item: MemoryFileItem): string {
  const desc = item.description?.trim()
  if (desc && !isPlaceholderDescription(desc)) return desc
  const fromBody = firstFactFromPreview(item.bodyPreview || '')
  if (fromBody) return fromBody
  return item.bodyPreview?.trim() || ''
}

export function LongTermMemoryPanel() {
  const [items, setItems] = useState<MemoryFileItem[]>([])
  const [loading, setLoading] = useState(false)
  const [filterBucket, setFilterBucket] = useState<BucketFilter>('all')
  const [filterAgent, setFilterAgent] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState(false)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [fileDetail, setFileDetail] = useState<{
    path: string
    name: string
    body: string
    description: string
    tags: string[]
    importance: number
    bucket: string
  } | null>(null)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editBody, setEditBody] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editImportance, setEditImportance] = useState('')
  const [editBucket, setEditBucket] = useState('wiki')
  const [saving, setSaving] = useState(false)
  const [deleteConfirmPath, setDeleteConfirmPath] = useState<string | null>(null)
  const [proactiveTopics, setProactiveTopics] = useState<ProactiveTopic[]>([])
  const [dreaming, setDreaming] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      if (searchMode && searchQuery.trim()) {
        const resp = await searchMemoryFiles({ query: searchQuery.trim() })
        const searchItems: MemoryFileItem[] = resp.items.map((r) => ({
          path: r.path,
          name: r.name,
          description: '',
          bucket: (r.frontmatter.bucket as string) || 'wiki',
          agentId: (r.frontmatter.agent_id as string) || null,
          tags: (r.frontmatter.tags as string[]) || [],
          importance: (r.frontmatter.importance as number) || 0.5,
          createdAt: (r.frontmatter.created_at as string) || '',
          updatedAt: (r.frontmatter.updated_at as string) || '',
          source: r.source,
          bodyPreview: r.content.slice(0, 200),
        }))
        setItems(searchItems)
      } else {
        const resp = await fetchMemoryFiles({
          bucket: filterBucket === 'all' ? undefined : filterBucket,
          agentId: filterAgent || undefined,
        })
        setItems(resp.items)
      }
    } catch (err) {
      console.error('[MemoryPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [filterBucket, filterAgent, searchMode, searchQuery])

  useEffect(() => {
    void load()
  }, [load])

  useGuideSideEffectRefresh('memory', () => {
    void load()
  })

  useEffect(() => {
    void fetchProactiveTopics()
      .then((r) => setProactiveTopics(r.topics))
      .catch(() => {})
  }, [])

  const openFile = async (path: string) => {
    try {
      const detail = await readMemoryFile(path)
      setSelectedPath(path)
      setFileDetail({
        path: detail.path,
        name: detail.name,
        body: detail.body,
        description: detail.description,
        tags: detail.tags,
        importance: detail.importance,
        bucket: detail.bucket,
      })
      setEditing(false)
    } catch (err) {
      console.error('[MemoryPanel] read file failed', err)
    }
  }

  const startEdit = () => {
    if (!fileDetail) return
    setEditName(fileDetail.name)
    setEditBody(fileDetail.body)
    setEditDescription(fileDetail.description)
    setEditTags(fileDetail.tags.join(', '))
    setEditImportance(String(fileDetail.importance))
    setEditBucket(fileDetail.bucket)
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
  }

  const handleSave = async () => {
    if (!fileDetail || saving) return
    setSaving(true)
    try {
      await writeMemoryFile(fileDetail.path, {
        name: editName,
        body: editBody,
        description: editDescription,
        tags: editTags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        importance: parseFloat(editImportance) || 0.5,
        bucket: editBucket,
      })
      setEditing(false)
      await openFile(fileDetail.path)
      await load()
    } catch (err) {
      console.error('[MemoryPanel] save failed', err)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (path: string) => {
    try {
      await deleteMemoryFile(path)
      setDeleteConfirmPath(null)
      if (selectedPath === path) {
        setSelectedPath(null)
        setFileDetail(null)
      }
      await load()
    } catch (err) {
      console.error('[MemoryPanel] delete failed', err)
    }
  }

  const handleDream = async () => {
    setDreaming(true)
    try {
      await triggerAutoDream()
      await load()
    } catch (err) {
      console.error('[MemoryPanel] auto-dream failed', err)
    } finally {
      setDreaming(false)
    }
  }

  // ─── Detail dialog (overlay, list stays visible) ───
  const detailOpen = !!fileDetail && !!selectedPath
  const displayBucket = fileDetail?.path?.includes('daily') ? 'daily' : fileDetail?.bucket ?? 'wiki'
  const displayBucketCfg = getBucketConfig(displayBucket)

  // ─── List view (always visible) ───
  return (
    <div className="flex flex-col gap-4">
      {/* Proactive topics from daily/interests.yaml */}
      {proactiveTopics.length > 0 && (
        <div className="cognition-fade-up flex flex-col gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3">
          <div className="flex items-center gap-2">
            <Sparkles className="size-3.5 text-primary" />
            <span className="text-xs font-semibold text-primary">兴趣话题</span>
            <span className="text-[10px] text-muted-foreground">
              来自今日 interests.yaml
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {proactiveTopics.map((t, i) => {
              const cfg = getBucketConfig(t.bucket || 'wiki')
              return (
                <span
                  key={`${t.title}-${i}`}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]',
                    cfg.badge,
                    cfg.border,
                  )}
                  title={t.reason || cfg.label}
                >
                  <span className={cn('size-1.5 shrink-0 rounded-full', cfg.dot)} />
                  {t.title || '未命名话题'}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="cognition-fade-up flex flex-wrap items-center gap-2 rounded-lg border bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          <Filter className="size-3" />
          筛选
        </div>
        <select
          value={filterBucket}
          onChange={(e) => {
            setFilterBucket(e.target.value as BucketFilter)
            setSearchMode(false)
          }}
          className="h-8 w-28 rounded-md border border-input bg-background px-2 text-xs outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
        >
          {BUCKETS.map((b) => (
            <option key={b} value={b}>
              {b === 'all' ? '全部分类' : (BUCKET_CONFIG[b]?.label ?? b)}
            </option>
          ))}
        </select>
        <div className="relative">
          <Input
            placeholder="Agent ID"
            value={filterAgent}
            onChange={(e) => setFilterAgent(e.target.value)}
            className="h-8 w-28 text-xs"
          />
        </div>
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="搜索记忆内容..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchQuery.trim()) {
                setSearchMode(true)
                void load()
              }
            }}
            className="h-8 pl-7 text-xs"
          />
        </div>
        {searchMode && (
          <Button
            size="sm"
            variant="ghost"
            className="h-8 gap-1 text-xs"
            onClick={() => {
              setSearchMode(false)
              setSearchQuery('')
              void load()
            }}
          >
            <X className="size-3" />
            清除搜索
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void load()}
          disabled={loading}
          className="h-8 gap-1 text-xs"
        >
          {loading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          刷新
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void handleDream()}
          disabled={dreaming}
          className="h-8 gap-1 text-xs"
        >
          {dreaming ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
          手动精炼
        </Button>
      </div>

      {/* File list - Hybrid layout */}
      <MemoryHybridGrid items={items} openFile={openFile} getBucketConfig={getBucketConfig} />

      {/* Empty state */}
      {items.length === 0 && !loading && (
        <div className="relative flex flex-col items-center gap-3 py-16 text-center">
          <div className="cognition-ambient pointer-events-none absolute size-40 rounded-full bg-primary/8 blur-3xl" />
          <div className="relative">
            <div className="flex size-14 items-center justify-center rounded-2xl border border-border/50 bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
              <Brain className="size-6 text-muted-foreground/70 cognition-empty-float" />
            </div>
          </div>
          <div className="cognition-fade-up relative space-y-0.5">
            <p className="text-sm font-semibold text-foreground">暂无记忆文件</p>
            <p className="text-xs text-muted-foreground">
              Agent 在对话中积累的知识会自动提取为文件并出现在这里
            </p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Detail dialog overlay */}
      <Dialog open={detailOpen} onOpenChange={(open) => { if (!open) { setSelectedPath(null); setFileDetail(null); setEditing(false) } }}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto p-0 sm:max-w-3xl">
          {fileDetail && (
            <MemoryDetailView
              fileDetail={{ ...fileDetail, bucket: displayBucket }}
              bucketCfg={displayBucketCfg}
              onStartEdit={startEdit}
              onDeleteConfirm={(path) => setDeleteConfirmPath(path)}
              deleteConfirmPath={deleteConfirmPath}
              onHandleDelete={handleDelete}
              onCancelEdit={cancelEdit}
              onSave={handleSave}
              editing={editing}
              editName={editName}
              setEditName={setEditName}
              editBody={editBody}
              setEditBody={setEditBody}
              editDescription={editDescription}
              setEditDescription={setEditDescription}
              editTags={editTags}
              setEditTags={setEditTags}
              editImportance={editImportance}
              setEditImportance={setEditImportance}
              editBucket={editBucket}
              setEditBucket={setEditBucket}
              saving={saving}
              setSelectedPath={setSelectedPath}
              setFileDetail={setFileDetail}
              load={load}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ─── Hybrid Grid: Featured cards (>0.9) + Bento grid for rest ──────────────

const FEATURED_THRESHOLD = 0.9

interface MemoryHybridGridProps {
  items: MemoryFileItem[]
  openFile: (path: string) => Promise<void>
  getBucketConfig: (bucket: string) => BucketCfg
}

function MemoryHybridGrid({ items, openFile, getBucketConfig }: MemoryHybridGridProps) {
  const featured = items.filter((item) => item.importance >= FEATURED_THRESHOLD)
  const regular = items.filter((item) => item.importance < FEATURED_THRESHOLD)

  return (
    <div className="flex flex-col gap-5">
      {/* Featured high-importance memories as hero cards */}
      {featured.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <Star className="size-3.5 text-amber-500" />
            精选记忆
            <span className="ml-0.5 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-mono tabular-nums text-[10px] text-amber-600 dark:text-amber-400">
              {featured.length}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 [grid-auto-rows:minmax(140px,auto)]">
            {featured.map((item, index) => (
              <FeaturedCard
                key={item.path}
                item={item}
                bucketCfg={getBucketConfig(item.bucket)}
                onClick={() => void openFile(item.path)}
                index={index}
              />
            ))}
          </div>
        </div>
      )}

      {/* Regular memories in bento grid */}
      {regular.length > 0 && (
        <div className="flex flex-col gap-3">
          {featured.length > 0 && (
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <FileText className="size-3.5" />
              全部记忆
              <span className="ml-0.5 rounded-full bg-muted px-1.5 py-0.5 font-mono tabular-nums text-[10px]">
                {regular.length}
              </span>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 [grid-auto-rows:minmax(140px,auto)]">
            {regular.map((item, index) => (
              <CompactCard
                key={item.path}
                item={item}
                bucketCfg={getBucketConfig(item.bucket)}
                onClick={() => void openFile(item.path)}
                index={index + featured.length}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Featured grid card for importance >= 0.9 */
function FeaturedCard({
  item,
  bucketCfg,
  onClick,
  index,
}: {
  item: MemoryFileItem
  bucketCfg: BucketCfg
  onClick: () => void
  index: number
}) {
  const preview = cardPreview(item)
  const title = cardTitle(item)

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'cognition-fade-up group relative flex h-full flex-col overflow-hidden rounded-lg border border-amber-500/25 bg-gradient-to-br from-amber-500/[0.06] to-card p-3.5 text-left shadow-[var(--shadow-sm)] transition-all duration-200 ease-out',
        'hover:-translate-y-1 hover:scale-[1.03] hover:border-amber-500/45 hover:shadow-[var(--shadow-md)] hover:z-10',
      )}
      style={{ animationDelay: `${index * 45}ms` }}
    >
      {/* Top: name + star badge */}
      <div className="flex items-start justify-between gap-2">
        <h4
          className="min-w-0 flex-1 truncate text-[13px] font-semibold leading-5 text-foreground"
          title={title}
        >
          {title}
        </h4>
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-amber-500/10 px-1.5 py-px text-[10px] font-medium text-amber-600 dark:text-amber-400">
          <Star className="size-2.5 fill-amber-500 text-amber-500" />
          {item.importance.toFixed(2)}
        </span>
      </div>

      {/* Category badge */}
      <div className="mt-2">
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium',
            bucketCfg.badge,
          )}
        >
          <span className={cn('size-1.5 rounded-full', bucketCfg.dot)} />
          {bucketCfg.label}
        </span>
      </div>

      {/* Preview body — fills the card middle */}
      {preview ? (
        <p className="mt-2.5 line-clamp-3 flex-1 text-[11px] leading-relaxed text-muted-foreground">
          {preview}
        </p>
      ) : (
        <div className="min-h-0 flex-1" />
      )}

      {/* Bottom: tags + date */}
      <div className="mt-3 flex items-end justify-between gap-2">
        <div className="min-w-0 flex-1 overflow-hidden">
          {item.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="mr-1 inline-block rounded-md border border-primary/12 bg-primary/6 px-1.5 py-px text-[10px] leading-tight text-foreground/75"
            >
              {tag}
            </span>
          ))}
          {item.tags.length > 3 && (
            <span className="text-[9px] text-muted-foreground">
              +{item.tags.length - 3}
            </span>
          )}
        </div>
        {item.createdAt && (
          <span className="shrink-0 tabular-nums text-[9px] leading-tight text-muted-foreground/40">
            {item.createdAt.slice(5)}
          </span>
        )}
      </div>
    </button>
  )
}

/** Compact card for the regular grid */
function CompactCard({
  item,
  bucketCfg,
  onClick,
  index,
}: {
  item: MemoryFileItem
  bucketCfg: BucketCfg
  onClick: () => void
  index: number
}) {
  const preview = cardPreview(item)
  const title = cardTitle(item)

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'cognition-fade-up group relative flex h-full flex-col overflow-hidden rounded-lg border bg-card p-3 text-left shadow-[var(--shadow-sm)] transition-all duration-200 ease-out',
        bucketCfg.border,
        bucketCfg.borderHover,
        'hover:-translate-y-1 hover:scale-[1.03] hover:shadow-[var(--shadow-md)] hover:z-10',
      )}
      style={{ animationDelay: `${Math.min(index * 35, 400)}ms` }}
    >
      <span
        className={cn('absolute inset-y-0 left-0 w-0.5 opacity-70', bucketCfg.bar)}
        aria-hidden
      />

      {/* Top: name + category badge */}
      <div className="flex items-start justify-between gap-2 pl-1">
        <h4
          className="min-w-0 flex-1 truncate text-[13px] font-medium leading-5 text-foreground"
          title={title}
        >
          {title}
        </h4>
        <span
          className={cn(
            'inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium',
            bucketCfg.badge,
          )}
        >
          <span className={cn('size-1.5 rounded-full', bucketCfg.dot)} />
          {bucketCfg.label}
        </span>
      </div>

      {/* Importance bar */}
      <div className="mt-2 flex items-center gap-2 pl-1">
        <span className="relative inline-block h-1.5 w-10 shrink-0 overflow-hidden rounded-full bg-muted">
          <span
            className={cn(
              'absolute inset-y-0 left-0 rounded-full',
              item.importance >= 0.7
                ? 'bg-emerald-500/80'
                : item.importance >= 0.4
                  ? 'bg-primary/60'
                  : 'bg-zinc-400/60',
            )}
            style={{ width: `${Math.round(item.importance * 100)}%` }}
          />
        </span>
        <span className="font-mono tabular-nums text-[9px] text-muted-foreground/70">
          {item.importance.toFixed(2)}
        </span>
      </div>

      {/* Preview — description or bodyPreview */}
      {preview ? (
        <p className="mt-2 line-clamp-2 flex-1 pl-1 text-[11px] leading-snug text-muted-foreground/80">
          {preview}
        </p>
      ) : (
        <div className="min-h-0 flex-1" />
      )}

      {/* Bottom: tags + date */}
      <div className="mt-auto flex items-end justify-between gap-2 pt-2 pl-1">
        <div className="min-w-0 flex-1 overflow-hidden">
          {item.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="mr-1 inline-block rounded bg-muted/60 px-1 py-px text-[9px] leading-tight text-muted-foreground"
            >
              {tag}
            </span>
          ))}
          {item.tags.length > 3 && (
            <span className="text-[9px] text-muted-foreground/50">
              +{item.tags.length - 3}
            </span>
          )}
        </div>
        {item.createdAt && (
          <span className="shrink-0 tabular-nums text-[9px] leading-tight text-muted-foreground/40">
            {item.createdAt.slice(5)}
          </span>
        )}
      </div>
    </button>
  )
}

// ─── Memory Detail View (structured rendering) ──────────────────────────────

interface MemoryDetailViewProps {
  fileDetail: {
    path: string
    name: string
    body: string
    description: string
    tags: string[]
    importance: number
    bucket: string
  }
  bucketCfg: { label: string; dot: string; badge: string }
  onStartEdit: () => void
  onDeleteConfirm: (path: string) => void
  deleteConfirmPath: string | null
  onHandleDelete: (path: string) => Promise<void>
  onCancelEdit: () => void
  onSave: () => Promise<void>
  editing: boolean
  editName: string
  setEditName: (v: string) => void
  editBody: string
  setEditBody: (v: string) => void
  editDescription: string
  setEditDescription: (v: string) => void
  editTags: string
  setEditTags: (v: string) => void
  editImportance: string
  setEditImportance: (v: string) => void
  editBucket: string
  setEditBucket: (v: string) => void
  saving: boolean
  setSelectedPath: (p: string | null) => void
  setFileDetail: (d: { path: string; name: string; body: string; description: string; tags: string[]; importance: number; bucket: string } | null) => void
  load: () => Promise<void>
}

function MemoryDetailView({
  fileDetail,
  bucketCfg,
  onStartEdit,
  onDeleteConfirm,
  deleteConfirmPath,
  onHandleDelete,
  onCancelEdit,
  onSave,
  editing,
  editName,
  setEditName,
  editBody,
  setEditBody,
  editDescription,
  setEditDescription,
  editTags,
  setEditTags,
  editImportance,
  setEditImportance,
  editBucket,
  setEditBucket,
  saving,
  setSelectedPath,
  setFileDetail,
  load,
}: MemoryDetailViewProps) {
  // Extract source line from body for display
  const sourceLine = useMemo(() => {
    const match = fileDetail.body.match(/(\*Source:.*|- \*Source:.*)/m)
    return match ? match[1].trim() : ''
  }, [fileDetail.body])

  // Clean body: strip the source line for rendering
  const cleanBody = useMemo(() => {
    return fileDetail.body.replace(/\n?\*Source:.*$/m, '').replace(/\n?- \*Source:.*$/m, '').trim()
  }, [fileDetail.body])

  const displayTitle = useMemo(() => {
    const name = fileDetail.name?.trim() || ''
    if (name && !isMachineName(name)) return shortTitle(name, 28)
    const fromBody = firstFactFromPreview(cleanBody)
    if (fromBody) return shortTitle(fromBody, 28)
    return shortTitle(name || '未命名记忆', 28)
  }, [fileDetail.name, cleanBody])

  const displayDescription = useMemo(() => {
    const desc = fileDetail.description?.trim() || ''
    if (desc && !isPlaceholderDescription(desc)) return desc
    return firstFactFromPreview(cleanBody)
  }, [fileDetail.description, cleanBody])

  if (editing) {
    return (
      <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" className="h-8 gap-1 text-xs" onClick={onCancelEdit}>
            <X className="size-3" /> 返回列表
          </Button>
        </div>
        <div className="flex flex-col gap-3 rounded-lg border bg-card/50 p-4 shadow-[var(--shadow-sm)]">
          <div className="flex flex-wrap items-center gap-2">
            <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="h-8 flex-1 text-sm" placeholder="文件名称" />
            <select value={editBucket} onChange={(e) => setEditBucket(e.target.value)} className="h-8 w-28 rounded-md border border-input bg-background px-2 text-xs outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10">
              <option value="procedure">经验 (procedure)</option>
              <option value="wiki">知识 (wiki)</option>
            </select>
            <Input type="number" step="0.1" min="0" max="1" value={editImportance} onChange={(e) => setEditImportance(e.target.value)} className="h-8 w-20 text-xs" placeholder="重要性" />
          </div>
          <Input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} className="h-8 text-xs" placeholder="简短描述" />
          <Input value={editTags} onChange={(e) => setEditTags(e.target.value)} className="h-8 text-xs" placeholder="标签（逗号分隔）" />
          <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} className="min-h-[200px] w-full rounded border bg-background px-3 py-2 text-sm leading-6 outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10" placeholder="Markdown 内容" />
          <div className="flex items-center justify-end gap-1">
            <Button size="sm" variant="ghost" className="h-8 px-3 text-xs" onClick={onCancelEdit}>取消</Button>
            <Button size="sm" className="h-8 gap-1 px-3 text-xs" onClick={() => void onSave()} disabled={saving}>
              {saving ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />} 保存
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-5 shadow-[var(--shadow-sm)]">
      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <FileText className="size-5 text-primary/70" />
          <h3 className="text-base font-semibold text-foreground">{displayTitle}</h3>
          <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium', bucketCfg.badge)}>
            <span className={cn('size-1.5 rounded-full', bucketCfg.dot)} />
            {bucketCfg.label}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {!editing && (
            <>
              <Button size="sm" variant="ghost" className="h-7 gap-1 text-[11px]" onClick={onStartEdit}>
                <Pencil className="size-3" /> 编辑
              </Button>
              {deleteConfirmPath === fileDetail.path ? (
                <div className="flex items-center gap-1">
                  <Button size="sm" variant="destructive" className="h-7 px-2 text-[11px]" onClick={() => void onHandleDelete(fileDetail.path)}>确认删除</Button>
                  <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => onDeleteConfirm(null)}>取消</Button>
                </div>
              ) : (
                <Button size="sm" variant="ghost" className="h-7 gap-1 text-[11px] text-destructive hover:text-destructive" onClick={() => onDeleteConfirm(fileDetail.path)}>
                  <Trash2 className="size-3" /> 删除
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Meta info strip */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <Info className="size-3" /> {displayDescription || '无描述'}
        </span>
        <span className="flex items-center gap-1">
          重要性
          <span className="relative inline-block h-1.5 w-12 overflow-hidden rounded-full bg-muted">
            <span className="absolute inset-y-0 left-0 rounded-full bg-primary/70" style={{ width: `${Math.round(fileDetail.importance * 100)}%` }} />
          </span>
          <span className="font-mono tabular-nums font-medium text-foreground/80">{fileDetail.importance.toFixed(2)}</span>
        </span>
        {sourceLine && (
          <span className="flex items-center gap-0.5 font-mono opacity-60">
            <CalendarDays className="size-3" /> {sourceLine.replace('*', '').replace('Source:', '').trim()}
          </span>
        )}
      </div>

      {/* Tags */}
      {fileDetail.tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Hash className="size-3 text-muted-foreground" />
          {fileDetail.tags.map((tag) => (
            <span key={tag} className="rounded-md bg-primary/6 border border-primary/12 px-2 py-0.5 text-[11px] text-foreground/80 hover:bg-primary/10 transition-colors">
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* File path */}
      <div className="flex items-center gap-0.5 text-[10px] text-muted-foreground/50 font-mono">
        <Folder className="size-2.5" /> {fileDetail.path}
      </div>

      {/* Markdown body — rendered as readable content */}
      <div className="mt-2 rounded-lg border border-border/50 bg-background p-4 shadow-[var(--shadow-xs)]">
        <div className="prose prose-sm prose-slate max-w-none dark:prose-invert
          prose-headings:text-foreground prose-p:text-foreground/85 prose-li:text-foreground/85
          prose-strong:text-foreground prose-code:text-foreground/80
          prose-h3:text-sm prose-h3:font-semibold prose-h3:mt-4 prose-h3:mb-2
          prose-ul:my-2 prose-li:my-0.5 prose-li:marker:text-muted-foreground">
          {cleanBody.split('\n').map((line, i) => {
            // Render markdown-like lines with basic formatting
            if (line.startsWith('## ')) {
              return <h3 key={i} className="text-sm font-semibold mt-4 mb-2 text-foreground">{line.replace('## ', '')}</h3>
            }
            if (line.startsWith('### ')) {
              return <h4 key={i} className="text-[13px] font-semibold mt-3 mb-1.5 text-foreground/90">{line.replace('### ', '')}</h4>
            }
            if (line.startsWith('---')) {
              return <hr key={i} className="my-3 border-border/50" />
            }
            if (line.trim().startsWith('- ')) {
              return (
                <li key={i} className="ml-4 list-disc text-[13px] leading-relaxed text-foreground/85">
                  {line.trim().slice(2)}
                </li>
              )
            }
            if (line.trim().startsWith('- ')) {
              return null // handled above
            }
            if (line.trim() === '') {
              return <div key={i} className="h-2" />
            }
            return <p key={i} className="text-[13px] leading-relaxed text-foreground/85 my-1">{line}</p>
          })}
        </div>
      </div>
    </div>
  )
}
