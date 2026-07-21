'use client'

import { ArrowLeft, ChevronRight, FileText, Loader2, Trash2, Upload } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Markdown } from '@/components/markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { deleteDocument, getDocument, ingestDocument, listVersions } from '@/lib/api'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { cn } from '@/lib/utils'
import type { DocumentRow, VersionRow } from '@/shared/types'

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const SOURCE_LABELS: Record<string, string> = {
  agent_generated: 'Agent 生成',
  user_upload: '用户上传',
}

/** Pre-process contentMd for display. Converts PDF page markers into visual separators. */
function preprocessContent(contentMd: string, parser: string | undefined): string {
  if (!contentMd) return ''
  // PDF extracted text has "--- page N ---" markers; convert to markdown hr + page label
  if (parser && parser !== 'plain_text') {
    return contentMd.replace(
      /^---\s*page\s*(\d+)\s*---$/gim,
      '\n\n---\n\n**\u00a0📄 Page $1**\n\n',
    )
  }
  return contentMd
}

export function DocumentDetail({
  documentId,
  onBack,
}: {
  documentId: string
  onBack: () => void
}) {
  const [doc, setDoc] = useState<DocumentRow | null>(null)
  const [latestVer, setLatestVer] = useState<VersionRow | null>(null)
  const [versions, setVersions] = useState<VersionRow[]>([])
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [ingestingId, setIngestingId] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [detail, verList] = await Promise.all([
        getDocument(documentId),
        listVersions(documentId),
      ])
      setDoc(detail.document)
      setLatestVer(detail.version)
      // Sort by version descending (latest first)
      const sorted = verList.sort((a, b) => b.version - a.version)
      setVersions(sorted)
      // Default to latest version
      setSelectedVersionId(detail.version.id)
    } catch (err) {
      console.error('[DocumentDetail] load failed', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [documentId])

  const handleIngest = async (versionId: string) => {
    setIngestingId(versionId)
    try {
      await ingestDocument(documentId, versionId)
    } catch (err) {
      console.error('[DocumentDetail] ingest failed', err)
    } finally {
      setIngestingId(null)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteDocument(documentId)
      onBack()
    } catch (err) {
      console.error('[DocumentDetail] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  // The currently selected version object
  const currentVersion = useMemo(() => {
    if (!selectedVersionId) return latestVer
    return versions.find((v) => v.id === selectedVersionId) ?? latestVer
  }, [selectedVersionId, versions, latestVer])

  // Pre-processed content for rendering
  const renderedContent = useMemo(() => {
    if (!currentVersion) return ''
    const parser = (currentVersion.metadata?.parser as string | undefined) ?? undefined
    return preprocessContent(currentVersion.contentMd, parser)
  }, [currentVersion])

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center bg-background/85 backdrop-blur-2xl">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!doc || !latestVer) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 bg-background/85 backdrop-blur-2xl">
        <span className="text-xs text-muted-foreground">文档未找到</span>
        <Button variant="outline" size="sm" onClick={onBack}>
          返回列表
        </Button>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header */}
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="size-7" onClick={onBack} title="返回" aria-label="返回">
            <ArrowLeft className="size-4" />
          </Button>
          <div className="flex size-8 items-center justify-center rounded-lg bg-muted/60">
            <FileText className="size-4 text-muted-foreground" />
          </div>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{doc.title}</h2>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 hover:text-destructive"
            onClick={() => setDeleteOpen(true)}
            title="删除文档"
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>

        {/* Metadata badges */}
        <div className="mt-2 flex flex-wrap items-center gap-2 pl-[2.75rem] text-[10px] text-muted-foreground">
          <Badge variant="outline" className="text-[10px]">{doc.docType}</Badge>
          <Badge variant="secondary" className="text-[10px]">
            {SOURCE_LABELS[doc.source] ?? doc.source}
          </Badge>
          <span>·</span>
          <span>{doc.createdBy}</span>
          <span>·</span>
          <span>创建 {formatTime(doc.createdAt)}</span>
          <span>·</span>
          <span>更新 {formatTime(doc.updatedAt)}</span>
        </div>
      </div>

      {/* Body: left content + right version sidebar */}
      <div className="flex min-h-0 flex-1">
        {/* Content area */}
        <ScrollArea className="min-h-0 min-w-0 flex-1">
          <div className="mx-auto max-w-3xl p-6">
            {currentVersion ? (
              <>
                {/* Version selector badge */}
                {versions.length > 1 && (
                  <div className="mb-4 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span>正在查看</span>
                    <Badge variant="secondary" className="text-[10px]">
                      v{currentVersion.version}
                    </Badge>
                    {currentVersion.id === doc.latestVersionId && (
                      <Badge variant="outline" className="text-[10px] text-success">最新</Badge>
                    )}
                    {currentVersion.summary && (
                      <>
                        <span>·</span>
                        <span className="truncate">{currentVersion.summary}</span>
                      </>
                    )}
                  </div>
                )}

                {/* Rendered content */}
                <Markdown className="min-h-32">{renderedContent}</Markdown>
              </>
            ) : (
              <div className="flex items-center justify-center py-16 text-xs text-muted-foreground">
                无内容
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Version history sidebar */}
        <div className="flex w-56 shrink-0 flex-col border-l max-md:hidden">
          <div className="flex shrink-0 items-center justify-between px-3 py-2.5">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              版本历史 ({versions.length})
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-[10px]"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="size-3" />
              新版本
            </Button>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-1.5 p-2">
              {versions.map((ver) => {
                const isActive = ver.id === (selectedVersionId ?? latestVer.id)
                const parser = (ver.metadata?.parser as string | undefined) ?? undefined
                return (
                  <div
                    key={ver.id}
                    className={cn(
                      'cursor-pointer rounded-lg border px-2.5 py-2 transition-all duration-150',
                      isActive
                        ? 'border-primary/40 bg-primary/5 shadow-[var(--shadow-sm)]'
                        : 'border-border/40 hover:border-primary/20 hover:bg-accent/50',
                    )}
                    onClick={() => setSelectedVersionId(ver.id)}
                  >
                    <div className="flex items-center gap-1.5">
                      <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
                      <span className="font-mono text-[11px] font-medium">v{ver.version}</span>
                      {ver.id === doc.latestVersionId && (
                        <Badge variant="secondary" className="text-[9px]">最新</Badge>
                      )}
                    </div>
                    <div className="mt-0.5 pl-[1.125rem] text-[10px] text-muted-foreground">
                      {formatTime(ver.createdAt)}
                    </div>
                    {parser && (
                      <div className="pl-[1.125rem] text-[10px] text-muted-foreground">
                        {parser}
                      </div>
                    )}
                    {/* Inline ingest button */}
                    {ingestingId === null && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          void handleIngest(ver.id)
                        }}
                        className="ml-[1.125rem] mt-0.5 text-[10px] text-primary hover:underline"
                      >
                        入库 RAG
                      </button>
                    )}
                    {ingestingId === ver.id && (
                      <span className="ml-[1.125rem] mt-0.5 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                        <Loader2 className="size-2.5 animate-spin" /> 入库中
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        </div>
      </div>

      {/* Upload new version dialog */}
      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => void load()}
        documentId={documentId}
        defaultTitle={doc?.title}
        defaultDocType={doc?.docType}
      />

      {/* Delete confirmation */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>删除文档</DialogTitle>
            <DialogDescription>
              确定要删除「{doc.title}」吗？文档将标记为已删除，所有版本的 RAG 分块也会被清理。此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)} size="sm">
              取消
            </Button>
            <Button
              className="bg-destructive hover:bg-destructive/90"
              onClick={() => void handleDelete()}
              disabled={deleting}
              size="sm"
            >
              {deleting ? '删除中...' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
