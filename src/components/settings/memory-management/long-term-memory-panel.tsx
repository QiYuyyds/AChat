'use client'

import { Brain, Filter, Folder, Loader2, Pencil, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  type LongTermMemoryItem,
  deleteLongTermMemory,
  fetchLongTermMemories,
  updateLongTermMemory,
} from '@/lib/api/memory'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 10

const CATEGORIES = ['fact', 'preference', 'policy', 'tool_failure', 'identity', 'case'] as const

type CategoryConfig = {
  label: string
  dot: string
  badge: string
  accent: string
}

const CATEGORY_CONFIG: Record<string, CategoryConfig> = {
  '': {
    label: '通用',
    dot: 'bg-zinc-400',
    badge: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-400',
    accent: 'before:bg-zinc-400',
  },
  fact: {
    label: '事实',
    dot: 'bg-blue-500',
    badge: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    accent: 'before:bg-blue-500',
  },
  preference: {
    label: '偏好',
    dot: 'bg-amber-500',
    badge: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    accent: 'before:bg-amber-500',
  },
  policy: {
    label: '策略',
    dot: 'bg-emerald-500',
    badge: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    accent: 'before:bg-emerald-500',
  },
  tool_failure: {
    label: '工具失败',
    dot: 'bg-rose-500',
    badge: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
    accent: 'before:bg-rose-500',
  },
  identity: {
    label: '身份',
    dot: 'bg-violet-500',
    badge: 'bg-violet-500/10 text-violet-600 dark:text-violet-400',
    accent: 'before:bg-violet-500',
  },
  case: {
    label: '任务经验',
    dot: 'bg-cyan-500',
    badge: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400',
    accent: 'before:bg-cyan-500',
  },
}

function getCategoryConfig(category: string): CategoryConfig {
  return CATEGORY_CONFIG[category] ?? CATEGORY_CONFIG['']
}

