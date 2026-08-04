'use client'

import {
  Cable,
  CheckCircle2,
  Edit2,
  Globe,
  Terminal,
  TestTube,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react'
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
import { cn } from '@/lib/utils'
import type { McpServerResponse, McpTestResult } from '@/lib/api'

// ─── Transport Meta ──────────────────────────────────────────────

interface TransportMeta {
  label: string
  icon: typeof Terminal
  iconBg: string
  iconText: string
}

function getTransportMeta(transport: string): TransportMeta {
  switch (transport) {
    case 'stdio':
      return { label: 'stdio', icon: Terminal, iconBg: 'bg-violet-500/10', iconText: 'text-violet-500' }
    case 'sse':
      return { label: 'SSE', icon: Globe, iconBg: 'bg-cyan-500/10', iconText: 'text-cyan-500' }
    case 'streamable_http':
      return { label: 'Streamable HTTP', icon: Globe, iconBg: 'bg-cyan-500/10', iconText: 'text-cyan-500' }
    default:
      return { label: transport, icon: Cable, iconBg: 'bg-blue-500/10', iconText: 'text-blue-500' }
  }
}

// ─── Component ───────────────────────────────────────────────────

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
    setTimeout(() => setTesting(false), 1000)
  }

  const handleDelete = () => {
    onDelete()
    setDeleteConfirmOpen(false)
    onOpenChange(false)
  }

  const transportMeta = getTransportMeta(server.transport)
  const TransportIcon = transportMeta.icon

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex max-h-[80vh] flex-col overflow-hidden p-0 sm:max-w-xl">
          {/* Header with gradient accent */}
          <DialogHeader className="shrink-0 border-b px-6 pb-4 pt-6">
            <div className="flex items-start gap-3">
              <div
                className={cn(
                  'flex size-11 shrink-0 items-center justify-center rounded-xl',
                  transportMeta.iconBg,
                )}
              >
                <TransportIcon className={cn('size-5.5', transportMeta.iconText)} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <DialogTitle className="text-lg tracking-tight">{server.name}</DialogTitle>
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium',
                      server.enabled
                        ? 'bg-success/10 text-success'
                        : 'bg-muted text-muted-foreground',
                    )}
                  >
                    <span
                      className={cn(
                        'size-1.5 rounded-full',
                        server.enabled ? 'bg-success' : 'bg-muted-foreground/40',
                      )}
                    />
                    {server.enabled ? '已启用' : '已禁用'}
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium',
                      transportMeta.iconBg,
                      transportMeta.iconText,
                    )}
                  >
                    {transportMeta.label}
                  </span>
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium',
                      server.trust === 'always'
                        ? 'bg-warning/10 text-warning'
                        : 'bg-muted text-muted-foreground',
                    )}
                  >
                    {server.trust === 'always' && <Zap className="size-2.5" />}
                    {server.trust === 'always' ? '始终信任' : '每次询问'}
                  </span>
                </div>
              </div>
            </div>
          </DialogHeader>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-5 px-6 py-5">
              {/* Enable Toggle */}
              <div className="flex items-center justify-between rounded-xl border bg-muted/30 px-4 py-3">
                <div>
                  <h4 className="text-sm font-medium">启用状态</h4>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {server.enabled ? 'Agent 可以调用此 MCP Server' : 'Agent 暂时无法调用此 MCP Server'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onToggleEnabled}
                  className={cn(
                    'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                    server.enabled ? 'bg-primary' : 'bg-muted-foreground/30',
                  )}
                >
                  <span
                    className={cn(
                      'inline-block size-4 rounded-full bg-white transition-transform',
                      server.enabled ? 'translate-x-5' : 'translate-x-0.5',
                    )}
                  />
                </button>
              </div>

              {/* Connection Config */}
              <div>
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  连接配置
                </h4>
                <div className="mt-2 space-y-2.5 rounded-xl border bg-muted/20 p-4">
                  {server.transport === 'stdio' ? (
                    <>
                      <ConfigRow label="命令" value={server.command} mono />
                      {server.args && server.args.length > 0 && (
                        <ConfigRow label="参数" value={server.args.join(' ')} mono />
                      )}
                    </>
                  ) : (
                    <ConfigRow label="URL" value={server.url} mono />
                  )}
                  {server.env && Object.keys(server.env).length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">环境变量</span>
                      <div className="mt-1 space-y-1">
                        {Object.entries(server.env).map(([key, value]) => (
                          <div key={key} className="font-mono text-xs">
                            <span className="text-muted-foreground">{key}</span>
                            <span className="text-border"> = </span>
                            <span className="text-foreground">{value}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {server.headers && Object.keys(server.headers).length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">Headers</span>
                      <div className="mt-1 space-y-1">
                        {Object.entries(server.headers).map(([key, value]) => (
                          <div key={key} className="font-mono text-xs">
                            <span className="text-muted-foreground">{key}</span>
                            <span className="text-border"> : </span>
                            <span className="text-foreground">{value}</span>
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
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    连接测试
                  </h4>
                  <div
                    className={cn(
                      'mt-2 rounded-xl border p-4',
                      testResult.ok
                        ? 'border-success/30 bg-success/5'
                        : 'border-destructive/30 bg-destructive/5',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      {testResult.ok ? (
                        <CheckCircle2 className="size-4 text-success" />
                      ) : (
                        <XCircle className="size-4 text-destructive" />
                      )}
                      <span
                        className={cn(
                          'text-sm font-medium',
                          testResult.ok ? 'text-success' : 'text-destructive',
                        )}
                      >
                        {testResult.ok ? '连接成功' : '连接失败'}
                      </span>
                    </div>
                    {testResult.ok && testResult.tools.length > 0 && (
                      <div className="mt-3 space-y-1.5">
                        <p className="text-xs text-muted-foreground">
                          发现 {testResult.tools.length} 个工具
                        </p>
                        <div className="space-y-1">
                          {testResult.tools.map((tool) => (
                            <div
                              key={tool.name}
                              className="rounded-lg border bg-background/60 px-3 py-1.5"
                            >
                              <code className="font-mono text-xs text-primary">{tool.name}</code>
                              {tool.description && (
                                <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
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

          {/* Footer */}
          <div className="shrink-0 border-t px-6 py-4">
            <div className="flex items-center justify-between">
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteConfirmOpen(true)}
                className="gap-1.5"
              >
                <Trash2 className="size-3.5" />
                删除
              </Button>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                  关闭
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleTest}
                  disabled={testing}
                  className="gap-1.5"
                >
                  {testing ? (
                    <div className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  ) : (
                    <TestTube className="size-3.5" />
                  )}
                  测试连接
                </Button>
                <Button variant="outline" size="sm" onClick={onEdit} className="gap-1.5">
                  <Edit2 className="size-3.5" />
                  编辑
                </Button>
              </div>
            </div>
          </div>
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

// ─── Config Row ──────────────────────────────────────────────────

function ConfigRow({
  label,
  value,
  mono,
}: {
  label: string
  value?: string | null
  mono?: boolean
}) {
  if (!value) return null
  return (
    <div className="flex gap-3">
      <span className="w-12 shrink-0 text-xs text-muted-foreground">{label}</span>
      <code
        className={cn(
          'min-w-0 flex-1 break-all text-xs',
          mono && 'font-mono',
        )}
      >
        {value}
      </code>
    </div>
  )
}
