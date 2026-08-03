'use client'

import { Ban, Code2, GitMerge, Loader2, ShieldCheck, Swords } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  fetchPendingMergeConflicts,
  resolveMergeConflict,
  type PendingMergeConflict,
} from '@/lib/api'
import { useAppStore, usePendingMergeConflicts } from '@/stores/app-store'

type ResolveAction = 'ours' | 'theirs' | 'edit' | 'abandon'

/**
 * MergeConflictPanel — worktree 合并冲突审批面板。
 *
 * 当并行子 Agent 的 worktree merge-back 遇到冲突且 LLM 无法自动解决时，
 * 冲突推到前端此面板，等待用户决策：保留我方 / 保留对方 / 手动编辑 / 放弃。
 */
export function MergeConflictPanel({ conversationId }: { conversationId: string }) {
  const pending = usePendingMergeConflicts(conversationId)
  const setList = useAppStore((s) => s.setPendingMergeConflictsForConversation)

  useEffect(() => {
    let cancelled = false
    fetchPendingMergeConflicts(conversationId)
      .then((list) => {
        if (!cancelled) setList(conversationId, list)
      })
      .catch((err) => {
        console.warn('[MergeConflictPanel] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setList])

  if (pending.length === 0) return null

  return (
    <div className="shrink-0 space-y-2 border-t bg-destructive/10 px-4 py-2.5">
      {pending.map((c) => (
        <MergeConflictCard key={c.id} conversationId={conversationId} conflict={c} />
      ))}
    </div>
  )
}

function MergeConflictCard({
  conversationId,
  conflict,
}: {
  conversationId: string
  conflict: PendingMergeConflict
}) {
  const [busy, setBusy] = useState<ResolveAction | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editContents, setEditContents] = useState<Record<string, string>>({})

  const handleResolve = useCallback(
    async (action: ResolveAction, fileContents?: Record<string, string>) => {
      setBusy(action)
      setError(null)
      try {
        await resolveMergeConflict(conversationId, conflict.id, action, fileContents)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        setBusy(null)
      }
    },
    [conversationId, conflict.id],
  )

  const startEdit = () => {
    const initial: Record<string, string> = {}
    for (const f of conflict.conflictFiles) {
      initial[f] = ''
    }
    setEditContents(initial)
    setEditing(true)
  }

  const submitEdit = () => {
    setEditing(false)
    void handleResolve('edit', editContents)
  }

  return (
    <div className="rounded-lg border bg-card px-3 py-2 text-xs shadow-sm">
      <div className="flex items-center gap-2">
        <Swords className="size-4 shrink-0 text-destructive" />
        <span className="font-medium">合并冲突</span>
        <span className="text-muted-foreground">·</span>
        <code className="font-mono text-[11px] text-muted-foreground">
          {conflict.taskId}
        </code>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">
          {conflict.conflictFiles.length} 个文件冲突
        </span>
        {error && <span className="text-destructive">· {error}</span>}
      </div>

      <div className="mt-2 space-y-1">
        {conflict.conflictFiles.map((file) => (
          <div key={file} className="flex items-center gap-2">
            <GitMerge className="size-3 shrink-0 text-muted-foreground" />
            <code className="truncate font-mono text-[11px]">{file}</code>
          </div>
        ))}
      </div>

      <div className="mt-2.5 flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          onClick={() => handleResolve('ours')}
          disabled={!!busy}
          className="h-7 px-2.5"
          title="保留主分支版本"
        >
          {busy === 'ours' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ShieldCheck className="size-3.5" />
          )}
          保留我方
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => handleResolve('theirs')}
          disabled={!!busy}
          className="h-7 px-2.5"
          title="保留子 Agent 版本"
        >
          {busy === 'theirs' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <GitMerge className="size-3.5" />
          )}
          保留对方
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={startEdit}
          disabled={!!busy}
          className="h-7 px-2.5"
          title="手动编辑合并内容"
        >
          <Code2 className="size-3.5" />
          手动编辑
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => handleResolve('abandon')}
          disabled={!!busy}
          className="h-7 px-2.5 text-destructive hover:bg-destructive/10"
          title="放弃此任务"
        >
          {busy === 'abandon' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Ban className="size-3.5" />
          )}
          放弃
        </Button>
      </div>

      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent className="max-h-[80vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>手动编辑合并内容</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {conflict.conflictFiles.map((file) => (
              <div key={file}>
                <label className="mb-1 block font-mono text-xs text-muted-foreground">
                  {file}
                </label>
                <textarea
                  className="h-48 w-full resize-y rounded-md border bg-muted/30 p-2 font-mono text-xs"
                  value={editContents[file] ?? ''}
                  onChange={(e) =>
                    setEditContents((prev) => ({ ...prev, [file]: e.target.value }))
                  }
                  placeholder="输入合并后的完整文件内容..."
                />
              </div>
            ))}
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                取消
              </Button>
              <Button size="sm" onClick={submitEdit} disabled={!!busy}>
                {busy === 'edit' ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : null}
                提交合并
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
