'use client'

import { ArrowLeft, FileText, Loader2, Trash2, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DocumentVersionItem } from '@/components/document-version-item'
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
      setVersions(verList.sort((a, b) => b.version - a.version))
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

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!doc || !latestVer) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2">
        <span className="text-xs text-muted-foreground">文档未找到</span>
        <Button variant="outline" size="sm" onClick={onBack}>
          返回列表
        </Button>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="size-7" onClick={onBack} title="返回">
            <ArrowLeft className="size-4" />
          </Button>
          <FileText className="size-4 text-muted-foreground" />
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
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-9 text-[10px] text-muted-foreground">
          <Badge variant="outline" className="text-[10px]">{doc.docType}</Badge>
          <Badge variant="ghost" className="text-[10px]">
            {SOURCE_LABELS[doc.source] ?? doc.source}
          </Badge>
          <span>·</span>
          <span>{doc.createdBy}</span>
          <span>·</span>
          <span>创建于 {formatTime(doc.createdAt)}</span>
          <span>·</span>
          <span>更新于 {formatTime(doc.updatedAt)}</span>
        </div>
      </div>

      {/* Version history */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1.5 p-2">
          <div className="flex items-center justify-between px-1 py-1">
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
              上传新版本
            </Button>
          </div>
          {versions.map((ver) => (
            <DocumentVersionItem
              key={ver.id}
              version={ver}
              isLatest={ver.id === doc.latestVersionId}
              onIngest={ingestingId === null ? (vid) => handleIngest(vid) : undefined}
            />
          ))}
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
