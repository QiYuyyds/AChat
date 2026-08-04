'use client'

import { ArrowLeft, FileText, Loader2, Trash2, Upload } from 'lucide-react'
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

/** Pre-process contentMd for display.
 * - Converts PDF page markers into visual separators.
 *
 * HTML tags (e.g. <br>, <img>) are preserved and rendered by rehype-raw
 * in the Markdown component.
 */
function preprocessContent(contentMd: string, parser: string | undefined): string {
  if (!contentMd) return ''
  if (parser && parser !== 'plain_text') {
    return contentMd.replace(
      /^---\s*page\s*(\d+)\s*---$/gim,
      '\n\n---\n\n**\u00a0 Page $1**\n\n',
    )
  }
  return contentMd
}

const DETAIL_TABS = [
  { value: 'content' as const, label: '内容' },
  { value: 'versions' as const, label: '版本历史' },
]

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
  const [detailTab, setDetailTab] = useState<'content' | 'versions'>('content')

  const load = async () => {
    setLoading(true)
    try {
      const [detail, verList] = await Promise.all([
        getDocument(documentId),
        listVersions(documentId),
      ])
      setDoc(detail.document)
      setLatestVer(detail.version)
      const sorted = verList.sort((a, b) => b.version - a.version)
      setVersions(sorted)
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

  const currentVersion = useMemo(() => {
    if (!selectedVersionId) return latestVer
    return versions.find((v) => v.id === selectedVersionId) ?? latestVer
  }, [selectedVersionId, versions, latestVer])

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

  const activeTabIndex = DETAIL_TABS.findIndex((t) => t.value === detailTab)

  return (
    <div className="cognition-fade-up flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header */}
      <div className="shrink-0 border-b px-5 py-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted-foreground transition hover:bg-accent hover:text-foreground active:scale-95"
            onClick={onBack}
            title="返回"
            aria-label="返回"
          >
            <ArrowLeft className="size-4" />
          </Button>
          <div className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
            <FileText className="size-4 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="min-w-0 truncate text-sm font-semibold tracking-tight">{doc.title}</h2>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
              <Badge variant="outline" className="text-[9px] font-medium">{doc.docType}</Badge>
              <Badge variant="secondary" className="text-[9px] font-medium">
                {SOURCE_LABELS[doc.source] ?? doc.source}
              </Badge>
              <span className="tabular-nums">{formatTime(doc.updatedAt)}</span>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive active:scale-95"
            onClick={() => setDeleteOpen(true)}
            title="删除文档"
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* Tab bar with sliding indicator */}
      <div className="flex shrink-0 items-center justify-between border-b px-5 py-2.5">
        <div className="relative flex w-fit items-center rounded-lg bg-muted p-0.5">
          <span
            className="pointer-events-none absolute top-0.5 bottom-0.5 left-0.5 w-[calc(50%-2px)] rounded-md bg-background shadow-[var(--shadow-sm),var(--inset-hi)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{ transform: `translateX(${activeTabIndex * 100}%)` }}
          />
          {DETAIL_TABS.map((t) => {
            const isActive = t.value === detailTab
            return (
              <button
                key={t.value}
                type="button"
                onClick={() => setDetailTab(t.value)}
                className={cn(
                  'relative z-10 inline-flex h-7 w-[5.5rem] items-center justify-center rounded-md text-xs font-medium transition-colors duration-200',
                  isActive
                    ? 'text-foreground'
                    : 'text-muted-foreground hover:text-foreground/70',
                )}
              >
                {t.value === 'versions' ? `${t.label} ${versions.length}` : t.label}
              </button>
            )
          })}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 text-[10px] text-muted-foreground transition hover:text-foreground active:scale-95"
          onClick={() => setUploadOpen(true)}
        >
          <Upload className="size-3" />
          新版本
        </Button>
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div key={detailTab} className="tab-content-enter">
          {detailTab === 'content' ? (
            <div className="mx-auto max-w-3xl p-6">
              {currentVersion ? (
                <>
                  {versions.length > 1 && (
                    <div className="mb-4 flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span>正在查看</span>
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        v{currentVersion.version}
                      </Badge>
                      {currentVersion.id === doc.latestVersionId && (
                        <Badge variant="outline" className="text-[10px] text-success">最新</Badge>
                      )}
                      {currentVersion.summary && (
                        <>
                          <span className="text-border">|</span>
                          <span className="truncate">{currentVersion.summary}</span>
                        </>
                      )}
                    </div>
                  )}
                  <Markdown className="min-h-32">{renderedContent}</Markdown>
                </>
              ) : (
                <div className="flex items-center justify-center py-16 text-xs text-muted-foreground">
                  无内容
                </div>
              )}
            </div>
          ) : (
            <div className="mx-auto max-w-3xl p-6">
              {/* Version timeline */}
              <div className="relative">
                {/* Vertical connector line */}
                <div className="absolute left-[15px] top-3 bottom-3 w-px bg-border" />

                <div className="space-y-1">
                  {versions.map((ver) => {
                    const isActive = ver.id === (selectedVersionId ?? latestVer.id)
                    const parser = (ver.metadata?.parser as string | undefined) ?? undefined
                    return (
                      <div
                        key={ver.id}
                        className={cn(
                          'group relative flex cursor-pointer items-start gap-3 rounded-lg pl-1 pr-3 py-2.5 transition-all duration-150',
                          isActive
                            ? 'bg-primary/5 ring-1 ring-primary/15'
                            : 'hover:bg-accent/50',
                        )}
                        onClick={() => {
                          setSelectedVersionId(ver.id)
                          setDetailTab('content')
                        }}
                      >
                        {/* Timeline dot */}
                        <div className={cn(
                          'relative z-10 mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border-2 transition-all',
                          isActive
                            ? 'border-primary bg-primary/10'
                            : 'border-border bg-background group-hover:border-primary/30',
                        )}>
                          <span className={cn(
                            'text-[10px] font-mono font-semibold tabular-nums',
                            isActive ? 'text-primary' : 'text-muted-foreground',
                          )}>
                            {ver.version}
                          </span>
                        </div>

                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            {ver.id === doc.latestVersionId && (
                              <Badge variant="secondary" className="text-[9px] font-medium">最新</Badge>
                            )}
                            <span className="text-[10px] tabular-nums text-muted-foreground">
                              {formatTime(ver.createdAt)}
                            </span>
                            {parser && (
                              <span className="rounded bg-muted/60 px-1 py-0.5 text-[9px] text-muted-foreground">{parser}</span>
                            )}
                          </div>
                          {ver.summary && (
                            <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                              {ver.summary}
                            </p>
                          )}
                          {ingestingId === null && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                void handleIngest(ver.id)
                              }}
                              className="mt-1 text-[10px] text-primary transition hover:underline"
                            >
                              入库 RAG
                            </button>
                          )}
                          {ingestingId === ver.id && (
                            <span className="mt-1 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                              <Loader2 className="size-2.5 animate-spin" /> 入库中
                            </span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

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
