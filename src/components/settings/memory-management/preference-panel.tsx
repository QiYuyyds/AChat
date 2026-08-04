'use client'

import { Loader2, Pencil, Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  type PreferenceItem,
  deletePreference,
  fetchPreferences,
  updatePreference,
} from '@/lib/api/memory'
import { cn } from '@/lib/utils'

/** Stable color from key string hash, for visual variety across tiles */
function keyAccent(key: string): { dot: string; bar: string; glow: string } {
  let hash = 0
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  const palette = [
    { dot: 'bg-blue-500', bar: 'before:bg-blue-500', glow: 'group-hover:shadow-blue-500/10' },
    { dot: 'bg-violet-500', bar: 'before:bg-violet-500', glow: 'group-hover:shadow-violet-500/10' },
    { dot: 'bg-emerald-500', bar: 'before:bg-emerald-500', glow: 'group-hover:shadow-emerald-500/10' },
    { dot: 'bg-amber-500', bar: 'before:bg-amber-500', glow: 'group-hover:shadow-amber-500/10' },
    { dot: 'bg-rose-500', bar: 'before:bg-rose-500', glow: 'group-hover:shadow-rose-500/10' },
    { dot: 'bg-cyan-500', bar: 'before:bg-cyan-500', glow: 'group-hover:shadow-cyan-500/10' },
  ]
  return palette[Math.abs(hash) % palette.length]
}

/** Short values get pill treatment, long values get truncated */
function isShortValue(value: string): boolean {
  return value.length <= 24 && !value.includes('\n')
}

