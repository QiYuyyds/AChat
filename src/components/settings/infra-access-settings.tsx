'use client'

/**
 * 基础设施接入（rag-infra-config）— 桌面模式设置区的「基础设施」Tab。
 *
 * - 状态徽标：轮询 /api/infra/status，展示各服务 connected/degraded/disabled
 * - 表单：Milvus / Neo4j 端点与凭证（密码掩码回显，空值/掩码回写 = 未修改）
 * - 连接测试：以表单当前值（未持久化）实测，成功显示时延、失败显示分类原因
 * - 保存：PUT /api/infra/config（仅桌面模式可写），保存后提示重启生效
 *
 * 生效语义：基础设施在启动期装配 —— 保存后需重启，无热重连。
 */

import { AlertTriangle, Loader2, PlugZap, Server } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  INFRA_PASSWORD_MASK,
  fetchInfraConfig,
  fetchInfraStatus,
  testInfraConnection,
  updateInfraConfig,
  type InfraStatusResponse,
  type InfraTestResult,
} from '@/lib/api'
import { cn } from '@/lib/utils'

const STATUS_BADGES = ['milvus', 'neo4j', 'kafka', 'embedding'] as const

const STATUS_LABEL: Record<string, string> = {
  connected: '已接入',
  degraded: '降级中',
  disabled: '未启用',
}

const ERROR_KIND_LABEL: Record<string, string> = {
  network: '网络不可达 / 端口错误',
  auth: '认证失败',
  protocol: '协议错误',
}

interface InfraForm {
  milvusHost: string
  milvusPort: string
  neo4jUri: string
  neo4jUser: string
  neo4jPassword: string
  enableGraph: 'env' | 'on' | 'off'
}

const EMPTY_FORM: InfraForm = {
  milvusHost: '',
  milvusPort: '',
  neo4jUri: '',
  neo4jUser: '',
  neo4jPassword: '',
  enableGraph: 'env',
}

function configToForm(config: {
  milvusHost: string | null
  milvusPort: number | null
  neo4jUri: string | null
  neo4jUser: string | null
  neo4jPassword: string
  enableGraph: boolean | null
}): InfraForm {
  return {
    milvusHost: config.milvusHost ?? '',
    milvusPort: config.milvusPort != null ? String(config.milvusPort) : '',
    neo4jUri: config.neo4jUri ?? '',
    neo4jUser: config.neo4jUser ?? '',
    neo4jPassword: config.neo4jPassword ?? '',
    enableGraph: config.enableGraph == null ? 'env' : config.enableGraph ? 'on' : 'off',
  }
}

