'use client'

import { CheckCircle2, Loader2, Pencil, Plug, Plus, Search, Trash2, X, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { McpServerEditDialog } from '@/components/mcp-server-edit-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  deleteMcpServer,
  fetchMcpServers,
  testMcpServer,
  updateMcpServer,
  type McpServerResponse,
  type McpTestResult,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'

export function McpServerLibrary() {
  const [servers, setServers] = useState<McpServerResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<McpServerResponse | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, McpTestResult | undefined>>({})
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set())

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setServers(await fetchMcpServers())
    } catch (err) {
      console.error('[McpServerLibrary] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useGuideSideEffectRefresh('mcp', () => { void refresh() })

  const openCreate = () => {
    setEditingServer(null)
    setEditOpen(true)
  }

  const openEdit = (server: McpServerResponse) => {
    setEditingServer(server)
    setEditOpen(true)
  }

  const handleEditOpenChange = (open: boolean) => {
    setEditOpen(open)
    if (!open) {
      setEditingServer(null)
      void refresh()
    }
  }

  const handleToggleEnabled = async (server: McpServerResponse) => {
    try {
      const updated = await updateMcpServer(server.id, { enabled: !server.enabled })
      setServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
    } catch (err) {
      console.error('[McpServerLibrary] toggle enabled failed', err)
    }
  }

  const handleTest = async (server: McpServerResponse) => {
    setTestingIds((prev) => new Set(prev).add(server.id))
    setTestResult((prev) => ({ ...prev, [server.id]: undefined }))
    try {
      const result = await testMcpServer(server.id)
      setTestResult((prev) => ({ ...prev, [server.id]: result }))
    } catch (err) {
      setTestResult((prev) => ({
        ...prev,
        [server.id]: { ok: false, tools: [], error: err instanceof Error ? err.message : String(err) },
      }))
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev)
        next.delete(server.id)
        return next
      })
    }
  }

  const confirmDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteMcpServer(deleteTargetId)
      setServers((prev) => prev.filter((s) => s.id !== deleteTargetId))
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[McpServerLibrary] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return servers
    return servers.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.transport.toLowerCase().includes(q) ||
        (s.command ?? '').toLowerCase().includes(q) ||
        (s.url ?? '').toLowerCase().includes(q),
    )
  }, [servers, query])

  const deleteTarget = deleteTargetId ? servers.find((s) => s.id === deleteTargetId) : null

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header: search + add */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索 MCP Server"
              className="h-8 pl-7 text-xs"
            />
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 text-xs"
            onClick={openCreate}
          >
            <Plus className="size-3.5" />
            添加
          </Button>
        </div>
      </div>

      {/* Server list */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1 p-2">
          {loading && servers.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
              <Loader2 className="mr-2 size-3 animate-spin" /> 加载中
            </div>
          ) : servers.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <Plug className="size-8 opacity-30" />
              <span className="text-xs">还没有 MCP Server</span>
              <span className="text-[10px]">点击「添加」登记一个外部 MCP server</span>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <Search className="size-7 opacity-30" />
              <span className="text-xs">没有匹配「{query}」的 Server</span>
            </div>
          ) : (
            filtered.map((server) => (
              <McpServerCard
                key={server.id}
                server={server}
                onEdit={() => openEdit(server)}
                onDelete={() => setDeleteTargetId(server.id)}
                onToggleEnabled={() => void handleToggleEnabled(server)}
                onTest={() => void handleTest(server)}
                testing={testingIds.has(server.id)}
                testResult={testResult[server.id]}
              />
            ))
          )}
        </div>
      </ScrollArea>

      <McpServerEditDialog
        open={editOpen}
        onOpenChange={handleEditOpenChange}
        server={editingServer}
      />

      {/* Delete confirmation */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setDeleteTargetId(null)}
        >
          <div
            className="mx-4 w-full max-w-sm rounded-lg border bg-card p-5 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold">删除 MCP Server</h3>
            <p className="mt-1.5 text-xs text-muted-foreground">
              确定要删除「{deleteTarget.name}」吗？所有引用此 server 的 Agent 会自动移除关联。此操作不可恢复。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDeleteTargetId(null)}>
                取消
              </Button>
              <Button
                className="bg-destructive hover:bg-destructive/90"
                size="sm"
                onClick={() => void confirmDelete()}
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

