'use client'

import { AlertTriangle, Loader2, Plus, X } from 'lucide-react'
import { useEffect, useState } from 'react'

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
import { cn } from '@/lib/utils'
import {
  createMcpServer,
  updateMcpServer,
  type McpServerResponse,
} from '@/lib/api'

interface KvPair {
  key: string
  value: string
}

export function McpServerEditDialog({
  open,
  onOpenChange,
  server,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  server?: McpServerResponse | null
}) {
  const isEdit = !!server

  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'sse' | 'streamable_http'>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState<string[]>([])
  const [envPairs, setEnvPairs] = useState<KvPair[]>([])
  const [url, setUrl] = useState('')
  const [headerPairs, setHeaderPairs] = useState<KvPair[]>([])
  const [trust, setTrust] = useState<'always' | 'ask'>('ask')
  const [enabled, setEnabled] = useState(true)
  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    if (server) {
      setName(server.name)
      setTransport(server.transport)
      setCommand(server.command ?? '')
      setArgs(server.args ?? [])
      setEnvPairs(dictToPairs(server.env))
      setUrl(server.url ?? '')
      setHeaderPairs(dictToPairs(server.headers))
      setTrust(server.trust)
      setEnabled(server.enabled)
      setConfirmed(true)
    } else {
      setName('')
      setTransport('stdio')
      setCommand('')
      setArgs([])
      setEnvPairs([])
      setUrl('')
      setHeaderPairs([])
      setTrust('ask')
      setEnabled(true)
      setConfirmed(false)
    }
    setError(null)
  }, [open, server])

  const addArg = () => setArgs((prev) => [...prev, ''])
  const updateArg = (idx: number, val: string) =>
    setArgs((prev) => prev.map((a, i) => (i === idx ? val : a)))
  const removeArg = (idx: number) =>
    setArgs((prev) => prev.filter((_, i) => i !== idx))

  const addEnv = () => setEnvPairs((prev) => [...prev, { key: '', value: '' }])
  const updateEnv = (idx: number, field: 'key' | 'value', val: string) =>
    setEnvPairs((prev) => prev.map((p, i) => (i === idx ? { ...p, [field]: val } : p)))
  const removeEnv = (idx: number) =>
    setEnvPairs((prev) => prev.filter((_, i) => i !== idx))

  const addHeader = () => setHeaderPairs((prev) => [...prev, { key: '', value: '' }])
  const updateHeader = (idx: number, field: 'key' | 'value', val: string) =>
    setHeaderPairs((prev) => prev.map((p, i) => (i === idx ? { ...p, [field]: val } : p)))
  const removeHeader = (idx: number) =>
    setHeaderPairs((prev) => prev.filter((_, i) => i !== idx))

  const submit = async () => {
    if (submitting) return
    setError(null)

    const trimmedName = name.trim()
    if (!/^[a-z0-9_]+$/.test(trimmedName)) {
      setError('名称只能包含小写字母、数字和下划线 [a-z0-9_]')
      return
    }
    if (transport === 'stdio' && !command.trim()) {
      setError('stdio 传输方式需要填写命令')
      return
    }
    if ((transport === 'sse' || transport === 'streamable_http') && !url.trim()) {
      setError(`${transport === 'sse' ? 'SSE' : 'Streamable HTTP'} 传输方式需要填写 URL`)
      return
    }
    if (!confirmed) {
      setError('请确认你信任此 server')
      return
    }

    const envDict = pairsToDict(envPairs)
    const headerDict = pairsToDict(headerPairs)

    setSubmitting(true)
    try {
      const body = {
        name: trimmedName,
        transport,
        command: transport === 'stdio' ? command.trim() : null,
        args: transport === 'stdio' ? args.map((a) => a.trim()).filter(Boolean) : [],
        env: Object.keys(envDict).length > 0 ? envDict : null,
        url: transport === 'sse' || transport === 'streamable_http' ? url.trim() : null,
        headers: Object.keys(headerDict).length > 0 ? headerDict : null,
        trust,
        enabled,
      }
      if (isEdit && server) {
        await updateMcpServer(server.id, body)
      } else {
        await createMcpServer(body)
      }
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑 MCP Server' : '添加 MCP Server'}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? '修改此 MCP server 的连接配置和信任级别。'
              : '登记一个外部 MCP server，Custom agent 可在运行时连接并使用其工具。'}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          {/* Name */}
          <div className="grid grid-cols-[80px_1fr] items-start gap-3">
            <Label>名称</Label>
            <div>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my_mcp_server"
                className="font-mono text-xs"
                disabled={isEdit}
              />
              <div className="mt-1 text-[10px] text-muted-foreground">
                仅小写字母、数字、下划线。工具名将为 <code className="font-mono">mcp__{name || 'name'}__&lt;tool&gt;</code>
              </div>
            </div>
          </div>

          {/* Transport */}
          <div className="grid grid-cols-[80px_1fr] items-start gap-3">
            <Label>传输方式</Label>
            <div className="flex gap-2">
              <label
                className={cn(
                  'flex cursor-pointer items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition',
                  transport === 'stdio' && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  checked={transport === 'stdio'}
                  onChange={() => setTransport('stdio')}
                  className="accent-primary"
                />
                stdio
              </label>
              <label
                className={cn(
                  'flex cursor-pointer items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition',
                  transport === 'sse' && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  checked={transport === 'sse'}
                  onChange={() => setTransport('sse')}
                  className="accent-primary"
                />
                SSE
              </label>
              <label
                className={cn(
                  'flex cursor-pointer items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition',
                  transport === 'streamable_http' && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  checked={transport === 'streamable_http'}
                  onChange={() => setTransport('streamable_http')}
                  className="accent-primary"
                />
                Streamable HTTP
              </label>
            </div>
          </div>

          {/* stdio fields */}
          {transport === 'stdio' && (
            <>
              <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                <Label required>命令</Label>
                <Input
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                  className="font-mono text-xs"
                />
              </div>

              <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                <Label>参数</Label>
                <div className="space-y-1.5">
                  {args.map((arg, idx) => (
                    <div key={idx} className="flex items-center gap-1.5">
                      <Input
                        value={arg}
                        onChange={(e) => updateArg(idx, e.target.value)}
                        placeholder="-y @modelcontextprotocol/server-filesystem"
                        className="flex-1 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => removeArg(idx)}
                        className="shrink-0 rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={addArg}
                    className="flex items-center gap-1 text-[10px] text-primary transition hover:opacity-80"
                  >
                    <Plus className="size-3" />
                    添加参数
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                <Label>环境变量</Label>
                <div className="space-y-1.5">
                  {envPairs.map((pair, idx) => (
                    <div key={idx} className="flex items-center gap-1.5">
                      <Input
                        value={pair.key}
                        onChange={(e) => updateEnv(idx, 'key', e.target.value)}
                        placeholder="API_KEY"
                        className="flex-1 font-mono text-xs"
                      />
                      <Input
                        value={pair.value}
                        onChange={(e) => updateEnv(idx, 'value', e.target.value)}
                        placeholder="${ENV_VAR} 或直接填值"
                        className="flex-1 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => removeEnv(idx)}
                        className="shrink-0 rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={addEnv}
                    className="flex items-center gap-1 text-[10px] text-primary transition hover:opacity-80"
                  >
                    <Plus className="size-3" />
                    添加变量
                  </button>
                  <div className="text-[10px] text-muted-foreground">
                    值中写 <code className="font-mono">{'${ENV_NAME}'}</code> 可引用 <code className="font-mono">.env.local</code> 中的变量
                  </div>
                </div>
              </div>
            </>
          )}

          {/* sse / streamable_http fields */}
          {(transport === 'sse' || transport === 'streamable_http') && (
            <>
              <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                <Label required>URL</Label>
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://mcp.example.com/sse  或  https://mcp.example.com/mcp"
                  className="font-mono text-xs"
                />
              </div>

              <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                <Label>Headers</Label>
                <div className="space-y-1.5">
                  {headerPairs.map((pair, idx) => (
                    <div key={idx} className="flex items-center gap-1.5">
                      <Input
                        value={pair.key}
                        onChange={(e) => updateHeader(idx, 'key', e.target.value)}
                        placeholder="Authorization"
                        className="flex-1 font-mono text-xs"
                      />
                      <Input
                        value={pair.value}
                        onChange={(e) => updateHeader(idx, 'value', e.target.value)}
                        placeholder="Bearer ${API_TOKEN}"
                        className="flex-1 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => removeHeader(idx)}
                        className="shrink-0 rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={addHeader}
                    className="flex items-center gap-1 text-[10px] text-primary transition hover:opacity-80"
                  >
                    <Plus className="size-3" />
                    添加 Header
                  </button>
                </div>
              </div>
            </>
          )}

          {/* Trust */}
          <div className="grid grid-cols-[80px_1fr] items-start gap-3">
            <Label>信任级别</Label>
            <div className="flex flex-col gap-1.5">
              <label
                className={cn(
                  'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition',
                  trust === 'ask' && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  checked={trust === 'ask'}
                  onChange={() => setTrust('ask')}
                  className="mt-0.5 accent-primary"
                />
                <div className="min-w-0">
                  <div className="text-xs font-medium">每次会话首次询问 (ask)</div>
                  <div className="mt-0.5 text-[10px] text-muted-foreground">
                    该 server 的每个工具在当前会话内首次调用时弹审批，批准后该会话内该工具免再问。
                  </div>
                </div>
              </label>
              <label
                className={cn(
                  'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition',
                  trust === 'always' && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  checked={trust === 'always'}
                  onChange={() => setTrust('always')}
                  className="mt-0.5 accent-primary"
                />
                <div className="min-w-0">
                  <div className="text-xs font-medium">始终放行 (always)</div>
                  <div className="mt-0.5 text-[10px] text-muted-foreground">
                    该 server 的所有工具直接执行，不弹审批。仅在你完全信任此 server 时选择。
                  </div>
                </div>
              </label>
            </div>
          </div>

          {/* Enabled */}
          <div className="grid grid-cols-[80px_1fr] items-start gap-3">
            <Label>启用</Label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="accent-primary"
              />
              <span className="text-xs">启用此 server（关闭后 agent 连接时跳过）</span>
            </label>
          </div>

          {/* Security warning */}
          <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
              <div className="min-w-0 space-y-1">
                <div className="text-xs font-medium text-warning">安全提示</div>
                <div className="text-[10px] leading-relaxed text-muted-foreground">
                  外部 MCP server 运行任意代码或访问外部网络，不在 AChat 的沙箱保证范围内。
                  {transport === 'stdio' && ' stdio 模式会启动子进程并传入你配置的环境变量。'}
                  仅在你信任此 server 来源时启用。
                </div>
                <label className="flex cursor-pointer items-center gap-1.5 pt-1">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                    className="accent-primary"
                  />
                  <span className="text-[11px] font-medium">我信任此 server</span>
                </label>
              </div>
            </div>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={submitting || !confirmed}>
            {submitting ? (
              <Loader2 className="mr-1.5 size-3.5 animate-spin" />
            ) : null}
            {isEdit ? '保存' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Label({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <div className="pt-2 text-xs text-muted-foreground">
      {children}
      {required && <span className="ml-0.5 text-destructive">*</span>}
    </div>
  )
}

function dictToPairs(dict: Record<string, string> | null): KvPair[] {
  if (!dict) return []
  return Object.entries(dict).map(([key, value]) => ({ key, value }))
}

function pairsToDict(pairs: KvPair[]): Record<string, string> {
  const dict: Record<string, string> = {}
  for (const { key, value } of pairs) {
    const k = key.trim()
    if (k) dict[k] = value
  }
  return dict
}
