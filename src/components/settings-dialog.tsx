'use client'

import {
  AlertTriangle,
  Check,
  Clipboard,
  Eye,
  EyeOff,
  FileText,
  FolderUp,
  Info,
  KeyRound,
  Loader2,
  Monitor,
  Network,
  Power,
  RotateCw,
  Smartphone,
} from 'lucide-react'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { AppSettingsRow } from '@/db/schema'
import {
  fetchAppSettings,
  fetchConnectionHints,
  fetchOcrEngines,
  fetchRagPresets,
  regenerateMobileDeviceToken,
  updateAppSettings,
  type AppSettingsPatchBody,
  type ConnectionHint,
} from '@/lib/api'
import type { OcrEngineStatus, RagPreset } from '@/shared/types'
import { cn } from '@/lib/utils'
interface SettingsForm {
  anthropicApiKey: string
  anthropicBaseUrl: string
  openaiApiKey: string
  deepseekApiKey: string
  arkApiKey: string
  companionMode: 'off' | 'lan' | 'tailnet'
  mobileDeviceToken: string
  deploymentPublishEnabled: boolean
  deploymentPublishDir: string
  deploymentPublicBaseUrl: string
  ragChunkPreset: string
  ragChunkSize: string
  ragChunkOverlap: string
  ocrEngine: string
}

/**
 * 全局 API key / endpoint 设置面板。
 *
 * 4 个 provider key + Anthropic 自定义 base URL。明文展示，因为本地单用户场景安全
 * 收益小且会引入 keychain / safeStorage 复杂度（详见 spec / CLAUDE.md §5.4）。
 *
 * 优先级（adapter 侧）：agent.apiKey > app_settings > process.env。
 */
