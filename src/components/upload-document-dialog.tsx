'use client'

import { AlertCircle, CheckCircle2, FileUp, Loader2, ScanText, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

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
import { fetchOcrEngines, fetchRagPresets, uploadDocument } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { OcrEngineStatus, RagPreset, UploadResult } from '@/shared/types'

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
  documentId,
  defaultTitle,
  defaultDocType,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploaded?: () => void
  /** When provided, the dialog operates in "new version" mode for this document */
  documentId?: string
  /** Pre-fill title (used in new-version mode) */
  defaultTitle?: string
  /** Pre-fill doc type (used in new-version mode) */
  defaultDocType?: string
}) {
  const isVersionMode = !!documentId
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('note')
  const [autoIngest, setAutoIngest] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [presets, setPresets] = useState<RagPreset[]>([])
  const [presetId, setPresetId] = useState('')
  const [ocrEngines, setOcrEngines] = useState<OcrEngineStatus[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  // Pre-fill title and doc type when entering new-version mode
  useEffect(() => {
    if (open && isVersionMode) {
      setTitle(defaultTitle || '')
      setDocType(defaultDocType || 'note')
    }
  }, [open, isVersionMode, defaultTitle, defaultDocType])

  // Load RAG presets when dialog opens
  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetchRagPresets()
      .then((data) => {
        if (!cancelled) setPresets(data.presets)
      })
      .catch((err) => console.error('[UploadDialog] fetchRagPresets failed', err))
    return () => { cancelled = true }
  }, [open])

  // Load OCR engine status when needs_ocr is detected
  useEffect(() => {
    if (!result?.needsOcr) return
    fetchOcrEngines()
      .then((data) => setOcrEngines(data.engines))
      .catch((err) => console.error('[UploadDialog] fetchOcrEngines failed', err))
  }, [result?.needsOcr])

  const reset = () => {
    setFile(null)
    if (!isVersionMode) {
      setTitle('')
      setDocType('note')
    }
    setAutoIngest(true)
    setResult(null)
    setError(null)
    setPresetId('')
    setOcrEngines([])
  }

  const handleFileSelect = useCallback((f: File | null) => {
    if (!f) return
    setFile(f)
    setResult(null)
    setError(null)
    // Auto-fill title from filename if empty and not in version mode
    if (!title && !isVersionMode) {
      const baseName = f.name.replace(/\.[^.]+$/, '')
      setTitle(baseName)
    }
  }, [title, isVersionMode])

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
      const res = await uploadDocument(file, {
        documentId: documentId,
        title: title || undefined,
        docType: docType || undefined,
        presetId: presetId || undefined,
      })
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
            {isVersionMode ? '上传新版本' : '上传文档'}
          </DialogTitle>
          <DialogDescription>
            {isVersionMode
              ? '上传文件作为该文档的新版本，将自动清理旧版本数据后重新入库。'
              : '上传文件后自动解析并创建文档，可选入库到 RAG 知识库。'}
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
                ? 'border-success/40 bg-success/10'
                : 'border-border hover:border-foreground/30 hover:bg-accent/50',
          )}
        >
          {file ? (
            <div className="flex flex-col items-center gap-1">
              <CheckCircle2 className="size-6 text-success" />
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

        {/* Chunking preset selector */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium">分块策略</label>
          <select
            value={presetId}
            onChange={(e) => setPresetId(e.target.value)}
            className="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:border-foreground/30"
          >
            <option value="">跟随默认</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          {presetId === 'semantic' && (
            <p className="text-[10px] text-muted-foreground">需要嵌入模型，处理较慢</p>
          )}
        </div>

        {/* Result display */}
        {result && (
          <div className={cn(
            'rounded-md border px-3 py-2 text-xs',
            result.success
              ? 'border-success/30 bg-success/10 text-success'
              : 'border-warning/30 bg-warning/10 text-warning',
          )}>
            <div className="flex items-center gap-1.5 font-medium">
              {result.success ? <CheckCircle2 className="size-3.5" /> : <AlertCircle className="size-3.5" />}
              {result.success ? '上传成功' : '上传失败'}
            </div>
            {result.parser && <div className="mt-1 text-[10px]">解析器: {result.parser}</div>}
            {result.pages != null && <div className="text-[10px]">页数: {result.pages}</div>}
            {result.textChars != null && <div className="text-[10px]">字数: {result.textChars}</div>}
            {result.chunkCount != null && <div className="text-[10px]">分块数: {result.chunkCount}</div>}
            {result.needsOcr && (
              <div className="mt-2 space-y-1.5">
                <div className="flex items-center gap-1.5 text-[10px] text-warning">
                  <ScanText className="size-3" />
                  检测到扫描件，需要 OCR
                </div>
                {ocrEngines.length > 0 ? (
                  <div className="space-y-0.5">
                    {ocrEngines.map((eng) => (
                      <div key={eng.id} className="flex items-center gap-1.5 text-[10px]">
                        <span className={cn('size-1.5 rounded-full', eng.available ? 'bg-success' : 'bg-muted-foreground/40')} />
                        <span className="font-medium">{eng.label}</span>
                        <span className="text-muted-foreground">
                          {eng.status === 'ok' ? '可用' :
                           eng.status === 'not_installed' ? '未安装' :
                           eng.status === 'not_configured' ? '未配置 Key' :
                           '服务不可达'}
                        </span>
                      </div>
                    ))}
                    {ocrEngines.some((e) => e.available) ? (
                      <p className="text-[10px] text-success">已使用可用引擎自动解析</p>
                    ) : (
                      <p className="text-[10px] text-warning">无可用 OCR 引擎，请前往设置配置</p>
                    )}
                  </div>
                ) : (
                  <p className="text-[10px] text-muted-foreground">正在检查 OCR 引擎状态...</p>
                )}
              </div>
            )}
            {result.message && <div className="mt-0.5 text-[10px]">{result.message}</div>}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
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
