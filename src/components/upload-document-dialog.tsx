'use client'

import { AlertCircle, CheckCircle2, FileUp, Loader2, Upload } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { uploadDocument } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { UploadResult } from '@/shared/types'

const DOC_TYPES = [
  { value: 'note', label: '笔记' },
  { value: 'manual', label: '手册' },
  { value: 'spec', label: '规格' },
  { value: 'reference', label: '参考' },
  { value: 'report', label: '报告' },
  { value: 'other', label: '其他' },
]

export function UploadDocumentDialog({
  open,
  onOpenChange,
  onUploaded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploaded?: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('note')
  const [autoIngest, setAutoIngest] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const reset = () => {
    setFile(null)
    setTitle('')
    setDocType('note')
    setAutoIngest(true)
    setResult(null)
    setError(null)
  }

  const handleFileSelect = useCallback((f: File | null) => {
    if (!f) return
    setFile(f)
    setResult(null)
    setError(null)
    // Auto-fill title from filename if empty
    if (!title) {
      const baseName = f.name.replace(/\.[^.]+$/, '')
      setTitle(baseName)
    }
  }, [title])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFileSelect(f)
  }, [handleFileSelect])

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    setResult(null)
    try {
      const res = await uploadDocument(file)
      setResult(res)
      if (res.success && onUploaded) {
        onUploaded()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }

  const handleClose = (open: boolean) => {
    if (!open) {
      reset()
    }
    onOpenChange(open)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileUp className="size-4" />
            上传文档
          </DialogTitle>
          <DialogDescription>
            上传文件后自动解析并创建文档，可选入库到 RAG 知识库。
          </DialogDescription>
        </DialogHeader>

        {/* File drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed py-8 transition',
            dragOver
              ? 'border-primary bg-primary/5'
              : file
                ? 'border-green-500/40 bg-green-50/30 dark:bg-green-950/10'
                : 'border-border hover:border-foreground/30 hover:bg-accent/50',
          )}
        >
          {file ? (
            <div className="flex flex-col items-center gap-1">
              <CheckCircle2 className="size-6 text-green-500" />
              <span className="text-xs font-medium">{file.name}</span>
              <span className="text-[10px] text-muted-foreground">
                {(file.size / 1024).toFixed(1)} KB
              </span>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1 text-muted-foreground">
              <Upload className="size-6" />
              <span className="text-xs">点击或拖拽文件到此处</span>
              <span className="text-[10px]">支持 PDF、TXT、Markdown 等</span>
            </div>
          )}
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept=".pdf,.txt,.md,.markdown,.text"
            onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
          />
        </div>

        {/* Title */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium">标题</label>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="文档标题"
            className="h-8 text-xs"
          />
        </div>

        {/* Doc type */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium">类型</label>
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            className="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:border-foreground/30"
          >
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>

        {/* Auto ingest */}
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={autoIngest}
            onChange={(e) => setAutoIngest(e.target.checked)}
            className="size-3.5 rounded border-border"
          />
          <span>上传后自动入库到 RAG</span>
        </label>

        {/* Result display */}
        {result && (
          <div className={cn(
            'rounded-md border px-3 py-2 text-xs',
            result.success
              ? 'border-green-500/30 bg-green-50/30 text-green-700 dark:bg-green-950/10 dark:text-green-400'
              : 'border-amber-500/30 bg-amber-50/30 text-amber-700 dark:bg-amber-950/10 dark:text-amber-400',
          )}>
            <div className="flex items-center gap-1.5 font-medium">
              {result.success ? <CheckCircle2 className="size-3.5" /> : <AlertCircle className="size-3.5" />}
              {result.success ? '上传成功' : '上传失败'}
            </div>
            {result.parser && <div className="mt-1 text-[10px]">解析器: {result.parser}</div>}
            {result.pages != null && <div className="text-[10px]">页数: {result.pages}</div>}
            {result.textChars != null && <div className="text-[10px]">字数: {result.textChars}</div>}
            {result.chunkCount != null && <div className="text-[10px]">分块数: {result.chunkCount}</div>}
            {result.needsOcr && <div className="text-[10px] text-amber-600">需要 OCR</div>}
            {result.message && <div className="mt-0.5 text-[10px]">{result.message}</div>}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-md border border-red-500/30 bg-red-50/30 px-3 py-2 text-xs text-red-700 dark:bg-red-950/10 dark:text-red-400">
            <div className="flex items-center gap-1.5 font-medium">
              <AlertCircle className="size-3.5" />
              上传出错
            </div>
            <div className="mt-0.5 text-[10px]">{error}</div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => handleClose(false)} size="sm">
            {result?.success ? '关闭' : '取消'}
          </Button>
          <Button
            onClick={() => void handleUpload()}
            disabled={!file || uploading}
            size="sm"
          >
            {uploading ? (
              <>
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                上传中...
              </>
            ) : (
              <>
                <Upload className="mr-1.5 size-3.5" />
                上传
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