export function PreferencePanel() {
  const [items, setItems] = useState<PreferenceItem[]>([])
  const [loading, setLoading] = useState(false)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleteConfirmKey, setDeleteConfirmKey] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [createSaving, setCreateSaving] = useState(false)
  const [createError, setCreateError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetchPreferences()
      setItems(resp.items)
    } catch (err) {
      console.error('[PreferencePanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const startEdit = (item: PreferenceItem) => {
    setEditingKey(item.key)
    setEditValue(item.value)
  }

  const cancelEdit = () => {
    setEditingKey(null)
    setEditValue('')
  }

  const handleSave = async () => {
    if (editingKey === null || saving) return
    setSaving(true)
    try {
      await updatePreference(editingKey, editValue)
      cancelEdit()
      await load()
    } catch (err) {
      console.error('[PreferencePanel] save failed', err)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (key: string) => {
    try {
      await deletePreference(key)
      setDeleteConfirmKey(null)
      await load()
    } catch (err) {
      console.error('[PreferencePanel] delete failed', err)
    }
  }

  const startCreate = () => {
    setCreating(true)
    setNewKey('')
    setNewValue('')
    setCreateError('')
  }

  const cancelCreate = () => {
    setCreating(false)
    setNewKey('')
    setNewValue('')
    setCreateError('')
  }

  const handleCreate = async () => {
    const key = newKey.trim()
    if (!key || createSaving) return
    if (items.some((item) => item.key === key)) {
      setCreateError('该 Key 已存在')
      return
    }
    setCreateSaving(true)
    setCreateError('')
    try {
      await updatePreference(key, newValue)
      cancelCreate()
      await load()
    } catch (err) {
      console.error('[PreferencePanel] create failed', err)
      setCreateError('创建失败, 请重试')
    } finally {
      setCreateSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="cognition-fade-up flex items-center justify-between rounded-lg border bg-card/50 px-3 py-2.5 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-lg bg-primary/8">
            <Settings2 className="size-3.5 text-primary/70" />
          </div>
          <span className="text-xs font-medium text-foreground">
            {items.length > 0 ? (
              <><span className="tabular-nums">{items.length}</span> 条偏好</>
            ) : '偏好设置'}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {!creating && (
            <Button
              size="sm"
              variant="outline"
              onClick={startCreate}
              className="h-7 gap-1 px-2.5 text-xs shadow-[var(--shadow-sm),var(--inset-hi)] transition-all duration-200 hover:shadow-[var(--shadow-md),var(--inset-hi)] active:scale-[0.97]"
            >
              <Plus className="size-3" />
              新建
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void load()}
            disabled={loading}
            className="h-7 gap-1 px-2.5 text-xs"
          >
            {loading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          </Button>
        </div>
      </div>

      {/* Create form — dashed tile */}
      {creating && (
        <div className="cognition-fade-up rounded-xl border-2 border-dashed border-primary/30 bg-primary/[0.02] p-4">
          <div className="mb-3 flex items-center gap-1.5">
            <Plus className="size-3.5 text-primary" />
            <span className="text-xs font-semibold text-primary">新建偏好</span>
          </div>
          <div className="flex flex-col gap-2.5">
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Key</label>
              <Input
                placeholder="如 language, theme, editor_font"
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                className="h-8 font-mono text-xs"
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Value</label>
              <Input
                placeholder="偏好值"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
            {createError && (
              <span className="text-xs text-destructive">{createError}</span>
            )}
            <div className="flex items-center justify-end gap-1.5 pt-0.5">
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2.5 text-xs"
                onClick={cancelCreate}
              >
                <X className="size-3" />
                取消
              </Button>
              <Button
                size="sm"
                className="h-7 gap-1 px-2.5 text-xs"
                onClick={() => void handleCreate()}
                disabled={createSaving || !newKey.trim()}
              >
                {createSaving ? <Loader2 className="size-3 animate-spin" /> : null}
                创建
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Tile grid */}
      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
          {items.map((item, index) => {
            const accent = keyAccent(item.key)
            const isEditing = editingKey === item.key
            const isDeleteConfirm = deleteConfirmKey === item.key

            return (
              <div
                key={item.key}
                className={cn(
                  'cognition-fade-up group relative overflow-hidden rounded-xl border bg-card p-4 shadow-[var(--shadow-sm)] transition-all duration-200',
                  'before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:opacity-50 transition-shadow',
                  accent.bar,
                  accent.glow,
                  isEditing
                    ? 'border-primary/40 ring-1 ring-primary/15'
                    : isDeleteConfirm
                      ? 'border-destructive/40'
                      : 'hover:border-primary/25 hover:shadow-[var(--shadow-md)]',
                )}
                style={{ animationDelay: `${index * 35}ms` }}
              >
                {isEditing ? (
                  /* ─── Edit mode ─── */
                  <div className="flex flex-col gap-2 pl-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className={cn('size-1.5 rounded-full', accent.dot)} />
                      <span className="font-mono text-xs font-semibold text-foreground">{item.key}</span>
                    </div>
                    <textarea
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      className="w-full resize-none rounded-lg border bg-background px-2.5 py-1.5 text-xs leading-5 outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                      rows={2}
                      autoFocus
                    />
                    <div className="flex items-center justify-end gap-1.5">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 gap-1 px-2.5 text-xs"
                        onClick={cancelEdit}
                      >
                        取消
                      </Button>
                      <Button
                        size="sm"
                        className="h-7 gap-1 px-2.5 text-xs"
                        onClick={() => void handleSave()}
                        disabled={saving}
                      >
                        {saving ? <Loader2 className="size-3 animate-spin" /> : null}
                        保存
                      </Button>
                    </div>
                  </div>
                ) : isDeleteConfirm ? (
                  /* ─── Delete confirm ─── */
                  <div className="flex items-center gap-3 pl-1.5">
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-xs font-semibold text-foreground">{item.key}</div>
                      <p className="mt-0.5 text-[10px] text-destructive">确定要删除这条偏好吗?</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="sm"
                        variant="destructive"
                        className="h-7 px-2.5 text-xs"
                        onClick={() => void handleDelete(item.key)}
                      >
                        删除
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2.5 text-xs"
                        onClick={() => setDeleteConfirmKey(null)}
                      >
                        取消
                      </Button>
                    </div>
                  </div>
                ) : (
                  /* ─── Display mode ─── */
                  <div className="flex items-start gap-3 pl-1.5">
                    <div className={cn('mt-1 size-1.5 shrink-0 rounded-full', accent.dot)} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-1.5">
                        <span className="font-mono text-sm font-semibold tracking-tight text-foreground">
                          {item.key}
                        </span>
                      </div>
                      <div className="mt-1">
                        {isShortValue(item.value) ? (
                          <span className="inline-flex items-center rounded-md bg-muted/70 px-2 py-0.5 font-mono text-[11px] text-foreground/80">
                            {item.value || <span className="text-muted-foreground/50">(空)</span>}
                          </span>
                        ) : (
                          <p className="break-words text-xs leading-5 text-muted-foreground">
                            {item.value}
                          </p>
                        )}
                      </div>
                    </div>
                    {/* Hover actions */}
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="size-6 p-0"
                        onClick={() => startEdit(item)}
                        title="编辑"
                        aria-label="编辑"
                      >
                        <Pencil className="size-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="size-6 p-0 text-destructive hover:text-destructive"
                        onClick={() => setDeleteConfirmKey(item.key)}
                        title="删除"
                        aria-label="删除"
                      >
                        <Trash2 className="size-3" />
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Empty state */}
      {items.length === 0 && !loading && !creating && (
        <div className="relative flex flex-col items-center gap-3 py-16 text-center">
          <div className="cognition-ambient pointer-events-none absolute size-40 rounded-full bg-primary/8 blur-3xl" />
          <div className="relative">
            <div className="flex size-14 items-center justify-center rounded-2xl border border-border/50 bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
              <Settings2 className="size-6 text-muted-foreground/70 cognition-empty-float" />
            </div>
          </div>
          <div className="cognition-fade-up relative space-y-0.5">
            <p className="text-sm font-semibold text-foreground">暂无用户偏好</p>
            <p className="text-xs text-muted-foreground">点击「新建」添加跨会话持久的偏好设置</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  )
}
