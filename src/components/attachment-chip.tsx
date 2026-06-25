'use client'

import { File as FileIcon, FileText, Image as ImageIcon, Loader2, X } from 'lucide-react'

import { attachmentDownloadUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface AttachmentChipData {
  id: string
  fileName: string
  size: number
  mimeType: string
  kind: 'image' | 'file'
}

interface AttachmentChipProps {
  attachment: AttachmentChipData
  onRemove?: () => void
  /** 是否在消息泡里展示（更紧凑），还是在 input 区（更宽松） */
  context?: 'compose' | 'message'
}

export function AttachmentChip({ attachment, onRemove, context = 'compose' }: AttachmentChipProps) {
  if (attachment.kind === 'image') {
    return <ImageChip attachment={attachment} onRemove={onRemove} context={context} />
  }
  return <FileChip attachment={attachment} onRemove={onRemove} context={context} />
}

// ─── Image chip：方形缩略图 ──────────────────────────
function ImageChip({ attachment, onRemove, context }: AttachmentChipProps) {
  const sizeClass = context === 'compose' ? 'size-16' : 'size-32 max-w-full'
  return (
    <div className={cn('group relative overflow-hidden rounded-md border bg-muted', sizeClass)}>
      <a
        href={attachmentDownloadUrl(attachment.id)}
        target="_blank"
        rel="noopener noreferrer"
        className="block size-full"
        title={attachment.fileName}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={attachmentDownloadUrl(attachment.id)}
          alt={attachment.fileName}
          className="size-full object-cover"
        />
      </a>
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault()
            onRemove()
          }}
          className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white opacity-0 transition group-hover:opacity-100 hover:bg-black/80"
          title="移除"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

// ─── File chip：行卡片 ──────────────────────────────
function FileChip({ attachment, onRemove, context }: AttachmentChipProps) {
  const compact = context === 'compose'
  return (
    <div
      className={cn(
        'group flex items-center gap-2 rounded-md border bg-card transition hover:border-foreground/30',
        compact ? 'max-w-[240px] px-2 py-1.5' : 'max-w-sm px-3 py-2',
      )}
    >
      <FileIconByMime mime={attachment.mimeType} />
      <a
        href={attachmentDownloadUrl(attachment.id)}
        target="_blank"
        rel="noopener noreferrer"
        download={attachment.fileName}
        className="min-w-0 flex-1 text-left"
        title={attachment.fileName}
      >
        <div className="truncate text-xs font-medium">{attachment.fileName}</div>
        <div className="text-[10px] text-muted-foreground">{formatSize(attachment.size)}</div>
      </a>
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault()
            onRemove()
          }}
          className="shrink-0 rounded p-0.5 text-muted-foreground transition hover:text-foreground"
          title="移除"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

function FileIconByMime({ mime }: { mime: string }) {
  if (mime.startsWith('image/')) {
    return <ImageIcon className="size-5 shrink-0 text-muted-foreground" />
  }
  if (mime.startsWith('text/') || mime === 'application/json') {
    return <FileText className="size-5 shrink-0 text-muted-foreground" />
  }
  return <FileIcon className="size-5 shrink-0 text-muted-foreground" />
}

// ─── 上传中态 chip ──────────────────────────────────
export function PendingAttachmentChip({
  fileName,
  onCancel,
}: {
  fileName: string
  onCancel?: () => void
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-dashed bg-muted/40 px-2 py-1.5 text-xs text-muted-foreground">
      <Loader2 className="size-3 shrink-0 animate-spin" />
      <span className="max-w-[160px] truncate">{fileName}</span>
      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-0.5 hover:text-foreground"
          title="取消"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
