'use client'

import { Check, File as FileIcon, FileText, Image as ImageIcon, Loader2, Paperclip, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { formatSize } from '@/components/attachment-chip'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { AttachmentRow } from '@/db/schema'
import {
  attachmentDownloadUrl,
  deleteAttachment as deleteAttachmentAPI,
  fetchAttachments,
  uploadAttachment as uploadAttachmentAPI,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore, usePendingAttachments } from '@/stores/app-store'

/**
 * FileLibraryDialog — 单会话文件库视图。
 *
 * 顶部上传 button；图片网格 + 文件列表两个分组；hover 显示删除；
 * 删除走二次确认。删除后只清 attachments，不会影响已发消息里的引用——
 * 那些 AttachmentChip 会根据 404 显示「已删除」（沿用 ArtifactRefPart 思路）。
 */
export function FileLibraryDialog({
  open,
  onOpenChange,
  conversationId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: string
}) {
  const [attachments, setAttachments] = useState<AttachmentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [uploadingCount, setUploadingCount] = useState(0)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const pending = usePendingAttachments(conversationId)
  const pendingIds = new Set(pending.map((a) => a.id))
  const addPendingAttachment = useAppStore((s) => s.addPendingAttachment)
  const removePendingAttachment = useAppStore((s) => s.removePendingAttachment)

  const toggleAttach = (a: AttachmentRow) => {
    if (pendingIds.has(a.id)) removePendingAttachment(conversationId, a.id)
    else addPendingAttachment(conversationId, a)
  }

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetchAttachments(conversationId)
      .then(setAttachments)
      .catch((err) => console.error('[FileLibraryDialog] load', err))
      .finally(() => setLoading(false))
  }, [open, conversationId])

  const images = attachments.filter((a) => a.kind === 'image')
  const files = attachments.filter((a) => a.kind === 'file')

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return
    setUploadingCount((n) => n + fileList.length)
    await Promise.all(
      Array.from(fileList).map(async (f) => {
        try {
          const att = await uploadAttachmentAPI(conversationId, f)
          setAttachments((prev) => [att, ...prev])
        } catch (err) {
          console.error('[FileLibraryDialog] upload', err)
        } finally {
          setUploadingCount((n) => Math.max(0, n - 1))
        }
      }),
    )
  }

  const confirmDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteAttachmentAPI(deleteTargetId)
      setAttachments((prev) => prev.filter((a) => a.id !== deleteTargetId))
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[FileLibraryDialog] delete', err)
    } finally {
      setDeleting(false)
    }
  }

  const deleteTarget = deleteTargetId ? attachments.find((a) => a.id === deleteTargetId) : null

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[85vh] overflow-x-hidden overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>会话文件库</DialogTitle>
            <DialogDescription>
              当前会话上传的所有图片和文件。Agent 可以在 prompt 中引用它们。
            </DialogDescription>
          </DialogHeader>

          {/* 上传 */}
          <div className="shrink-0">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                void handleUpload(e.target.files)
                e.target.value = ''
              }}
            />
            <Button
              variant="outline"
              className="gap-2"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingCount > 0}
            >
              <Upload className="size-4" />
              {uploadingCount > 0 ? `上传中 (${uploadingCount})…` : '上传文件 / 图片'}
            </Button>
          </div>

          <ScrollArea className="-mx-6 max-h-[55vh] overflow-x-hidden">
            <div className="min-w-0 space-y-5 overflow-hidden px-6 py-2">
              {/* 图片分组 */}
              <Section title="图片" icon={<ImageIcon className="size-4" />} count={images.length}>
                {loading ? (
                  <SkeletonGrid />
                ) : images.length === 0 ? (
                  <EmptyHint>没有图片</EmptyHint>
                ) : (
                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                    {images.map((a) => (
                      <ImageGridItem
                        key={a.id}
                        attachment={a}
                        attached={pendingIds.has(a.id)}
                        onToggleAttach={() => toggleAttach(a)}
                        onDelete={() => setDeleteTargetId(a.id)}
                      />
                    ))}
                  </div>
                )}
              </Section>

              {/* 文件分组 */}
              <Section title="文件" icon={<FileIcon className="size-4" />} count={files.length}>
                {loading ? (
                  <EmptyHint>
                    <Loader2 className="mr-1 inline size-3 animate-spin" /> 加载中
                  </EmptyHint>
                ) : files.length === 0 ? (
                  <EmptyHint>没有文件</EmptyHint>
                ) : (
                  <div className="min-w-0 space-y-1 overflow-hidden">
                    {files.map((a) => (
                      <FileRow
                        key={a.id}
                        attachment={a}
                        attached={pendingIds.has(a.id)}
                        onToggleAttach={() => toggleAttach(a)}
                        onDelete={() => setDeleteTargetId(a.id)}
                      />
                    ))}
                  </div>
                )}
              </Section>
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除附件</DialogTitle>
            <DialogDescription className="min-w-0">
              <span>确定删除「</span>
              <span className="inline-block max-w-full align-bottom">
                <span className="block max-w-[min(28rem,70vw)] truncate" title={deleteTarget?.fileName}>
                  {deleteTarget?.fileName}
                </span>
              </span>
              <span>」吗？已发送消息中对此附件的引用会显示「已删除」。</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTargetId(null)}>
              取消
            </Button>
            <Button
              className="bg-red-600 hover:bg-red-700"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {deleting ? '删除中...' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ─── 子组件 ──────────────────────────────────────────────
function Section({
  title,
  icon,
  count,
  children,
}: {
  title: string
  icon: React.ReactNode
  count: number
  children: React.ReactNode
}) {
  return (
    <div className="min-w-0 space-y-2 overflow-hidden">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {icon}
        <span>{title}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{count}</span>
      </div>
      {children}
    </div>
  )
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-4 text-center text-xs text-muted-foreground">{children}</div>
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="aspect-square animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  )
}

function ImageGridItem({
  attachment,
  attached,
  onToggleAttach,
  onDelete,
}: {
  attachment: AttachmentRow
  attached: boolean
  onToggleAttach: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        'group relative aspect-square overflow-hidden rounded-md border bg-muted',
        attached && 'ring-2 ring-primary',
      )}
    >
      <a
        href={attachmentDownloadUrl(attachment.id)}
        target="_blank"
        rel="noopener noreferrer"
        title={attachment.fileName}
        className="block size-full"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={attachmentDownloadUrl(attachment.id)}
          alt={attachment.fileName}
          className="size-full object-cover"
        />
      </a>
      <div
        className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 to-transparent px-1.5 py-1 text-[10px] text-white opacity-0 transition group-hover:opacity-100"
        title={attachment.fileName}
      >
        {attachment.fileName}
      </div>
      <div className="absolute right-1 top-1 flex gap-1 opacity-0 transition group-hover:opacity-100">
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault()
            onToggleAttach()
          }}
          className={cn(
            'rounded-full p-1 text-white transition',
            attached ? 'bg-primary' : 'bg-black/60 hover:bg-primary',
          )}
          title={attached ? '已附加（再次点击移除）' : '附加到当前消息'}
        >
          {attached ? <Check className="size-3" /> : <Paperclip className="size-3" />}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault()
            onDelete()
          }}
          className="rounded-full bg-black/60 p-1 text-white transition hover:bg-red-600"
          title="删除"
        >
          <Trash2 className="size-3" />
        </button>
      </div>
    </div>
  )
}