export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hintsLoading, setHintsLoading] = useState(false)
  const [tokenBusy, setTokenBusy] = useState(false)
  const [restartRequired, setRestartRequired] = useState(false)
  const [connectionHints, setConnectionHints] = useState<ConnectionHint[]>([])
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const [tab, setTab] = useState('keys')
  const [form, setForm] = useState<SettingsForm>({
    anthropicApiKey: '',
    anthropicBaseUrl: '',
    openaiApiKey: '',
    deepseekApiKey: '',
    arkApiKey: '',
    companionMode: 'off',
    mobileDeviceToken: '',
    deploymentPublishEnabled: false,
    deploymentPublishDir: '',
    deploymentPublicBaseUrl: '',
    ragChunkPreset: '',
    ragChunkSize: '',
    ragChunkOverlap: '',
    ocrEngine: '',
  })
  const [ragPresets, setRagPresets] = useState<RagPreset[]>([])
  const [ocrEngines, setOcrEngines] = useState<OcrEngineStatus[]>([])
  const [reveal, setReveal] = useState<Record<keyof SettingsForm, boolean>>({
    anthropicApiKey: false,
    anthropicBaseUrl: false,
    openaiApiKey: false,
    deepseekApiKey: false,
    arkApiKey: false,
    companionMode: true,
    mobileDeviceToken: false,
    deploymentPublishEnabled: true,
    deploymentPublishDir: true,
    deploymentPublicBaseUrl: true,
    ragChunkPreset: true,
    ragChunkSize: true,
    ragChunkOverlap: true,
    ocrEngine: true,
  })

  useEffect(() => {
    if (!open) return
    let cancelled = false

    void Promise.resolve()
      .then(() => {
        if (!cancelled) {
          setRestartRequired(false)
          setLoading(true)
        }
        return fetchAppSettings()
      })
      .then((s) => {
        if (!cancelled) setForm(rowToForm(s))
      })
      .catch((err) => console.error('[SettingsDialog] load failed', err))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    // Load RAG presets and OCR engines for the RAG tab
    fetchRagPresets()
      .then((data) => { if (!cancelled) setRagPresets(data.presets) })
      .catch((err) => console.error('[SettingsDialog] fetchRagPresets failed', err))
    fetchOcrEngines()
      .then((data) => { if (!cancelled) setOcrEngines(data.engines) })
      .catch((err) => console.error('[SettingsDialog] fetchOcrEngines failed', err))

    return () => {
      cancelled = true
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    let cancelled = false

    void Promise.resolve()
      .then(() => {
        if (!cancelled) setHintsLoading(true)
        return fetchConnectionHints()
      })
      .then((hints) => {
        if (!cancelled) setConnectionHints(hints)
      })
      .catch((err) => console.error('[SettingsDialog] load connection hints failed', err))
      .finally(() => {
        if (!cancelled) setHintsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [open])

  const handleSave = async () => {
    if (busy) return
    setBusy(true)
    try {
      // 空串归一 null，明确「清空」语义
      const patch: AppSettingsPatchBody = {
        anthropicApiKey: form.anthropicApiKey.trim() || null,
        anthropicBaseUrl: form.anthropicBaseUrl.trim() || null,
        openaiApiKey: form.openaiApiKey.trim() || null,
        deepseekApiKey: form.deepseekApiKey.trim() || null,
        arkApiKey: form.arkApiKey.trim() || null,
        companionMode: form.companionMode,
        mobileDeviceToken: form.mobileDeviceToken.trim() || null,
        deploymentPublishEnabled: form.deploymentPublishEnabled,
        deploymentPublishDir: form.deploymentPublishDir.trim() || null,
        deploymentPublicBaseUrl: form.deploymentPublicBaseUrl.trim() || null,
        ragChunkPreset: form.ragChunkPreset || null,
        ragChunkSize: form.ragChunkSize ? parseInt(form.ragChunkSize, 10) : null,
        ragChunkOverlap: form.ragChunkOverlap ? parseInt(form.ragChunkOverlap, 10) : null,
        ocrEngine: form.ocrEngine || null,
      }
      await updateAppSettings(patch)
      onOpenChange(false)
    } catch (err) {
      console.error('[SettingsDialog] save failed', err)
    } finally {
      setBusy(false)
    }
  }

  const handleCopyHint = async (hint: ConnectionHint) => {
    try {
      await navigator.clipboard.writeText(hint.url)
      setCopiedUrl(hint.url)
      window.setTimeout(() => setCopiedUrl(null), 1200)
    } catch (err) {
      console.error('[SettingsDialog] copy connection hint failed', err)
    }
  }

  const handleCopyText = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopiedUrl(value)
      window.setTimeout(() => setCopiedUrl(null), 1200)
    } catch (err) {
      console.error('[SettingsDialog] copy text failed', err)
    }
  }

  const handleEnableCompanion = async () => {
    if (busy) return
    setBusy(true)
    try {
      const next = await updateAppSettings({
        companionMode: 'tailnet',
        mobileDeviceToken: form.mobileDeviceToken.trim() || null,
      })
      setForm(rowToForm(next))
      setConnectionHints(await fetchConnectionHints())
      setRestartRequired(true)
    } catch (err) {
      console.error('[SettingsDialog] enable companion failed', err)
    } finally {
      setBusy(false)
    }
  }

  const handleDisableCompanion = async () => {
    if (busy) return
    setBusy(true)
    try {
      const next = await updateAppSettings({ companionMode: 'off' })
      setForm(rowToForm(next))
      setConnectionHints(await fetchConnectionHints())
      setRestartRequired(true)
    } catch (err) {
      console.error('[SettingsDialog] disable companion failed', err)
    } finally {
      setBusy(false)
    }
  }

  const handleRegenerateToken = async () => {
    if (tokenBusy) return
    setTokenBusy(true)
    try {
      const next = await regenerateMobileDeviceToken()
      setForm(rowToForm(next))
    } catch (err) {
      console.error('[SettingsDialog] regenerate mobile token failed', err)
    } finally {
      setTokenBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] max-w-xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
          <DialogDescription className="sr-only">AChat 设置</DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex min-h-0 items-center justify-center">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <Tabs value={tab} onValueChange={setTab} className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-3">
            <div className="flex items-center justify-between gap-2">
              <TabsList>
                <TabsTrigger value="keys">
                  <KeyRound className="size-3.5" />
                  供应商 Key
                </TabsTrigger>
                <TabsTrigger value="mobile">
                  <Smartphone className="size-3.5" />
                  移动端
                </TabsTrigger>
                <TabsTrigger value="publish">
                  <FolderUp className="size-3.5" />
                  发布
                </TabsTrigger>
                <TabsTrigger value="rag">
                  <FileText className="size-3.5" />
                  RAG
                </TabsTrigger>
              </TabsList>
              {tab === 'keys' && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger
                      type="button"
                      className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition hover:bg-accent hover:text-foreground"
                      aria-label="供应商 Key 说明"
                    >
                      <Info className="size-4" />
                    </TooltipTrigger>
                    <TooltipContent side="bottom" align="end" className="max-w-72 whitespace-normal text-left leading-5">
                      填写常用供应商的 API Key。填写后将覆盖系统环境变量；留空则继续使用环境变量（如有）。
                      Agent 设置中单独配置的 Key 仍然优先级最高。
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
            </div>

            <div className="min-h-0 overflow-y-auto pr-1">
              <TabsContent value="keys" className="mt-0 flex flex-col gap-3 py-1">
                <KeyField
                  label="Anthropic API Key"
                  hint="用于 Claude Code adapter / custom anthropic provider。"
                  value={form.anthropicApiKey}
                  reveal={reveal.anthropicApiKey}
                  onChange={(v) => setForm((f) => ({ ...f, anthropicApiKey: v }))}
                  onToggleReveal={() =>
                    setReveal((r) => ({ ...r, anthropicApiKey: !r.anthropicApiKey }))
                  }
                />
                <KeyField
                  label="Anthropic Base URL（可选）"
                  hint="走第三方网关时填，如 https://anyrouter.top；留空走官方 endpoint。"
                  type="text"
                  value={form.anthropicBaseUrl}
                  reveal
                  onChange={(v) => setForm((f) => ({ ...f, anthropicBaseUrl: v }))}
                />
                <KeyField
                  label="OpenAI API Key"
                  value={form.openaiApiKey}
                  reveal={reveal.openaiApiKey}
                  onChange={(v) => setForm((f) => ({ ...f, openaiApiKey: v }))}
                  onToggleReveal={() => setReveal((r) => ({ ...r, openaiApiKey: !r.openaiApiKey }))}
                />
                <KeyField
                  label="DeepSeek API Key"
                  value={form.deepseekApiKey}
                  reveal={reveal.deepseekApiKey}
                  onChange={(v) => setForm((f) => ({ ...f, deepseekApiKey: v }))}
                  onToggleReveal={() => setReveal((r) => ({ ...r, deepseekApiKey: !r.deepseekApiKey }))}
                />
                <KeyField
                  label="Volcano Ark API Key"
                  value={form.arkApiKey}
                  reveal={reveal.arkApiKey}
                  onChange={(v) => setForm((f) => ({ ...f, arkApiKey: v }))}
                  onToggleReveal={() => setReveal((r) => ({ ...r, arkApiKey: !r.arkApiKey }))}
                />
              </TabsContent>

              <TabsContent value="mobile" className="mt-0 py-1">
                <MobileConnectionHints
                  hints={connectionHints}
                  loading={hintsLoading}
                  copiedUrl={copiedUrl}
                  companionMode={form.companionMode}
                  mobileDeviceToken={form.mobileDeviceToken}
                  busy={busy}
                  tokenBusy={tokenBusy}
                  restartRequired={restartRequired}
                  onCopy={(hint) => void handleCopyHint(hint)}
                  onCopyText={(value) => void handleCopyText(value)}
                  onEnable={() => void handleEnableCompanion()}
                  onDisable={() => void handleDisableCompanion()}
                  onRegenerateToken={() => void handleRegenerateToken()}
                />
              </TabsContent>

              <TabsContent value="publish" className="mt-0 py-1">
                <DeploymentPublishSettings
                  enabled={form.deploymentPublishEnabled}
                  publishDir={form.deploymentPublishDir}
                  publicBaseUrl={form.deploymentPublicBaseUrl}
                  onEnabledChange={(deploymentPublishEnabled) =>
                    setForm((f) => ({ ...f, deploymentPublishEnabled }))
                  }
                  onPublishDirChange={(deploymentPublishDir) =>
                    setForm((f) => ({ ...f, deploymentPublishDir }))
                  }
                  onPublicBaseUrlChange={(deploymentPublicBaseUrl) =>
                    setForm((f) => ({ ...f, deploymentPublicBaseUrl }))
                  }
                />
              </TabsContent>

              <TabsContent value="rag" className="mt-0 py-1">
                <RagConfigSettings
                  ragChunkPreset={form.ragChunkPreset}
                  ragChunkSize={form.ragChunkSize}
                  ragChunkOverlap={form.ragChunkOverlap}
                  ocrEngine={form.ocrEngine}
                  presets={ragPresets}
                  ocrEngines={ocrEngines}
                  onPresetChange={(ragChunkPreset) => setForm((f) => ({ ...f, ragChunkPreset }))}
                  onChunkSizeChange={(ragChunkSize) => setForm((f) => ({ ...f, ragChunkSize }))}
                  onChunkOverlapChange={(ragChunkOverlap) => setForm((f) => ({ ...f, ragChunkOverlap }))}
                  onOcrEngineChange={(ocrEngine) => setForm((f) => ({ ...f, ocrEngine }))}
                />
              </TabsContent>

            </div>
          </Tabs>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => void handleSave()} disabled={busy || loading}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MobileConnectionHints({
  hints,
  loading,
  copiedUrl,
  companionMode,
  mobileDeviceToken,
  busy,
  tokenBusy,
  restartRequired,
  onCopy,
  onCopyText,
  onEnable,
  onDisable,
  onRegenerateToken,
}: {
  hints: ConnectionHint[]
  loading: boolean
  copiedUrl: string | null
  companionMode: 'off' | 'lan' | 'tailnet'
  mobileDeviceToken: string
  busy: boolean
  tokenBusy: boolean
  restartRequired: boolean
  onCopy: (hint: ConnectionHint) => void
  onCopyText: (value: string) => void
  onEnable: () => void
  onDisable: () => void
  onRegenerateToken: () => void
}) {
  const enabled = companionMode !== 'off'

  return (
    <section className="rounded-lg border bg-muted/30 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Smartphone className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">移动端连接</h3>
        </div>
        <Button
          type="button"
          size="sm"
          variant={enabled ? 'outline' : 'default'}
          disabled={busy}
          onClick={enabled ? onDisable : onEnable}
        >
          <Power className="size-3.5" />
          {enabled ? '关闭' : '开启'}
        </Button>
      </div>

      {loading ? (
        <div className="flex h-16 items-center justify-center">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {restartRequired && (
            <div className="flex gap-2 rounded-md border border-warning/30 bg-warning/10 px-2 py-2 text-warning">
              <AlertTriangle className="mt-0.5 size-4 flex-none" />
              <div className="min-w-0">
                <p className="text-xs font-semibold">请重启桌面端 App</p>
                <p className="mt-1 text-[11px] leading-4">
                  开启或关闭移动端连接后，监听地址需要重启后才会生效。
                </p>
              </div>
            </div>
          )}

          <div className="rounded-md border bg-background px-2 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <KeyRound className="size-4 flex-none text-muted-foreground" />
                <div className="min-w-0">
                  <p className="text-xs font-medium">{enabled ? '已开启' : '未开启'}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {enabled ? '重启桌面端后生效' : '开启后生成设备 token'}
                  </p>
                </div>
              </div>
              {mobileDeviceToken && (
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label="复制设备 token"
                  title="复制 token"
                  onClick={() => onCopyText(mobileDeviceToken)}
                >
                  {copiedUrl === mobileDeviceToken ? (
                    <Check className="size-4" />
                  ) : (
                    <Clipboard className="size-4" />
                  )}
                </Button>
              )}
            </div>
            {mobileDeviceToken && (
              <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground">
                {mobileDeviceToken}
              </p>
            )}
          </div>

          {enabled && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={tokenBusy}
              onClick={onRegenerateToken}
            >
              {tokenBusy ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCw className="size-3.5" />}
              重新生成 token
            </Button>
          )}

          {hints.map((hint) => (
            <div
              key={hint.url}
              className="flex items-center gap-2 rounded-md border bg-background px-2 py-2"
            >
              {hintIcon(hint.kind)}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{hint.label}</span>
                  {hint.interfaceName && (
                    <span className="text-[10px] text-muted-foreground">{hint.interfaceName}</span>
                  )}
                </div>
                <p className="truncate font-mono text-[11px] text-muted-foreground">{hint.url}</p>
              </div>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={`复制 ${hint.label} 地址`}
                title="复制"
                onClick={() => onCopy(hint)}
              >
                {copiedUrl === hint.url ? <Check className="size-4" /> : <Clipboard className="size-4" />}
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function DeploymentPublishSettings({
  enabled,
  publishDir,
  publicBaseUrl,
  onEnabledChange,
  onPublishDirChange,
  onPublicBaseUrlChange,
}: {
  enabled: boolean
  publishDir: string
  publicBaseUrl: string
  onEnabledChange: (enabled: boolean) => void
  onPublishDirChange: (value: string) => void
  onPublicBaseUrlChange: (value: string) => void
}) {
  return (
    <section className="rounded-lg border bg-muted/30 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FolderUp className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">外部静态发布</h3>
        </div>
        <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => onEnabledChange(event.currentTarget.checked)}
            className="size-4 rounded border-input accent-primary"
          />
          启用
        </label>
      </div>

      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <label className="text-xs font-medium">发布目录</label>
          <Input
            value={publishDir}
            onChange={(event) => onPublishDirChange(event.target.value)}
            placeholder="D:\\sites\\agenthub"
          />
          <p className="text-[11px] leading-4 text-muted-foreground">
            AChat 会写入该目录下的 dep_xxx 子目录。
          </p>
        </div>
        <div className="grid gap-1.5">
          <label className="text-xs font-medium">公开根 URL</label>
          <Input
            value={publicBaseUrl}
            onChange={(event) => onPublicBaseUrlChange(event.target.value)}
            placeholder="https://example.com/apps"
          />
          <p className="text-[11px] leading-4 text-muted-foreground">
            部署卡片会返回公开根 URL 加 deployment id 的地址。
          </p>
        </div>
      </div>
    </section>
  )
}

function RagConfigSettings({
  ragChunkPreset,
  ragChunkSize,
  ragChunkOverlap,
  ocrEngine,
  presets,
  ocrEngines,
  onPresetChange,
  onChunkSizeChange,
  onChunkOverlapChange,
  onOcrEngineChange,
}: {
  ragChunkPreset: string
  ragChunkSize: string
  ragChunkOverlap: string
  ocrEngine: string
  presets: RagPreset[]
  ocrEngines: OcrEngineStatus[]
  onPresetChange: (value: string) => void
  onChunkSizeChange: (value: string) => void
  onChunkOverlapChange: (value: string) => void
  onOcrEngineChange: (value: string) => void
}) {
  return (
    <section className="rounded-lg border bg-muted/30 p-3">
      <div className="mb-3 flex items-center gap-2">
        <FileText className="size-4 text-muted-foreground" />
        <h3 className="text-sm font-medium">RAG 配置</h3>
      </div>

      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <label className="text-xs font-medium">默认分块策略</label>
          <select
            value={ragChunkPreset}
            onChange={(e) => onPresetChange(e.target.value)}
            className="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:border-foreground/30"
          >
            <option value="">跟随全局默认</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <p className="text-[11px] leading-4 text-muted-foreground">
            上传时选择「跟随默认」将使用此策略。
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <label className="text-xs font-medium">分块大小</label>
            <Input
              type="number"
              value={ragChunkSize}
              onChange={(e) => onChunkSizeChange(e.target.value)}
              placeholder="200"
              className="h-8 text-xs"
            />
          </div>
          <div className="grid gap-1.5">
            <label className="text-xs font-medium">分块重叠</label>
            <Input
              type="number"
              value={ragChunkOverlap}
              onChange={(e) => onChunkOverlapChange(e.target.value)}
              placeholder="50"
              className="h-8 text-xs"
            />
          </div>
        </div>

        <div className="grid gap-1.5">
          <label className="text-xs font-medium">OCR 引擎</label>
          <select
            value={ocrEngine}
            onChange={(e) => onOcrEngineChange(e.target.value)}
            className="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:border-foreground/30"
          >
            <option value="">跟随全局默认 (auto)</option>
            {ocrEngines.map((eng) => (
              <option key={eng.id} value={eng.id}>
                {eng.label} ({eng.available ? '可用' : '不可用'})
              </option>
            ))}
          </select>
        </div>

        {ocrEngines.length > 0 && (
          <div className="space-y-1 rounded-md border bg-background px-2 py-2">
            <p className="text-[11px] font-medium text-muted-foreground">引擎状态</p>
            {ocrEngines.map((eng) => (
              <div key={eng.id} className="flex items-center gap-1.5 text-[11px]">
                <span className={cn('size-1.5 rounded-full', eng.available ? 'bg-success' : 'bg-muted-foreground/40')} />
                <span className="font-medium">{eng.label}</span>
                <span className="text-muted-foreground">
                  {eng.status === 'ok' ? '可用' :
                   eng.status === 'not_installed' ? '未安装' :
                   eng.status === 'not_configured' ? '未配置 Key' :
                   '服务不可达'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function hintIcon(kind: ConnectionHint['kind']) {
  switch (kind) {
    case 'tailscale':
    case 'lan':
      return <Network className="size-4 flex-none text-muted-foreground" />
    case 'local':
      return <Monitor className="size-4 flex-none text-muted-foreground" />
  }
}

function KeyField({
  label,
  hint,
  value,
  reveal,
  type = 'password',
  onChange,
  onToggleReveal,
}: {
  label: string
  hint?: string
  value: string
  reveal: boolean
  type?: 'password' | 'text'
  onChange: (v: string) => void
  onToggleReveal?: () => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium">{label}</label>
      <div className="relative">
        <Input
          type={type === 'text' || reveal ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={onToggleReveal ? 'pr-9 font-mono text-xs' : 'font-mono text-xs'}
        />
        {onToggleReveal && (
          <button
            type="button"
            onClick={onToggleReveal}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            title={reveal ? '隐藏' : '显示'}
          >
            {reveal ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </button>
        )}
      </div>
      {hint && <p className="text-[10px] text-muted-foreground/80">{hint}</p>}
    </div>
  )
}

function rowToForm(row: AppSettingsRow): SettingsForm {
  return {
    anthropicApiKey: row.anthropicApiKey ?? '',
    anthropicBaseUrl: row.anthropicBaseUrl ?? '',
    openaiApiKey: row.openaiApiKey ?? '',
    deepseekApiKey: row.deepseekApiKey ?? '',
    arkApiKey: row.arkApiKey ?? '',
    companionMode: row.companionMode,
    mobileDeviceToken: row.mobileDeviceToken ?? '',
    deploymentPublishEnabled: row.deploymentPublishEnabled,
    deploymentPublishDir: row.deploymentPublishDir ?? '',
    deploymentPublicBaseUrl: row.deploymentPublicBaseUrl ?? '',
    ragChunkPreset: row.ragChunkPreset ?? '',
    ragChunkSize: row.ragChunkSize != null ? String(row.ragChunkSize) : '',
    ragChunkOverlap: row.ragChunkOverlap != null ? String(row.ragChunkOverlap) : '',
    ocrEngine: row.ocrEngine ?? '',
  }
}
