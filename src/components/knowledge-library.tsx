'use client'

import { BookOpen, ChevronRight, FileText, Folder, FolderOpen, Loader2, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { DocumentDetail } from '@/components/document-detail'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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

const TYPE_COLORS: Record<string, string> = {
  note: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  manual: 'bg-purple-500/10 text-purple-600 dark:text-purple-400',
  spec: 'bg-success/10 text-success',
  reference: 'bg-warning/10 text-warning',
  report: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
  other: 'bg-gray-500/10 text-gray-600 dark:text-gray-400',
}

// ─── Obsidian Vault Section ──────────────────────────────────

function ObsidianVaultSection({ onSelectFile }: { onSelectFile: (id: string) => void }) {
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
        // Navigate back to parent
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
    <div className="flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 px-1 py-1.5">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Folder className="size-3.5" />
          Obsidian Vault
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-6 gap-1 text-[10px] px-2"
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
        <div className="mx-1 mb-1.5 rounded-md border bg-primary/5 px-2 py-1.5 text-[10px]">
          <span className="font-medium">同步完成：</span>
          {syncReport.added > 0 && <span className="ml-1 text-green-600">+{syncReport.added}</span>}
          {syncReport.updated > 0 && <span className="ml-1 text-blue-600">↑{syncReport.updated}</span>}
          {syncReport.deleted > 0 && <span className="ml-1 text-red-600">−{syncReport.deleted}</span>}
          {syncReport.skipped > 0 && <span className="ml-1 text-muted-foreground">↷{syncReport.skipped}</span>}
          {syncReport.errors.length > 0 && (
            <span className="ml-1 text-destructive">⚠{syncReport.errors.length}错误</span>
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
        <div className="px-1 py-2 text-[10px] text-muted-foreground">
          未配置 Vault 路径，请在「设置」中配置后同步。
        </div>
      )}

      {/* Tree content */}
      {hasVault && loading && !tree && (
        <div className="flex items-center justify-center py-4 text-[10px] text-muted-foreground">
          <Loader2 className="mr-1.5 size-3 animate-spin" /> 加载中
        </div>
      )}

      {hasVault && tree && (
        <div className="space-y-0.5 px-1">
          {/* Breadcrumb */}
          {currentPath && (
            <button
              type="button"
              onClick={() => void loadTree(getParentPath(currentPath))}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
            >
              <ChevronRight className="size-2.5 rotate-180" />
              返回上级
            </button>
          )}

          {/* Folders */}
          {tree.folders.map((folder) => (
            <div
              key={folder.path}
              className="flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-accent"
              onClick={() => toggleFolder(folder)}
            >
              <Folder className="size-3.5 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{folder.name}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">({folder.docCount})</span>
            </div>
          ))}

          {/* Files */}
          {tree.files.map((file) => (
            <div
              key={file.id}
              className={cn(
                'flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-accent',
                selectedId === file.id && 'bg-primary/5',
              )}
              onClick={() => onSelectFile(file.id)}
            >
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{file.title}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">{formatTime(file.updatedAt)}</span>
            </div>
          ))}

          {tree.folders.length === 0 && tree.files.length === 0 && (
            <div className="py-4 text-center text-[10px] text-muted-foreground">
              此目录下没有文档
            </div>
          )}
        </div>
      )}

      {/* Status info */}
      {syncStatus && hasVault && (
        <div className="mt-1 px-1 text-[10px] text-muted-foreground">
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

function MyDocumentsSection() {
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
    <div className="flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-1.5 px-1 py-1.5 text-xs font-medium text-muted-foreground">
        <FileText className="size-3.5" />
        我的文档
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-4 text-[10px] text-muted-foreground">
          <Loader2 className="mr-1.5 size-3 animate-spin" /> 加载中
        </div>
      ) : documents.length === 0 ? (
        <div className="px-1 py-3 text-center text-[10px] text-muted-foreground">
          还没有上传的文档
        </div>
      ) : (
        <div className="space-y-0.5 px-1">
          {documents.map((doc) => {
            const meta = doc.latestMetadata ?? {}
            const parser = (meta.parser as string | undefined) ?? doc.latestParser
            const filename = (meta.filename as string | undefined) ?? null

            return (
              <div
                key={doc.id}
                className={cn(
                  'group flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-accent',
                  selectedId === doc.id && 'bg-primary/5',
                )}
                onClick={() => setSelectedId(doc.id)}
              >
                <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1">
                    <span className="min-w-0 truncate text-xs">{doc.title}</span>
                    <span className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                      v{doc.latestVersion}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span>{SOURCE_LABELS[doc.source] ?? doc.source}</span>
                    <span>·</span>
                    <span>{formatTime(doc.updatedAt)}</span>
                    {filename && (
                      <>
                        <span>·</span>
                        <span className="truncate">{filename}</span>
                      </>
                    )}
                    {parser && (
                      <>
                        <span>·</span>
                        <Badge variant="outline" className="text-[10px]">{parser}</Badge>
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
        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => void handleDrop(e)}
          className={cn(
            'mt-2 rounded-md border border-dashed px-3 py-2 text-center text-[10px] transition',
            dragOver ? 'border-primary bg-primary/5 text-primary' : 'text-muted-foreground',
          )}
        >
          {uploading ? '上传中...' : '拖入文档上传，或点「上传」选择'}
        </div>
        {uploadError && (
          <div className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
            {uploadError}
          </div>
        )}
      </div>

      {/* Obsidian Vault section */}
      <div className="border-b px-2 pb-2">
        <ObsidianVaultSection onSelectFile={setSelectedId} />
      </div>

      {/* My Documents section */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-2 pt-2">
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
      <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
        <BookOpen className="size-12 opacity-20" />
        <span className="text-sm">从左侧选择文档查看详情</span>
      </div>
    )
  }

  return <DocumentDetail documentId={selectedId} onBack={() => setSelectedId(null)} />
}