export function InfraAccessSettings({ open }: { open: boolean }) {
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<InfraForm>(EMPTY_FORM)
  const [status, setStatus] = useState<InfraStatusResponse | null>(null)
  const [saveBusy, setSaveBusy] = useState(false)
  const [testBusy, setTestBusy] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [restartRequired, setRestartRequired] = useState(false)
  const [testResult, setTestResult] = useState<{
    milvus?: InfraTestResult
    neo4j?: InfraTestResult
  } | null>(null)
  const [revealPassword, setRevealPassword] = useState(false)

  const refreshStatus = useCallback(() => {
    fetchInfraStatus()
      .then((s) => setStatus(s))
      .catch((err) => console.error('[InfraAccess] status poll failed', err))
  }, [])

  useEffect(() => {
    if (!open) return
    let cancelled = false

    setLoading(true)
    setSaveError(null)
    setRestartRequired(false)
    setTestResult(null)

    fetchInfraConfig()
      .then((resp) => {
        if (!cancelled) setForm(configToForm(resp.config))
      })
      .catch((err) => console.error('[InfraAccess] load config failed', err))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    refreshStatus()

    const timer = window.setInterval(refreshStatus, 10_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [open, refreshStatus])

  const handleSave = async () => {
    if (saveBusy) return
    setSaveBusy(true)
    setSaveError(null)
    try {
      const f = form
      const password = f.neo4jPassword
      await updateInfraConfig({
        milvusHost: f.milvusHost.trim() || null,
        milvusPort: f.milvusPort.trim() ? parseInt(f.milvusPort.trim(), 10) : null,
        neo4jUri: f.neo4jUri.trim() || null,
        neo4jUser: f.neo4jUser.trim() || null,
        // 掩码回显或空串 = 未修改（不发送）
        ...(password && password !== INFRA_PASSWORD_MASK ? { neo4jPassword: password } : {}),
        enableGraph: f.enableGraph === 'env' ? null : f.enableGraph === 'on',
      })
      setRestartRequired(true)
      setTestResult(null)
      refreshStatus()
    } catch (err) {
      console.error('[InfraAccess] save failed', err)
      setSaveError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaveBusy(false)
    }
  }

  const handleTest = async () => {
    if (testBusy) return
    setTestBusy(true)
    try {
      const f = form
      const result = await testInfraConnection({
        ...(f.milvusHost.trim()
          ? { milvusHost: f.milvusHost.trim(), milvusPort: f.milvusPort.trim() ? parseInt(f.milvusPort.trim(), 10) : null }
          : {}),
        ...(f.neo4jUri.trim()
          ? { neo4jUri: f.neo4jUri.trim(), neo4jUser: f.neo4jUser.trim(), neo4jPassword: f.neo4jPassword }
          : {}),
      })
      setTestResult(result)
    } catch (err) {
      console.error('[InfraAccess] test failed', err)
      const message = err instanceof Error ? err.message : '测试请求失败'
      setTestResult({
        milvus: { tested: true, ok: false, error: message },
        neo4j: { tested: false },
      })
    } finally {
      setTestBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-3 py-1">
      <div className="flex items-center gap-2">
        <Server className="size-4 text-muted-foreground" />
        <h3 className="text-sm font-medium">基础设施接入</h3>
      </div>

      <StatusBadges status={status} />

      {restartRequired && (
        <div className="flex gap-2 rounded-md border border-warning/30 bg-warning/10 px-2 py-2 text-warning">
          <AlertTriangle className="mt-0.5 size-4 flex-none" />
          <div className="min-w-0">
            <p className="text-xs font-semibold">配置已保存，重启后生效</p>
            <p className="mt-1 text-[11px] leading-4">
              基础设施在应用启动时装配，请重启桌面端 App 使新连接生效。
            </p>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex h-24 items-center justify-center">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          <div className="grid gap-3 rounded-lg border bg-muted/30 p-3">
            <div className="grid gap-1.5">
              <label className="text-xs font-medium">Milvus Host</label>
              <Input
                value={form.milvusHost}
                onChange={(e) => setForm((f) => ({ ...f, milvusHost: e.target.value }))}
                placeholder="留空跟随 env（如 127.0.0.1）"
                className="h-8 font-mono text-xs"
                spellCheck={false}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="text-xs font-medium">Milvus Port</label>
              <Input
                type="number"
                value={form.milvusPort}
                onChange={(e) => setForm((f) => ({ ...f, milvusPort: e.target.value }))}
                placeholder="19530"
                className="h-8 text-xs"
              />
            </div>

            <div className="my-1 border-t" />

            <div className="grid gap-1.5">
              <label className="text-xs font-medium">Neo4j URI</label>
              <Input
                value={form.neo4jUri}
                onChange={(e) => setForm((f) => ({ ...f, neo4jUri: e.target.value }))}
                placeholder="留空跟随 env（如 bolt://127.0.0.1:7687）"
                className="h-8 font-mono text-xs"
                spellCheck={false}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="text-xs font-medium">Neo4j 用户</label>
                <Input
                  value={form.neo4jUser}
                  onChange={(e) => setForm((f) => ({ ...f, neo4jUser: e.target.value }))}
                  placeholder="neo4j"
                  className="h-8 font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium">Neo4j 密码</label>
                <div className="relative">
                  <Input
                    type={revealPassword ? 'text' : 'password'}
                    value={form.neo4jPassword}
                    onChange={(e) => setForm((f) => ({ ...f, neo4jPassword: e.target.value }))}
                    placeholder="留空 = 不修改"
                    className="h-8 pr-9 font-mono text-xs"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    onClick={() => setRevealPassword((r) => !r)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground hover:text-foreground"
                    title={revealPassword ? '隐藏' : '显示'}
                  >
                    {revealPassword ? '隐藏' : '显示'}
                  </button>
                </div>
              </div>
            </div>
            <div className="grid gap-1.5">
              <label className="text-xs font-medium">知识图谱</label>
              <select
                value={form.enableGraph}
                onChange={(e) =>
                  setForm((f) => ({ ...f, enableGraph: e.target.value as InfraForm['enableGraph'] }))
                }
                className="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:border-foreground/30"
              >
                <option value="env">跟随全局默认</option>
                <option value="on">开启</option>
                <option value="off">关闭</option>
              </select>
            </div>
          </div>

          {testResult && <TestResults result={testResult} />}

          {saveError && (
            <p className="text-xs text-destructive">{saveError}</p>
          )}

          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={testBusy || saveBusy}
              onClick={() => void handleTest()}
            >
              {testBusy ? <Loader2 className="size-3.5 animate-spin" /> : <PlugZap className="size-3.5" />}
              测试连接
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={testBusy || saveBusy}
              onClick={() => void handleSave()}
            >
              {saveBusy ? '保存中…' : '保存接入配置'}
            </Button>
          </div>

          <p className="text-[11px] leading-4 text-muted-foreground">
            接入自部署的 Milvus / Neo4j 后，RAG 检索与记忆增强将使用远端服务；某个服务连接失败时独立降级，不影响其余功能。
          </p>
        </>
      )}
    </section>
  )
}

function StatusBadges({ status }: { status: InfraStatusResponse | null }) {
  if (!status) {
    return (
      <div className="flex h-8 items-center gap-2 rounded-md border bg-background px-2 text-[11px] text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
        正在获取接入状态…
      </div>
    )
  }
  return (
    <div className="flex flex-wrap gap-1.5 rounded-md border bg-background px-2 py-1.5">
      {STATUS_BADGES.map((name) => {
        const svc = status.services[name]
        if (!svc) return null
        const label = STATUS_LABEL[svc.status] ?? svc.status
        const source =
          svc.configSource === 'db' ? '落库配置' : svc.configSource === 'env' ? 'env 配置' : '未配置'
        return (
          <span
            key={name}
            className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px]"
            title={svc.detail ? `${label}：${svc.detail}` : `${label}（${source}）`}
          >
            <span
              className={cn(
                'size-1.5 rounded-full',
                svc.status === 'connected' && 'bg-success',
                svc.status === 'degraded' && 'bg-warning',
                svc.status === 'disabled' && 'bg-muted-foreground/40',
              )}
            />
            <span className="font-medium">{name}</span>
            <span className="text-muted-foreground">{label}</span>
          </span>
        )
      })}
    </div>
  )
}

function TestResults({
  result,
}: {
  result: { milvus?: InfraTestResult; neo4j?: InfraTestResult }
}) {
  return (
    <div className="space-y-1 rounded-md border bg-background px-2 py-2">
      {(['milvus', 'neo4j'] as const).map((name) => {
        const r = result[name]
        if (!r || !r.tested) return null
        return (
          <div key={name} className="flex items-center gap-1.5 text-[11px]">
            <span
              className={cn('size-1.5 rounded-full', r.ok ? 'bg-success' : 'bg-destructive')}
            />
            <span className="font-medium">{name}</span>
            {r.ok ? (
              <span className="text-muted-foreground">可连接（{r.latencyMs ?? 0} ms）</span>
            ) : (
              <span className="min-w-0 flex-1 truncate text-destructive" title={r.error}>
                {r.errorKind ? ERROR_KIND_LABEL[r.errorKind] : ''}
                {r.error ? `：${r.error}` : ''}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
