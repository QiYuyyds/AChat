'use client'

import { AlertTriangle, FolderSearch } from 'lucide-react'
import { useEffect, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { DirPickerDialog } from '@/components/dir-picker-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { createConversation, getServerPlatform, type ServerPlatform } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAgentList, useAppStore } from '@/stores/app-store'

type WorkspaceMode = 'sandbox' | 'local'

export function NewConversationDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const agents = useAgentList()
  const upsertConversation = useAppStore((s) => s.upsertConversation)
  const setActive = useAppStore((s) => s.setActiveConversation)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('sandbox')
  const [boundPath, setBoundPath] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [platform, setPlatform] = useState<ServerPlatform | null>(null)

  // 拉一次服务器平台，决定 boundPath placeholder 文案；失败不阻塞 UI（fallback posix）
  useEffect(() => {
    getServerPlatform()
      .then((p) => setPlatform(p))
      .catch(() => setPlatform('posix'))
  }, [])

  const boundPathPlaceholder =
    platform === 'windows' ? 'D:\\projects\\foo' : '/Users/me/projects/foo'

  const mode: 'single' | 'group' = selected.size > 1 ? 'group' : 'single'

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const reset = () => {
    setSelected(new Set())
    setWorkspaceMode('sandbox')
    setBoundPath('')
    setError(null)
  }

  const submit = async () => {
    if (selected.size === 0 || creating) return
    setError(null)

    if (workspaceMode === 'local' && !boundPath.trim()) {
      setError('选了「本地目录」就要填路径')
      return
    }

    setCreating(true)
    try {
      const conv = await createConversation({
        mode,
        agentIds: Array.from(selected),
        boundPath: workspaceMode === 'local' ? boundPath.trim() : undefined,
      })
      upsertConversation(conv)
      setActive(conv.id)
      reset()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>新建对话</DialogTitle>
          <DialogDescription>
            选择 1 个 Agent 创建单聊，选择 2 个或更多创建群聊
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 py-2">
          {agents.length === 0 ? (
            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
              暂无可用 Agent
              <div className="mt-1 text-xs">点 Sidebar 里的「Agents」入口创建一个，或重启应用</div>
            </div>
          ) : (
            agents.map((a) => {
              const isSelected = selected.has(a.id)
              return (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => toggle(a.id)}
                  className={cn(
                    'flex w-full items-start gap-3 rounded-md border p-3 text-left transition hover:border-foreground/30',
                    isSelected && 'border-primary bg-primary/5',
                  )}
                >
                  <AgentAvatar agent={a} size="lg" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{a.name}</span>
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                        {a.adapterName}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                      {a.description}
                    </p>
                  </div>
                </button>
              )
            })
          )}
        </div>

        {/* 工作目录 */}
        <div className="space-y-2 border-t pt-3">
          <div className="text-xs font-medium text-muted-foreground">工作目录</div>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30">
            <input
              type="radio"
              checked={workspaceMode === 'sandbox'}
              onChange={() => setWorkspaceMode('sandbox')}
              className="mt-0.5 accent-primary"
            />
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium">沙箱隔离</div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                工作目录在 <code className="font-mono">.agenthub-data/</code> 内部，不接触你的真实代码
              </div>
            </div>
          </label>
          <label
            className={cn(
              'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
              workspaceMode === 'local' && 'border-amber-300 bg-amber-50/40 dark:border-amber-900/50 dark:bg-amber-950/20',
            )}
          >
            <input
              type="radio"
              checked={workspaceMode === 'local'}
              onChange={() => setWorkspaceMode('local')}
              className="mt-0.5 accent-primary"
            />
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="text-xs font-medium">绑定本地目录</div>
              {workspaceMode === 'local' && (
                <>
                  <div className="flex gap-2">
                    <Input
                      value={boundPath}
                      onChange={(e) => setBoundPath(e.target.value)}
                      placeholder={boundPathPlaceholder}
                      className="flex-1 font-mono text-xs"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setPickerOpen(true)}
                    >
                      <FolderSearch className="mr-1 size-3.5" />
                      浏览
                    </Button>
                  </div>
                  <div className="flex items-start gap-1.5 text-[10px] text-amber-700 dark:text-amber-300">
                    <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                    <span>Agent 将能读写此目录中的真实文件。请确保已 git 备份。</span>
                  </div>
                </>
              )}
            </div>
          </label>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-200">
            {error}
          </div>
        )}

        <DialogFooter>
          <div className="mr-auto text-xs text-muted-foreground">
            已选 {selected.size} 位 · 将创建{mode === 'single' ? '单聊' : '群聊'}
          </div>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={selected.size === 0 || creating}>
            {creating ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>

      <DirPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        onSelect={(p) => setBoundPath(p)}
      />
    </Dialog>
  )
}
