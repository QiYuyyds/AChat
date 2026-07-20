'use client'

import { Loader2, Pencil, Trash2, X } from 'lucide-react'
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

const PAGE_SIZE = 10

const CATEGORIES = ['general', 'fact', 'preference', 'skill', 'project'] as const

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
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditContent('')
    setEditImportance('')
    setEditCategory('')
    setEditTags('')
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
    <div className="flex flex-col gap-3">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
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
          className="h-8 w-32 rounded-md border border-input bg-background px-2 text-xs"
        >
          <option value="all">All Categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <Input
          placeholder="Tag search"
          value={searchTag}
          onChange={(e) => {
            setSearchTag(e.target.value)
            setPage(1)
          }}
          className="h-8 w-32 text-xs"
        />
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : null}
          刷新
        </Button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-1.5 pr-2 font-medium">Content</th>
              <th className="pb-1.5 pr-2 font-medium">Category</th>
              <th className="pb-1.5 pr-2 font-medium">Imp.</th>
              <th className="pb-1.5 pr-2 font-medium">Tags</th>
              <th className="pb-1.5 pr-2 font-medium">Agent</th>
              <th className="pb-1.5 pr-2 font-medium">Created</th>
              <th className="pb-1.5 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-b last:border-0">
                {editingId === item.id ? (
                  <>
                    <td className="py-1.5 pr-2">
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="w-full rounded border bg-background px-1.5 py-1 text-xs"
                        rows={2}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <Input
                        value={editCategory}
                        onChange={(e) => setEditCategory(e.target.value)}
                        className="h-7 w-20 text-xs"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <Input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={editImportance}
                        onChange={(e) => setEditImportance(e.target.value)}
                        className="h-7 w-14 text-xs"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <Input
                        value={editTags}
                        onChange={(e) => setEditTags(e.target.value)}
                        placeholder="comma, separated"
                        className="h-7 w-28 text-xs"
                      />
                    </td>
                    <td className="py-1.5 pr-2 text-muted-foreground">{item.agentId || '-'}</td>
                    <td className="py-1.5 pr-2 text-muted-foreground">
                      {new Date(item.createdAt * 1000).toLocaleDateString()}
                    </td>
                    <td className="py-1.5">
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-xs"
                          onClick={() => void handleSave()}
                          disabled={saving}
                        >
                          {saving ? <Loader2 className="size-3 animate-spin" /> : '保存'}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-xs"
                          onClick={cancelEdit}
                        >
                          <X className="size-3" />
                        </Button>
                      </div>
                    </td>
                  </>
                ) : (
                  <>
                    <td className="max-w-xs truncate py-1.5 pr-2" title={item.content}>
                      {item.content}
                    </td>
                    <td className="py-1.5 pr-2">{item.category}</td>
                    <td className="py-1.5 pr-2">{item.importance.toFixed(2)}</td>
                    <td className="py-1.5 pr-2">
                      <div className="flex flex-wrap gap-0.5">
                        {item.tags.map((tag) => (
                          <span
                            key={tag}
                            className="rounded bg-muted px-1 py-0.5 text-[10px]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-1.5 pr-2 text-muted-foreground">
                      {item.agentId || '-'}
                    </td>
                    <td className="py-1.5 pr-2 text-muted-foreground">
                      {new Date(item.createdAt * 1000).toLocaleDateString()}
                    </td>
                    <td className="py-1.5">
                      {deleteConfirmId === item.id ? (
                        <div className="flex items-center gap-1">
                          <Button
                            size="sm"
                            variant="destructive"
                            className="h-6 px-2 text-xs"
                            onClick={() => void handleDelete(item.id)}
                          >
                            确认删除
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-xs"
                            onClick={() => setDeleteConfirmId(null)}
                          >
                            取消
                          </Button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 w-6 p-0"
                            onClick={() => startEdit(item)}
                            title="编辑"
                          >
                            <Pencil className="size-3" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 w-6 p-0 text-destructive"
                            onClick={() => setDeleteConfirmId(item.id)}
                            title="删除"
                          >
                            <Trash2 className="size-3" />
                          </Button>
                        </div>
                      )}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && !loading && (
          <div className="py-8 text-center text-xs text-muted-foreground">
            暂无长期记忆
          </div>
        )}
      </div>

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