export function LongTermMemoryPanel() {
  const [items, setItems] = useState<LongTermMemoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [filterAgent, setFilterAgent] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [searchTag, setSearchTag] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editImportance, setEditImportance] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [editKeywords, setEditKeywords] = useState('')
  const [editContentScope, setEditContentScope] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetchLongTermMemories({
        agentId: filterAgent || undefined,
        category: filterCategory || undefined,
        tag: searchTag || undefined,
        page,
        size: PAGE_SIZE,
      })
      setItems(resp.items)
      setTotal(resp.total)
    } catch (err) {
      console.error('[LTMPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [filterAgent, filterCategory, searchTag, page])

  useEffect(() => {
    void load()
  }, [load])

  useGuideSideEffectRefresh('memory', () => { void load() })

  const startEdit = (item: LongTermMemoryItem) => {
    setEditingId(item.id)
    setEditContent(item.content)
    setEditImportance(String(item.importance))
    setEditCategory(item.category)
    setEditTags(item.tags.join(', '))
    setEditSummary(item.summary)
    setEditKeywords(item.keywords.join(', '))
    setEditContentScope(item.contentScope)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditContent('')
    setEditImportance('')
    setEditCategory('')
    setEditTags('')
    setEditSummary('')
    setEditKeywords('')
    setEditContentScope('')
  }

  const handleSave = async () => {
    if (editingId === null || saving) return
    setSaving(true)
    try {
      await updateLongTermMemory(editingId, {
        content: editContent,
        importance: parseFloat(editImportance) || undefined,
        category: editCategory,
        tags: editTags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        summary: editSummary,
        keywords: editKeywords
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        contentScope: editContentScope,
      })
      cancelEdit()
      await load()
    } catch (err) {
      console.error('[LTMPanel] save failed', err)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteLongTermMemory(id)
      setDeleteConfirmId(null)
      await load()
    } catch (err) {
      console.error('[LTMPanel] delete failed', err)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="flex flex-col gap-4">
      {/* Filter bar */}
      <div className="cognition-fade-up flex flex-wrap items-center gap-2 rounded-lg border bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          <Filter className="size-3" />
          筛选
        </div>
        <div className="relative">
          <Input
            placeholder="Agent ID"
            value={filterAgent}
            onChange={(e) => {
              setFilterAgent(e.target.value)
              setPage(1)
            }}
            className="h-8 w-28 text-xs"
          />
        </div>
        <select
          value={filterCategory || 'all'}
          onChange={(e) => {
            setFilterCategory(e.target.value === 'all' ? '' : e.target.value)
            setPage(1)
          }}
          className="h-8 w-32 rounded-md border border-input bg-background px-2 text-xs outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
        >
          <option value="all">全部分类</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {getCategoryConfig(c).label}
            </option>
          ))}
        </select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="标签搜索"
            value={searchTag}
            onChange={(e) => {
              setSearchTag(e.target.value)
              setPage(1)
            }}
            className="h-8 w-28 pl-7 text-xs"
          />
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void load()}
          disabled={loading}
          className="ml-auto h-8 gap-1 text-xs"
        >
          {loading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          刷新
        </Button>
      </div>

      {/* Card list */}
      <div className="flex flex-col gap-2">
        {items.map((item, index) => {
          const catConfig = getCategoryConfig(item.category)
          return (
            <div
              key={item.id}
              className={cn(
                'cognition-fade-up group relative overflow-hidden rounded-lg border bg-card p-3 shadow-[var(--shadow-sm)] transition-all duration-150 hover:border-primary/30 hover:shadow-[var(--shadow-md)]',
                'before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:opacity-60',
                catConfig.accent,
              )}
              style={{ animationDelay: `${index * 40}ms` }}
            >
              {editingId === item.id ? (
                <div className="flex flex-col gap-2.5 pl-1.5">
                  <Input
                    value={editSummary}
                    onChange={(e) => setEditSummary(e.target.value)}
                    className="h-7 text-xs"
                    placeholder="摘要标题"
                  />
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full rounded border bg-background px-2 py-1.5 text-xs leading-5 outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                    rows={3}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={editCategory}
                      onChange={(e) => setEditCategory(e.target.value)}
                      className="h-7 w-24 text-xs"
                      placeholder="分类"
                    />
                    <Input
                      type="number"
                      step="0.1"
                      min="0"
                      max="1"
                      value={editImportance}
                      onChange={(e) => setEditImportance(e.target.value)}
                      className="h-7 w-16 text-xs"
                      placeholder="重要性"
                    />
                    <Input
                      value={editTags}
                      onChange={(e) => setEditTags(e.target.value)}
                      placeholder="标签（逗号分隔）"
                      className="h-7 w-32 text-xs"
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={editKeywords}
                      onChange={(e) => setEditKeywords(e.target.value)}
                      placeholder="关键词（逗号分隔）"
                      className="h-7 w-32 text-xs"
                    />
                    <Input
                      value={editContentScope}
                      onChange={(e) => setEditContentScope(e.target.value)}
                      placeholder="内容范围路径"
                      className="h-7 w-40 text-xs"
                    />
                  </div>
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={cancelEdit}
                    >
                      取消
                    </Button>
                    <Button
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => void handleSave()}
                      disabled={saving}
                    >
                      {saving ? <Loader2 className="size-3 animate-spin" /> : '保存'}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-3 pl-1.5">
                  <div className="min-w-0 flex-1">
                    {item.summary && (
                      <p className="mb-1 text-sm font-medium leading-5 text-foreground">{item.summary}</p>
                    )}
                    <p className="text-sm leading-5 text-foreground/90">{item.content}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      {/* Category badge */}
                      <span
                        className={cn(
                          'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium',
                          catConfig.badge,
                        )}
                      >
                        <span className={cn('size-1.5 rounded-full', catConfig.dot)} />
                        {catConfig.label}
                      </span>
                      {/* Importance */}
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <span>重要性</span>
                        <span className="relative inline-block h-1 w-10 overflow-hidden rounded-full bg-muted">
                          <span
                            className="absolute inset-y-0 left-0 rounded-full bg-primary/70 transition-all duration-300"
                            style={{ width: `${Math.round(item.importance * 100)}%` }}
                          />
                        </span>
                        <span className="font-mono tabular-nums">{item.importance.toFixed(2)}</span>
                      </span>
                      {/* Keywords */}
                      {item.keywords.map((kw) => (
                        <span
                          key={kw}
                          className="rounded bg-primary/5 px-1.5 py-0.5 text-[10px] text-primary/70"
                        >
                          #{kw}
                        </span>
                      ))}
                      {/* Tags */}
                      {item.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                    {/* Metadata */}
                    <div className="mt-1.5 flex items-center gap-2 text-[10px] text-muted-foreground/70">
                      {item.agentId && (
                        <>
                          <span className="font-mono">{item.agentId}</span>
                          <span className="text-border">|</span>
                        </>
                      )}
                      <span className="tabular-nums">{new Date(item.createdAt * 1000).toLocaleDateString()}</span>
                      {item.contentScope && (
                        <>
                          <span className="text-border">|</span>
                          <span className="flex items-center gap-0.5 font-mono">
                            <Folder className="size-2.5" />
                            {item.contentScope}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  {/* Actions */}
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                    {deleteConfirmId === item.id ? (
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="destructive"
                          className="h-7 px-2 text-xs"
                          onClick={() => void handleDelete(item.id)}
                        >
                          确认删除
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-xs"
                          onClick={() => setDeleteConfirmId(null)}
                        >
                          取消
                        </Button>
                      </div>
                    ) : (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="size-7 p-0"
                          onClick={() => startEdit(item)}
                          title="编辑"
                          aria-label="编辑"
                        >
                          <Pencil className="size-3" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="size-7 p-0 text-destructive hover:text-destructive"
                          onClick={() => setDeleteConfirmId(item.id)}
                          title="删除"
                          aria-label="删除"
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

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
            <p className="text-sm font-semibold text-foreground">暂无长期记忆</p>
            <p className="text-xs text-muted-foreground">Agent 在对话中积累的知识会出现在这里</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-[11px] tabular-nums text-muted-foreground">
            共 {total} 条, 第 {page}/{totalPages} 页
          </span>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              下一页
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
