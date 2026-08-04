'use client'

import { ChevronRight, FileText, Folder, FolderOpen, Loader2, RefreshCw, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  deleteDocument,
  fetchDocumentFlat,
  fetchDocumentTree,
  getObsidianStatus,
  syncObsidian,
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

const SOURCE_COLORS: Record<string, { dot: string; badge: string }> = {
  agent_generated: { dot: 'bg-violet-500', badge: 'bg-violet-500/10 text-violet-600 dark:text-violet-400' },
  user_upload: { dot: 'bg-blue-500', badge: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  obsidian_sync: { dot: 'bg-amber-500', badge: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  artifact_import: { dot: 'bg-emerald-500', badge: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
}

// ─── Obsidian Vault Section ──────────────────────────────────

export function ObsidianVaultSection({
  onSelectFile,
  search,
}: {
  onSelectFile: (id: string) => void
  search?: string
}) {
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
      <div className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <div className="flex size-4 items-center justify-center rounded-sm bg-amber-500/10">
            <Folder className="size-3 text-amber-500" />
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Obsidian</span>
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 gap-1 px-1.5 text-[10px] text-muted-foreground hover:text-foreground"
          onClick={() => void handleSync()}
          disabled={syncing || !hasVault}
          title={!hasVault ? '请先在设置中配置 Vault 路径' : '同步 Obsidian Vault'}
        >
          {syncing ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
        </Button>
      </div>

      {/* Sync report toast */}
      {syncReport && (
        <div className="cognition-fade-up rounded-lg border bg-card px-2.5 py-1.5 text-[10px] shadow-[var(--shadow-sm)]">
          <div className="flex items-center justify-between">
            <span className="font-medium text-foreground">同步完成</span>
            <button
              type="button"
              onClick={() => setSyncReport(null)}
              className="text-muted-foreground transition hover:text-foreground"
            >
              <X className="size-2.5" />
            </button>
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-muted-foreground">
            {syncReport.added > 0 && <span className="text-success">+{syncReport.added}</span>}
            {syncReport.updated > 0 && <span className="text-primary">↑{syncReport.updated}</span>}
            {syncReport.deleted > 0 && <span className="text-destructive">−{syncReport.deleted}</span>}
            {syncReport.skipped > 0 && <span>↷{syncReport.skipped}</span>}
            {syncReport.errors.length > 0 && (
              <span className="text-destructive">!{syncReport.errors.length} 错误</span>
            )}
          </div>
        </div>
      )}

      {/* No vault configured */}
      {!hasVault && (
        <div className="rounded-lg border border-dashed px-2.5 py-3 text-center text-[10px] text-muted-foreground">
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
              className="flex items-center gap-1 px-2 text-[10px] text-muted-foreground transition hover:text-foreground"
            >
              <ChevronRight className="size-2.5 rotate-180" />
              返回上级
            </button>
          )}

          {/* Folders */}
          {tree.folders.map((folder) => (
            <div
              key={folder.path}
              className="group flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs transition hover:bg-accent"
              onClick={() => toggleFolder(folder)}
            >
              <FolderOpen className="size-3.5 shrink-0 text-amber-500/80" />
              <span className="min-w-0 flex-1 truncate text-foreground">{folder.name}</span>
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
                {folder.docCount}
              </span>
            </div>
          ))}

          {/* Files */}
          {tree.files
            .filter((file) => !search || file.title.toLowerCase().includes(search.toLowerCase()))
            .map((file) => (
              <div
                key={file.id}
                className={cn(
                  'flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-all duration-150',
                  selectedId === file.id
                    ? 'bg-primary/10 text-primary'
                    : 'text-foreground/80 hover:bg-accent hover:text-foreground',
                )}
                onClick={() => onSelectFile(file.id)}
              >
                <div className="size-1 shrink-0 rounded-full bg-amber-500/60" />
                <span className="min-w-0 flex-1 truncate">{file.title}</span>
                <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{formatTime(file.updatedAt)}</span>
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
        <div className="px-2 text-[10px] tabular-nums text-muted-foreground/70">
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

export function MyDocumentsSection({ search }: { search?: string }) {
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

  const filteredDocuments = search
    ? documents.filter((d) => d.title.toLowerCase().includes(search.toLowerCase()))
    : documents

  return (
    <div className="flex flex-col gap-1.5">
      {/* Section header */}
      <div className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <div className="flex size-4 items-center justify-center rounded-sm bg-blue-500/10">
            <FileText className="size-3 text-blue-500" />
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">我的文档</span>
        </div>
        {!loading && filteredDocuments.length > 0 && (
          <span className="text-[10px] tabular-nums text-muted-foreground/60">{filteredDocuments.length}</span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <FileText className="size-6 text-muted-foreground/30" />
          <p className="text-[10px] text-muted-foreground">
            {search ? '没有匹配的文档' : '还没有上传的文档'}
          </p>
        </div>
      ) : (
        <div className="space-y-0.5">
          {filteredDocuments.map((doc) => {
            const meta = doc.latestMetadata ?? {}
            const parser = (meta.parser as string | undefined) ?? doc.latestParser
            const sourceColor = SOURCE_COLORS[doc.source] ?? SOURCE_COLORS.user_upload

            return (
              <div
                key={doc.id}
                className={cn(
                  'group flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition-all duration-150',
                  selectedId === doc.id
                    ? 'bg-primary/8 ring-1 ring-primary/20'
                    : 'hover:bg-accent',
                )}
                onClick={() => setSelectedId(doc.id)}
              >
                <div className={cn('size-1.5 shrink-0 rounded-full', sourceColor.dot)} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className={cn(
                      'min-w-0 truncate text-xs font-medium',
                      selectedId === doc.id ? 'text-primary' : 'text-foreground',
                    )}>{doc.title}</span>
                    <span className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[9px] tabular-nums text-muted-foreground">
                      v{doc.latestVersion}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    <span className={cn('rounded px-1 py-0.5 text-[9px] font-medium', sourceColor.badge)}>
                      {SOURCE_LABELS[doc.source] ?? doc.source}
                    </span>
                    <span className="tabular-nums">{formatTime(doc.updatedAt)}</span>
                    {parser && (
                      <span className="rounded bg-muted/60 px-1 py-0.5 text-[9px]">{parser}</span>
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
                  className="shrink-0 self-center rounded p-0.5 text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive"
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
