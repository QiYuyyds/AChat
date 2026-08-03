'use client'

import { Brain, Folder, Loader2, Pencil, Trash2, X } from 'lucide-react'
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

const CATEGORY_STYLES: Record<string, string> = {
  '': 'bg-muted text-muted-foreground',
  fact: 'bg-primary/10 text-primary',
  preference: 'bg-warning/10 text-warning',
  policy: 'bg-success/10 text-success',
  tool_failure: 'bg-destructive/10 text-destructive',
  identity: 'bg-secondary text-secondary-foreground',
  case: 'bg-secondary/60 text-secondary-foreground',
}

const CATEGORY_LABELS: Record<string, string> = {
  '': '通用',
  fact: '事实',
  preference: '偏好',
  policy: '策略',
  tool_failure: '工具失败',
  identity: '身份',
  case: '任务经验',
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
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
        <Input
          placeholder="Agent ID"
          value={filterAgent}
          onChange={(e) => {
            setFilterAgent(e.target.value)
            setPage(1)
          }}
          className="h-8 w-32 text-xs"
        />
        <select
          value={filterCategory || 'all'}
          onChange={(e) => {
            setFilterCategory(e.target.value === 'all' ? '' : e.target.value)
            setPage(1)
          }}
          className="h-8 w-36 rounded-md border border-input bg-background px-2 text-xs"
        >
          <option value="all">全部分类</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c]}
            </option>
          ))}
        </select>
        <Input
          placeholder="标签搜索"
          value={searchTag}
          onChange={(e) => {
            setSearchTag(e.target.value)
            setPage(1)
          }}
          className="h-8 w-32 text-xs"
        />
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading} className="ml-auto">
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : null}
          刷新
        </Button>
      </div>

      {/* Card list */}
      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            className="group rounded-lg border bg-card p-3 shadow-[var(--shadow-sm)] transition-all duration-150 hover:border-primary/30 hover:shadow-[var(--shadow-md)]"
          >
            {editingId === item.id ? (
              <div className="flex flex-col gap-2.5">
                <Input
                  value={editSummary}
                  onChange={(e) => setEditSummary(e.target.value)}
                  className="h-7 text-xs"
                  placeholder="摘要标题"
                />
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="w-full rounded border bg-background px-2 py-1.5 text-xs leading-5"
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
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  {item.summary && (
                    <p className="mb-1 text-sm font-medium leading-5 text-foreground">{item.summary}</p>
                  )}
                  <p className="text-sm leading-5 text-foreground">{item.content}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span
                      className={cn(
                        'rounded px-1.5 py-0.5 text-[10px] font-medium',
                        CATEGORY_STYLES[item.category] ?? CATEGORY_STYLES[''],
                      )}
                    >
                      {CATEGORY_LABELS[item.category] ?? item.category}
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                      <span>重要性</span>
                      <span className="inline-block h-1 w-10 overflow-hidden rounded-full bg-muted">
                        <span
                          className="block h-full rounded-full bg-primary"
                          style={{ width: `${Math.round(item.importance * 100)}%` }}
                        />
                      </span>
                      <span className="font-mono">{item.importance.toFixed(2)}</span>
                    </span>
                    {item.keywords.map((kw) => (
                      <span
                        key={kw}
                        className="rounded bg-primary/5 px-1.5 py-0.5 text-[10px] text-primary/70"
                      >
                        #{kw}
                      </span>
                    ))}
                    {item.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="mt-1.5 flex items-center gap-2 text-[10px] text-muted-foreground/70">
                    {item.agentId && (
                      <>
                        <span className="font-mono">{item.agentId}</span>
                        <span>·</span>
                      </>
                    )}
                    <span>{new Date(item.createdAt * 1000).toLocaleDateString()}</span>
                    {item.contentScope && (
                      <>
                        <span>·</span>
                        <span className="flex items-center gap-0.5 font-mono">
                          <Folder className="size-2.5" />
                          {item.contentScope}
                        </span>
                      </>
                    )}
                  </div>
                </div>
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
        ))}
      </div>

      {/* Empty state */}
      {items.length === 0 && !loading && (
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-muted/60 shadow-[var(--shadow-sm)]">
            <Brain className="size-5 text-muted-foreground" />
          </div>
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-foreground">暂无长期记忆</p>
            <p className="text-xs text-muted-foreground">Agent 在对话中积累的知识会出现在这里</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            共 {total} 条，第 {page}/{totalPages} 页
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
