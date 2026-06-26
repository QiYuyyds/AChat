'use client'

import { BookOpen, FileText, Loader2, Plus, Search, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { DocumentDetail } from '@/components/document-detail'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { deleteDocument, fetchDocuments } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DocumentRow } from '@/shared/types'

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
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

const SOURCE_LABELS: Record<string, string> = {
  agent_generated: 'Agent',
  user_upload: '上传',
}

const TYPE_COLORS: Record<string, string> = {
  note: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  manual: 'bg-purple-500/10 text-purple-600 dark:text-purple-400',
  spec: 'bg-green-500/10 text-green-600 dark:text-green-400',
  reference: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  report: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
  other: 'bg-gray-500/10 text-gray-600 dark:text-gray-400',
}

export function KnowledgeLibrary() {
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const list = await fetchDocuments()
      setDocuments(list)
    } catch (err) {
      console.error('[KnowledgeLibrary] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return documents
    return documents.filter(
      (d) =>
        d.title.toLowerCase().includes(q) ||
        d.docType.toLowerCase().includes(q) ||
        d.createdBy.toLowerCase().includes(q),
    )
  }, [documents, search])

  const handleUploaded = useCallback(() => {
    void refresh()
  }, [refresh])

  const handleDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteDocument(deleteTargetId)
      setDocuments((arr) => arr.filter((d) => d.id !== deleteTargetId))
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[KnowledgeLibrary] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  const deleteTarget = deleteTargetId
    ? documents.find((d) => d.id === deleteTargetId)
    : null

  // Detail view
  if (selectedId) {
    return <DocumentDetail documentId={selectedId} onBack={() => setSelectedId(null)} />
  }

  // List view
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header + upload */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文档..."
              className="w-full rounded-md border bg-background py-1.5 pl-8 pr-7 text-xs outline-none transition focus:border-foreground/30"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={() => setUploadOpen(true)}
          >
            <Plus className="size-3.5" />
            上传
          </Button>
        </div>
        <div className="mt-1 text-[10px] text-muted-foreground">
          {loading ? '加载中...' : `共 ${filtered.length} 篇文档`}
        </div>
      </div>

      {/* Document list */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1 p-2">
          {loading && documents.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
              <Loader2 className="mr-2 size-3 animate-spin" /> 加载中
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <BookOpen className="size-8 opacity-30" />
              <span className="text-xs">
                {search.trim() ? '没有匹配的文档' : '知识库还是空的'}
              </span>
              {!search.trim() && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1.5 text-xs"
                  onClick={() => setUploadOpen(true)}
                >
                  <Plus className="size-3" />
                  上传第一篇文档
                </Button>
              )}
            </div>
          ) : (
            filtered.map((doc) => {
              const meta = doc.latestMetadata ?? {}
              const parser = (meta.parser as string | undefined) ?? doc.latestParser
              const pages = (meta.pages as number | undefined) ?? null
              const textChars = (meta.textChars as number | undefined) ?? doc.latestContentChars
              const filename = (meta.filename as string | undefined) ?? null

              return (
                <div
                  key={doc.id}
                  className="group cursor-pointer rounded-md border border-transparent px-2 py-2 transition hover:border-border/60 hover:bg-accent"
                  onClick={() => setSelectedId(doc.id)}
                >
                  <div className="flex items-start gap-2">
                    <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />

                    <div className="min-w-0 flex-1">
                      {/* Title + version */}
                      <div className="flex items-center gap-1.5">
                        <span className="min-w-0 truncate text-xs font-medium" title={doc.title}>
                          {doc.title}
                        </span>
                        <span className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                          v{doc.latestVersion}
                        </span>
                      </div>

                      {/* Metadata line */}
                      <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                        <span className={cn(
                          'rounded px-1 py-0.5 text-[10px]',
                          TYPE_COLORS[doc.docType] ?? TYPE_COLORS.other,
                        )}>
                          {doc.docType}
                        </span>
                        <span>·</span>
                        <span>{SOURCE_LABELS[doc.source] ?? doc.source}</span>
                        <span>·</span>
                        <span>{doc.createdBy}</span>
                        {parser && (
                          <>
                            <span>·</span>
                            <Badge variant="outline" className="text-[10px]">{parser}</Badge>
                          </>
                        )}
                        {pages != null && (
                          <>
                            <span>·</span>
                            <span>{pages}页</span>
                          </>
                        )}
                        {textChars != null && (
                          <>
                            <span>·</span>
                            <span>{textChars}字</span>
                          </>
                        )}
                        <span>·</span>
                        <span>{formatTime(doc.updatedAt)}</span>
                        {filename && (
                          <>
                            <span>·</span>
                            <span className="truncate">{filename}</span>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Delete button */}
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteTargetId(doc.id)
                      }}
                      title="删除文档"
                      className="shrink-0 self-center opacity-0 transition group-hover:opacity-100 hover:text-red-600"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </div>
              )
            })
          )}
        </div>
      </ScrollArea>

      {/* Upload dialog */}
      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={handleUploaded}
      />

      {/* Delete confirmation */}
      {deleteTargetId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setDeleteTargetId(null)}>
          <div
            className="mx-4 w-full max-w-sm rounded-lg border bg-card p-5 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold">删除文档</h3>
            <p className="mt-1.5 text-xs text-muted-foreground">
              确定要删除「{deleteTarget?.title}」吗？文档将标记为已删除，所有 RAG 分块也会被清理。此操作不可恢复。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDeleteTargetId(null)}>
                取消
              </Button>
              <Button
                className="bg-red-600 hover:bg-red-700"
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
