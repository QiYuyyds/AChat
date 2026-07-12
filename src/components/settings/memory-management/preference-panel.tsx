'use client'

import { Loader2, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  type PreferenceItem,
  deletePreference,
  fetchPreferences,
  updatePreference,
} from '@/lib/api/memory'

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
      setCreateError('创建失败，请重试')
    } finally {
      setCreateSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          共 {items.length} 条偏好
        </span>
        <div className="flex items-center gap-2">
          {!creating && (
            <Button size="sm" variant="outline" onClick={startCreate}>
              <Plus className="size-3.5" />
              新建
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2 className="size-3.5 animate-spin" /> : null}
            刷新
          </Button>
        </div>
      </div>

      {/* Create form */}
      {creating && (
        <div className="flex flex-col gap-2 rounded-md border p-3">
          <div className="flex items-center gap-2">
            <Input
              placeholder="Key（如 language、theme）"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              className="h-8 flex-1 text-xs"
              autoFocus
            />
            <Input
              placeholder="Value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              className="h-8 flex-1 text-xs"
            />
          </div>
          {createError && (
            <span className="text-xs text-destructive">{createError}</span>
          )}
          <div className="flex items-center justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={cancelCreate}
            >
              取消
            </Button>
            <Button
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => void handleCreate()}
              disabled={createSaving || !newKey.trim()}
            >
              {createSaving ? <Loader2 className="size-3 animate-spin" /> : '创建'}
            </Button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-1.5 pr-2 font-medium">Key</th>
              <th className="pb-1.5 pr-2 font-medium">Value</th>
              <th className="pb-1.5 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.key} className="border-b last:border-0">
                <td className="py-1.5 pr-2 font-medium">{item.key}</td>
                {editingKey === item.key ? (
                  <>
                    <td className="py-1.5 pr-2">
                      <Input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="h-7 text-xs"
                      />
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
                    <td className="py-1.5 pr-2">{item.value}</td>
                    <td className="py-1.5">
                      {deleteConfirmKey === item.key ? (
                        <div className="flex items-center gap-1">
                          <Button
                            size="sm"
                            variant="destructive"
                            className="h-6 px-2 text-xs"
                            onClick={() => void handleDelete(item.key)}
                          >
                            确认删除
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-xs"
                            onClick={() => setDeleteConfirmKey(null)}
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
                            onClick={() => setDeleteConfirmKey(item.key)}
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
            暂无用户偏好
          </div>
        )}
      </div>
    </div>
  )
}