function McpServerCard({
  server,
  onEdit,
  onDelete,
  onToggleEnabled,
  onTest,
  testing,
  testResult,
}: {
  server: McpServerResponse
  onEdit: () => void
  onDelete: () => void
  onToggleEnabled: () => void
  onTest: () => void
  testing: boolean
  testResult?: McpTestResult
}) {
  return (
    <div className="group rounded-md border border-transparent px-2 py-2 transition hover:border-border/60 hover:bg-accent">
      <div className="flex items-start gap-2">
        <Plug className="mt-0.5 size-4 shrink-0 text-muted-foreground" />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 truncate text-xs font-medium" title={server.name}>
              {server.name}
            </span>
            <span
              className={cn(
                'shrink-0 rounded px-1 py-0.5 font-mono text-[9px]',
                server.transport === 'stdio'
                  ? 'bg-blue-500/10 text-blue-600'
                  : 'bg-purple-500/10 text-purple-600',
              )}
            >
              {server.transport}
            </span>
            <span
              className={cn(
                'shrink-0 rounded px-1 py-0.5 font-mono text-[9px]',
                server.trust === 'always'
                  ? 'bg-success/10 text-success'
                  : 'bg-warning/10 text-warning',
              )}
            >
              {server.trust}
            </span>
          </div>

          <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
            {server.transport === 'stdio'
              ? `${server.command ?? ''} ${(server.args ?? []).join(' ')}`
              : server.url ?? ''}
          </div>

          {/* Enabled toggle */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onToggleEnabled()
            }}
            className={cn(
              'mt-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition',
              server.enabled
                ? 'bg-success/10 text-success hover:bg-success/20'
                : 'bg-muted text-muted-foreground hover:bg-muted/80',
            )}
          >
            <span className={cn('size-1.5 rounded-full', server.enabled ? 'bg-success' : 'bg-muted-foreground')} />
            {server.enabled ? '已启用' : '已禁用'}
          </button>

          {/* Test result */}
          {testing && (
            <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              正在测试连接...
            </div>
          )}
          {!testing && testResult && (
            <div
              className={cn(
                'mt-1.5 rounded-md border px-2 py-1.5 text-[10px]',
                testResult.ok
                  ? 'border-success/30 bg-success/5 text-success'
                  : 'border-destructive/30 bg-destructive/5 text-destructive',
              )}
            >
              <div className="flex items-center gap-1 font-medium">
                {testResult.ok ? (
                  <>
                    <CheckCircle2 className="size-3" />
                    连接成功 · 发现 {testResult.tools.length} 个工具
                  </>
                ) : (
                  <>
                    <XCircle className="size-3" />
                    连接失败
                  </>
                )}
              </div>
              {testResult.ok && testResult.tools.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {testResult.tools.slice(0, 5).map((tool) => (
                    <div key={tool.name} className="flex items-start gap-1">
                      <code className="shrink-0 font-mono text-[9px] text-muted-foreground">
                        {tool.name}
                      </code>
                      {tool.description && (
                        <span className="line-clamp-1 text-[9px] text-muted-foreground">
                          — {tool.description}
                        </span>
                      )}
                    </div>
                  ))}
                  {testResult.tools.length > 5 && (
                    <div className="text-[9px] text-muted-foreground">
                      …还有 {testResult.tools.length - 5} 个
                    </div>
                  )}
                </div>
              )}
              {!testResult.ok && testResult.error && (
                <div className="mt-0.5 break-all text-[9px]">{testResult.error}</div>
              )}
            </div>
          )}
        </div>

        {/* Hover actions */}
        <div className="flex shrink-0 self-center gap-0.5 opacity-0 transition group-hover:opacity-100">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onTest()
            }}
            disabled={testing}
            title="测试连接"
            className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:opacity-50"
          >
            {testing ? <Loader2 className="size-3.5 animate-spin" /> : <Plug className="size-3.5" />}
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onEdit()
            }}
            title="编辑"
            className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground"
          >
            <Pencil className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            title="删除"
            className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}
