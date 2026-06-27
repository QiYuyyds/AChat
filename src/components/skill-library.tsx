'use client'

import { Loader2, Search, Sparkles, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { deleteSkill, listSkills, uploadSkill, type SkillSummary } from '@/lib/api'

export function SkillLibrary() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [deleteTargetSlug, setDeleteTargetSlug] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setSkills(await listSkills())
    } catch (err) {
      console.error('[SkillLibrary] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // One upload entry point: backend analyses whatever lands here (single
  // SKILL.md, several files, or a whole folder tree) and rebases to SKILL.md.
  const upload = useCallback(
    async (files: File[], paths: string[]) => {
      if (files.length === 0) return
      setUploading(true)
      setError(null)
      try {
        await uploadSkill(files, paths)
        await refresh()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setUploading(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [refresh],
  )

  const handlePicked = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const files = Array.from(fileList)
      void upload(files, files.map((f) => f.webkitRelativePath || f.name))
    },
    [upload],
  )

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const items = Array.from(e.dataTransfer.items)
        .map((it) => it.webkitGetAsEntry?.())
        .filter((x): x is FileSystemEntry => Boolean(x))
      if (items.length === 0) {
        handlePicked(e.dataTransfer.files)
        return
      }
      const collected: { file: File; path: string }[] = []
      for (const entry of items) await collectEntry(entry, '', collected)
      void upload(collected.map((c) => c.file), collected.map((c) => c.path))
    },
    [handlePicked, upload],
  )

  const handleDelete = async () => {
    if (!deleteTargetSlug) return
    setDeleting(true)
    try {
      await deleteSkill(deleteTargetSlug)
      setSkills((arr) => arr.filter((s) => s.slug !== deleteTargetSlug))
      setDeleteTargetSlug(null)
    } catch (err) {
      console.error('[SkillLibrary] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.slug.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    )
  }, [skills, query])

  const deleteTarget = deleteTargetSlug
    ? skills.find((s) => s.slug === deleteTargetSlug)
    : null

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header: single upload + search */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索技能"
              className="h-8 pl-7 text-xs"
            />
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 text-xs"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            <Upload className="size-3.5" />
            上传
          </Button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => handlePicked(e.target.files)}
        />
        {/* Drop zone — accepts a single file, multiple files, or a folder tree */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => void handleDrop(e)}
          className={`mt-2 rounded-md border border-dashed px-3 py-2 text-center text-[10px] transition ${
            dragOver ? 'border-primary bg-primary/5 text-primary' : 'text-muted-foreground'
          }`}
        >
          {uploading
            ? '上传中...'
            : loading
              ? '加载中...'
              : '拖入含 SKILL.md 的文件或文件夹，或点「上传技能」选择'}
        </div>
        {error && (
          <div className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
            {error}
          </div>
        )}
      </div>

      {/* Skill list */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1 p-2">
          {loading && skills.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
              <Loader2 className="mr-2 size-3 animate-spin" /> 加载中
            </div>
          ) : skills.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <Sparkles className="size-8 opacity-30" />
              <span className="text-xs">还没有技能</span>
              <span className="text-[10px]">上传含 SKILL.md 的文件或文件夹</span>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <Search className="size-7 opacity-30" />
              <span className="text-xs">没有匹配「{query}」的技能</span>
            </div>
          ) : (
            filtered.map((skill) => (
              <div
                key={skill.slug}
                className="group rounded-md border border-transparent px-2 py-2 transition hover:border-border/60 hover:bg-accent"
              >
                <div className="flex items-start gap-2">
                  <Sparkles className="mt-0.5 size-4 shrink-0 text-muted-foreground" />

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="min-w-0 truncate text-xs font-medium" title={skill.name}>
                        {skill.name}
                      </span>
                      <code className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                        {skill.slug}
                      </code>
                    </div>
                    {skill.description && (
                      <div className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">
                        {skill.description}
                      </div>
                    )}
                  </div>

                  {/* Delete button */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      setDeleteTargetSlug(skill.slug)
                    }}
                    title="删除技能"
                    className="shrink-0 self-center opacity-0 transition group-hover:opacity-100 hover:text-destructive"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </ScrollArea>

      {/* Delete confirmation */}
      {deleteTargetSlug && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setDeleteTargetSlug(null)}
        >
          <div
            className="mx-4 w-full max-w-sm rounded-lg border bg-card p-5 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold">删除技能</h3>
            <p className="mt-1.5 text-xs text-muted-foreground">
              确定要删除「{deleteTarget?.name}」吗？技能目录及其脚本会从磁盘移除。此操作不可恢复。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDeleteTargetSlug(null)}>
                取消
              </Button>
              <Button
                className="bg-destructive hover:bg-destructive/90"
                size="sm"
                onClick={() => void handleDelete()}
                disabled={deleting}
              >
                {deleting ? '删除中...' : '删除'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** Recursively collect files from a dropped FileSystemEntry, preserving relative paths. */
async function collectEntry(
  entry: FileSystemEntry,
  prefix: string,
  out: { file: File; path: string }[],
): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    )
    out.push({ file, path: prefix + entry.name })
    return
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  // readEntries returns in batches; loop until it drains.
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    )
    if (batch.length === 0) break
    for (const child of batch) await collectEntry(child, `${prefix}${entry.name}/`, out)
  }
}
