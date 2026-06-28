'use client'

import { ChevronRight, Folder, HardDrive, Home, Loader2 } from 'lucide-react'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { listDirectory } from '@/lib/api'
import { cn } from '@/lib/utils'

interface DirEntry {
  name: string
  isDirectory: boolean
  path?: string
}

const DRIVES_SENTINEL = '__drives__'
const DRIVES_LABEL = '此电脑'

/**
 * DirPickerDialog —— 服务端 listdir 驱动的目录选择器。
 *
 * 不能直接选「文件」，只能在目录树里导航 + 用「选择此目录」按钮提交当前路径。
 * 起点为用户 home（后端默认）；可手动点击「上一级」/ 面包屑跳转。
 */
export function DirPickerDialog({
  open,
  onOpenChange,
  onSelect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (absolutePath: string) => void
}) {
  const [currentPath, setCurrentPath] = useState<string>('')
  const [parent, setParent] = useState<string | null>(null)
  const [entries, setEntries] = useState<DirEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const navigate = useCallback(async (target?: string) => {
    setLoading(true)
    setError(null)
    try {
      const result = await listDirectory(target)
      setCurrentPath(result.path)
      setParent(result.parent)
      setEntries(result.entries)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  // 打开时初始化（家目录，由后端默认）；关闭时不清理（下次打开继承）。
  useEffect(() => {
    if (open && !currentPath) {
      void navigate()
    }
  }, [open, currentPath, navigate])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>选择本地目录</DialogTitle>
          <DialogDescription>
            导航到你希望 Agent 工作的目录，然后点「选择此目录」。
          </DialogDescription>
        </DialogHeader>

        {/* 路径栏 */}
        <div className="flex items-center gap-2 rounded-md border bg-muted/40 px-2 py-1.5">
          <Folder className="size-4 shrink-0 text-muted-foreground" />
          <code className="min-w-0 flex-1 truncate font-mono text-xs">
            {currentPath === DRIVES_SENTINEL ? DRIVES_LABEL : currentPath || '加载中...'}
          </code>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void navigate(DRIVES_SENTINEL)}
            disabled={loading}
            title="此电脑（盘符列表）"
            className="shrink-0"
          >
            <HardDrive className="size-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void navigate()}
            disabled={loading}
            title="回到 home"
            className="shrink-0"
          >
            <Home className="size-3.5" />
          </Button>
        </div>

        {/* 上一级 / 内容 */}
        <ScrollArea className="h-72 w-full min-w-0 overflow-hidden rounded-md border">
          <div className="w-full divide-y">
            {parent && (
              <button
                type="button"
                onClick={() => void navigate(parent)}
                disabled={loading}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent disabled:opacity-50"
              >
                <ChevronRight className="size-4 rotate-180 text-muted-foreground" />
                <span className="text-muted-foreground">
                  .. ({parent === DRIVES_SENTINEL ? DRIVES_LABEL : '上一级'})
                </span>
              </button>
            )}
            {loading ? (
              <div className="flex items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                加载中...
              </div>
            ) : entries.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                (无子目录)
              </div>
            ) : (
              entries.map((e) => {
                const childPath = e.path ?? joinPath(currentPath, e.name)
                const isDrive = currentPath === DRIVES_SENTINEL
                return (
                  <button
                    key={e.name}
                    type="button"
                    onClick={() => void navigate(childPath)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent"
                  >
                    {isDrive ? (
                      <HardDrive className="size-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <Folder className="size-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{e.name}</span>
                    <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/50" />
                  </button>
                )
              })
            )}
          </div>
        </ScrollArea>

        {error && (
          <div className={cn(
            'rounded-md border px-3 py-2 text-xs',
            'border-destructive/30 bg-destructive/10 text-destructive',
          )}>
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => {
              if (currentPath && currentPath !== DRIVES_SENTINEL) {
                onSelect(currentPath)
                onOpenChange(false)
              }
            }}
            disabled={!currentPath || currentPath === DRIVES_SENTINEL || loading}
          >
            选择此目录
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 浏览器端简易 path join（保留 OS 风格的分隔符 —— 后端已给绝对路径，前端按需拼接子目录）。 */
function joinPath(base: string, segment: string): string {
  if (!base) return segment
  // 兼容 Windows 反斜杠路径
  const sep = base.includes('\\') && !base.includes('/') ? '\\' : '/'
  const trimmed = base.endsWith(sep) ? base.slice(0, -1) : base
  return `${trimmed}${sep}${segment}`
}
