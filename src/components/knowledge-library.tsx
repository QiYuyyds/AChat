'use client'

import { BookOpen, ChevronRight, FileText, Folder, FolderOpen, Library, Loader2, Plus, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { DocumentDetail } from '@/components/document-detail'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { Badge } from '@/components/ui/badge'
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
import {
  deleteDocument,
  fetchDocumentFlat,
  fetchDocumentTree,
  fetchDocuments,
  getObsidianStatus,
  syncObsidian,
  uploadDocument,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { useAppStore } from '@/stores/app-store'
import type { DocumentRow, DocumentTree, FileNode, FolderNode, SyncReport, SyncStatus } from '@/shared/types'

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
  obsidian_sync: 'Obsidian',
  artifact_import: '产物',
}

// ─── Obsidian Vault Section ──────────────────────────────────

export function ObsidianVaultSection({ onSelectFile }: { onSelectFile: (id: string) => void }) {
  const [tree, setTree] = useState<DocumentTree | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [syncReport, setSyncReport] = useState<SyncReport | null>(null)
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [currentPath, setCurrentPath] = useState('')
  const selectedId = useAppStore((s) => s.selectedKnowledgeDocId)

  const loadTree = useCallback(async (path?: string) => {
    setLoading(true)
    try {
      const result = await fetchDocumentTree(path)
      setTree(result)
      setCurrentPath(result.currentPath)
    } catch (err) {
      console.error('[ObsidianVault] load tree failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const status = await getObsidianStatus()
      setSyncStatus(status)
    } catch {
      // Status is optional, don't block
    }
  }, [])

  useEffect(() => {
    void loadTree()
    void loadStatus()
  }, [loadTree, loadStatus])

  const handleSync = async () => {
    if (syncing) return
    setSyncing(true)
    setSyncReport(null)
    try {
      const report = await syncObsidian()
      setSyncReport(report)
      await loadTree(currentPath)
      await loadStatus()
    } catch (err) {
      console.error('[ObsidianVault] sync failed', err)
    } finally {
      setSyncing(false)
    }
  }

  const toggleFolder = (folder: FolderNode) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev)
      if (next.has(folder.path)) {
        next.delete(folder.path)
        void loadTree(getParentPath(folder.path))
      } else {
        next.add(folder.path)
        void loadTree(folder.path)
      }
      return next
    })
  }

  const getParentPath = (path: string): string => {
    const parts = path.split('/')
    parts.pop()
    return parts.join('/')
  }

  const hasVault = syncStatus?.vaultPath && syncStatus?.vaultExists

  return (
    <div className="flex flex-col gap-1.5">
      {/* Section header */}
      <div className="flex items-center justify-between gap-2 px-1 py-1">
        <div className="flex items-center gap-1.5">
          <Folder className="size-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-foreground">Obsidian Vault</span>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-6 gap-1 px-2 text-[10px]"
          onClick={() => void handleSync()}
          disabled={syncing || !hasVault}
          title={!hasVault ? '请先在设置中配置 Vault 路径' : '同步 Obsidian Vault'}
        >
          {syncing ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          同步
        </Button>
      </div>

      {/* Sync report toast */}
      {syncReport && (
        <div className="rounded-md border bg-primary/5 px-2.5 py-1.5 text-[10px]">
          <span className="font-medium">同步完成</span>
          {syncReport.added > 0 && <span className="ml-1.5 text-success">+{syncReport.added}</span>}
          {syncReport.updated > 0 && <span className="ml-1.5 text-primary">↑{syncReport.updated}</span>}
          {syncReport.deleted > 0 && <span className="ml-1.5 text-destructive">−{syncReport.deleted}</span>}
          {syncReport.skipped > 0 && <span className="ml-1.5 text-muted-foreground">↷{syncReport.skipped}</span>}
          {syncReport.errors.length > 0 && (
            <span className="ml-1.5 text-destructive">!{syncReport.errors.length} 错误</span>
          )}
          <button
            type="button"
            onClick={() => setSyncReport(null)}
            className="ml-1.5 text-muted-foreground hover:text-foreground"
          >
            <X className="size-2.5" />
          </button>
        </div>
      )}

      {/* No vault configured */}
      {!hasVault && (
        <div className="rounded-md border border-dashed px-2.5 py-2 text-center text-[10px] text-muted-foreground">
          未配置 Vault 路径
        </div>
      )}

      {/* Tree content */}
      {hasVault && loading && !tree && (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {hasVault && tree && (
        <div className="space-y-0.5">
          {/* Breadcrumb */}
          {currentPath && (
            <button
              type="button"
              onClick={() => void loadTree(getParentPath(currentPath))}
              className="flex items-center gap-1 text-[10px] text-muted-foreground transition hover:text-foreground"
            >
              <ChevronRight className="size-2.5 rotate-180" />
              返回上级
            </button>
          )}

          {/* Folders */}
          {tree.folders.map((folder) => (
            <div
              key={folder.path}
              className="flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs transition hover:bg-accent"
              onClick={() => toggleFolder(folder)}
            >
              <FolderOpen className="size-3.5 shrink-0 text-warning" />
              <span className="min-w-0 flex-1 truncate">{folder.name}</span>
              <span className="shrink-0 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                {folder.docCount}
              </span>
            </div>
          ))}

          {/* Files */}
          {tree.files.map((file) => (
            <div
              key={file.id}
              className={cn(
                'flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs transition hover:bg-accent',
                selectedId === file.id && 'bg-primary/10 text-primary',
              )}
              onClick={() => onSelectFile(file.id)}
            >
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{file.title}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">{formatTime(file.updatedAt)}</span>
            </div>
          ))}

          {tree.folders.length === 0 && tree.files.length === 0 && (
            <div className="py-3 text-center text-[10px] text-muted-foreground">
              此目录下没有文档
            </div>
          )}
        </div>
      )}

      {/* Status info */}
      {syncStatus && hasVault && (
        <div className="px-1 text-[10px] text-muted-foreground">
          {syncStatus.totalMdFiles} 个 .md 文件
          {syncStatus.lastSyncAt && (
            <> · 上次同步 {formatTime(syncStatus.lastSyncAt)}</>
          )}
        </div>
      )}
    </div>
  )
}

// ─── My Documents Section (flat list) ────────────────────────

export function MyDocumentsSection() {
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const selectedId = useAppStore((s) => s.selectedKnowledgeDocId)
  const setSelectedId = useAppStore((s) => s.setSelectedKnowledgeDocId)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const list = await fetchDocumentFlat()
      setDocuments(list)
    } catch (err) {
      console.error('[MyDocuments] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useGuideSideEffectRefresh('documents', () => { void refresh() })

  const handleDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteDocument(deleteTargetId)
      setDocuments((arr) => arr.filter((d) => d.id !== deleteTargetId))
      if (selectedId === deleteTargetId) setSelectedId(null)
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[MyDocuments] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  const deleteTarget = deleteTargetId
    ? documents.find((d) => d.id === deleteTargetId)
    : null

  return (
    <div className="flex flex-col gap-1.5">
      {/* Section header */}
      <div className="flex items-center gap-1.5 px-1 py-1">
        <FileText className="size-3.5 text-muted-foreground" />
        <span className="text-xs font-medium text-foreground">我的文档</span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : documents.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <FileText className="size-6 text-muted-foreground/40" />
          <p className="text-[10px] text-muted-foreground">还没有上传的文档</p>
        </div>
      ) : (
        <div className="space-y-1">
          {documents.map((doc) => {
            const meta = doc.latestMetadata ?? {}
            const parser = (meta.parser as string | undefined) ?? doc.latestParser
            const filename = (meta.filename as string | undefined) ?? null

            return (
              <div
                key={doc.id}
                className={cn(
                  'group flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition-all duration-150',
                  selectedId === doc.id
                    ? 'border-primary/40 bg-primary/5 shadow-[var(--shadow-sm)]'
                    : 'border-border/40 hover:border-primary/20 hover:bg-accent/50',
                )}
                onClick={() => setSelectedId(doc.id)}
              >
                <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="min-w-0 truncate text-xs font-medium text-foreground">{doc.title}</span>
                    <span className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                      v{doc.latestVersion}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    <span>{SOURCE_LABELS[doc.source] ?? doc.source}</span>
                    <span>·</span>
                    <span>{formatTime(doc.updatedAt)}</span>
                    {parser && (
                      <>
                        <span>·</span>
                        <span className="rounded bg-muted/60 px-1 py-0.5 text-[9px]">{parser}</span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleteTargetId(doc.id)
                  }}
                  title="删除文档"
                  className="shrink-0 self-center opacity-0 transition group-hover:opacity-100 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {/* Delete confirmation */}
      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !deleting && !open && setDeleteTargetId(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>删除文档</DialogTitle>
            <DialogDescription>
              确定要删除「{deleteTarget?.title}」吗？文档将标记为已删除，所有 RAG 分块也会被清理。此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setDeleteTargetId(null)}>
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
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ─── Main Sidebar ─────────────────────────────────────────────

/** 侧边栏导航：文档列表 + 搜索 + 上传入口 */
export function KnowledgeSidebarNav() {
  const [search, setSearch] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const selectedId = useAppStore((s) => s.selectedKnowledgeDocId)
  const setSelectedId = useAppStore((s) => s.setSelectedKnowledgeDocId)

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const files = Array.from(e.dataTransfer.files)
      if (files.length === 0) return
      setUploading(true)
      setUploadError(null)
      try {
        for (const file of files) await uploadDocument(file)
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err))
      } finally {
        setUploading(false)
      }
    },
    [],
  )

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 px-3 pt-4 pb-3">
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10">
            <Library className="size-3.5 text-primary" />
          </div>
          <h2 className="text-sm font-semibold">知识库</h2>
        </div>
      </div>

      {/* Search + upload */}
      <div className="shrink-0 px-3 pb-2">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文档..."
              className="w-full rounded-md border bg-background py-1.5 pl-8 pr-7 text-xs outline-none transition focus:border-primary/40"
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
            className="h-8 gap-1 text-xs"
            onClick={() => setUploadOpen(true)}
          >
            <Upload className="size-3.5" />
            上传
          </Button>
        </div>
        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => void handleDrop(e)}
          className={cn(
            'mt-2 flex items-center justify-center gap-1.5 rounded-md border border-dashed px-3 py-2 text-center text-[10px] transition',
            dragOver
              ? 'border-primary bg-primary/5 text-primary'
              : 'text-muted-foreground hover:border-border/60',
          )}
        >
          {uploading ? (
            <><Loader2 className="size-3 animate-spin" /> 上传中...</>
          ) : (
            <><Plus className="size-3" /> 拖入文档上传</>
          )}
        </div>
        {uploadError && (
          <div className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
            {uploadError}
          </div>
        )}
      </div>

      {/* Obsidian Vault section */}
      <div className="border-t px-2 py-2">
        <ObsidianVaultSection onSelectFile={setSelectedId} />
      </div>

      {/* My Documents section */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="border-t px-2 py-2">
          <MyDocumentsSection />
        </div>
      </ScrollArea>

      {/* Upload dialog */}
      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => {}}
      />
    </div>
  )
}

/** 主区域内容：文档详情或空状态 */
export function KnowledgeMainPanel() {
  const selectedId = useAppStore((s) => s.selectedKnowledgeDocId)
  const setSelectedId = useAppStore((s) => s.setSelectedKnowledgeDocId)

  if (!selectedId) {
    return (
      <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background/85 backdrop-blur-2xl">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex size-14 items-center justify-center rounded-2xl bg-muted/60 shadow-[var(--shadow-sm)]">
            <BookOpen className="size-6 text-muted-foreground" />
          </div>
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-foreground">知识库</p>
            <p className="text-xs text-muted-foreground">从左侧选择文档查看详情</p>
          </div>
        </div>
      </div>
    )
  }

  return <DocumentDetail documentId={selectedId} onBack={() => setSelectedId(null)} />
}
