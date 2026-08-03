'use client'

import { Cable, CheckCircle2, Edit2, TestTube, Trash2, XCircle } from 'lucide-react'
import { useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import type { McpServerResponse, McpTestResult } from '@/lib/api'

interface McpDetailDialogProps {
  server: McpServerResponse
  open: boolean
  onOpenChange: (open: boolean) => void
  testResult?: McpTestResult
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
  onToggleEnabled: () => void
}

export function McpDetailDialog({
  server,
  open,
  onOpenChange,
  testResult,
  onTest,
  onEdit,
  onDelete,
  onToggleEnabled,
}: McpDetailDialogProps) {
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [testing, setTesting] = useState(false)

  const handleTest = () => {
    setTesting(true)
    onTest()
    // Reset testing state after a timeout (parent will update actual state)
    setTimeout(() => setTesting(false), 1000)
  }

  const handleDelete = () => {
    onDelete()
    setDeleteConfirmOpen(false)
    onOpenChange(false)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader className="shrink-0">
            <div className="flex items-start gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-500">
                <Cable className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <DialogTitle className="text-lg">{server.name}</DialogTitle>
                  <Badge variant={server.enabled ? 'default' : 'secondary'} className="text-[10px]">
                    {server.enabled ? '已启用' : '已禁用'}
                  </Badge>
                </div>
                <DialogDescription className="mt-1 flex items-center gap-2">
                  <Badge variant="outline" className="text-[10px]">
                    {server.transport}
                  </Badge>
                  <Badge
                    variant={server.trust === 'always' ? 'default' : 'secondary'}
                    className="text-[10px]"
                  >
                    {server.trust === 'always' ? '始终信任' : '询问'}
                  </Badge>
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <ScrollArea className="flex-1 min-h-0 my-4">
            <div className="space-y-6 pr-4">
              {/* Enable Toggle */}
              <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-4 py-3">
                <div>
                  <h4 className="text-sm font-medium">启用状态</h4>
                  <p className="text-xs text-muted-foreground">
                    {server.enabled ? 'Agent 可以调用此 MCP Server' : 'Agent 暂时无法调用此 MCP Server'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onToggleEnabled}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    server.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block size-4 rounded-full bg-white transition-transform ${
                      server.enabled ? 'translate-x-5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>

              {/* Connection Config */}
              <div>
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  连接配置
                </h4>
                <div className="mt-2 space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
                  {server.transport === 'stdio' ? (
                    <>
                      <div className="flex gap-2">
                        <span className="text-muted-foreground min-w-[60px]">命令:</span>
                        <code className="font-mono text-xs">{server.command}</code>
                      </div>
                      {server.args && server.args.length > 0 && (
                        <div className="flex gap-2">
                          <span className="text-muted-foreground min-w-[60px]">参数:</span>
                          <code className="font-mono text-xs">{server.args.join(' ')}</code>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="flex gap-2">
                        <span className="text-muted-foreground min-w-[60px]">URL:</span>
                        <code className="font-mono text-xs break-all">{server.url}</code>
                      </div>
                    </>
                  )}
                  {server.env && Object.keys(server.env).length > 0 && (
                    <div className="flex gap-2">
                      <span className="text-muted-foreground min-w-[60px]">环境变量:</span>
                      <div className="space-y-1">
                        {Object.entries(server.env).map(([key, value]) => (
                          <div key={key} className="font-mono text-xs">
                            {key}={value}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Test Result */}
              {testResult && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    连接测试
                  </h4>
                  <div
                    className={`mt-2 rounded-lg border p-3 ${
                      testResult.ok
                        ? 'border-success/30 bg-success/5'
                        : 'border-destructive/30 bg-destructive/5'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {testResult.ok ? (
                        <CheckCircle2 className="size-4 text-success" />
                      ) : (
                        <XCircle className="size-4 text-destructive" />
                      )}
                      <span className={`text-sm font-medium ${testResult.ok ? 'text-success' : 'text-destructive'}`}>
                        {testResult.ok ? '连接成功' : '连接失败'}
                      </span>
                    </div>
                    {testResult.ok && testResult.tools.length > 0 && (
                      <div className="mt-3 space-y-2">
                        <p className="text-xs text-muted-foreground">
                          发现 {testResult.tools.length} 个工具:
                        </p>
                        <div className="space-y-1.5">
                          {testResult.tools.map((tool) => (
                            <div
                              key={tool.name}
                              className="rounded border bg-background/50 px-2.5 py-1.5 text-xs"
                            >
                              <code className="font-mono text-primary">{tool.name}</code>
                              {tool.description && (
                                <p className="mt-0.5 text-muted-foreground line-clamp-2">
                                  {tool.description}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {!testResult.ok && testResult.error && (
                      <p className="mt-2 text-xs text-destructive">{testResult.error}</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>

          <DialogFooter className="shrink-0 gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
            <Button variant="outline" onClick={handleTest} disabled={testing} className="gap-1.5">
              {testing ? (
                <div className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <TestTube className="size-4" />
              )}
              测试连接
            </Button>
            <Button variant="outline" onClick={onEdit} className="gap-1.5">
              <Edit2 className="size-4" />
              编辑
            </Button>
            <Button
              variant="destructive"
              onClick={() => setDeleteConfirmOpen(true)}
              className="gap-1.5"
            >
              <Trash2 className="size-4" />
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>删除 MCP Server</DialogTitle>
            <DialogDescription>
              确定要删除「{server.name}」吗？所有引用此 Server 的 Agent 会自动移除关联。此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDeleteConfirmOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