function FileRow({
  attachment,
  attached,
  onToggleAttach,
  onDelete,
}: {
  attachment: AttachmentRow
  attached: boolean
  onToggleAttach: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        'group grid w-full min-w-0 max-w-full grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-2 overflow-hidden rounded-md border bg-card px-3 py-2 transition hover:border-foreground/20',
        attached && 'border-primary/40 bg-primary/5',
      )}
    >
      <FileIconFor mime={attachment.mimeType} />
      <a
        href={attachmentDownloadUrl(attachment.id)}
        target="_blank"
        rel="noopener noreferrer"
        download={attachment.fileName}
        className="block min-w-0 overflow-hidden"
        title={attachment.fileName}
      >
        <div className="block max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-xs font-medium">
          {attachment.fileName}
        </div>
        <div className="block max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[10px] text-muted-foreground">
          {formatSize(attachment.size)} · {attachment.mimeType}
        </div>
      </a>
      <button
        type="button"
        onClick={onToggleAttach}
        className={cn(
          'shrink-0 rounded p-1 transition',
          attached
            ? 'bg-primary text-primary-foreground'
            : 'text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100',
        )}
        title={attached ? '已附加（再次点击移除）' : '附加到当前消息'}
      >
        {attached ? <Check className="size-3.5" /> : <Paperclip className="size-3.5" />}
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="shrink-0 opacity-0 transition group-hover:opacity-100 hover:text-red-600"
        title="删除"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  )
}

function FileIconFor({ mime }: { mime: string }) {
  if (mime.startsWith('text/') || mime === 'application/json' || mime === 'application/pdf') {
    return <FileText className="size-5 shrink-0 text-muted-foreground" />
  }
  return <FileIcon className="size-5 shrink-0 text-muted-foreground" />
}
