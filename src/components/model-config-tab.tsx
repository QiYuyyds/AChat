'use client'

import { CheckCircle2, Coins, Loader2, Pencil, Plus, Star, Trash2, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  createModelProfile,
  deleteModelProfile,
  fetchModelProfiles,
  fetchUsageSummary,
  testModelProfile,
  updateModelProfile,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'
import { formatTok } from '@/components/usage-dashboard'
import type { ModelProfile, ModelProvider } from '@/shared/types'
import type { UsageSummary } from '@/lib/api'

const PROVIDERS: { value: ModelProvider; label: string }[] = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'volcano-ark', label: '火山方舟 (豆包)' },
  { value: 'openai-compatible', label: 'OpenAI-compatible' },
]

const PROVIDER_COLORS: Record<ModelProvider, string> = {
  deepseek: 'bg-blue-500/10 text-blue-500',
  anthropic: 'bg-orange-500/10 text-orange-500',
  openai: 'bg-green-500/10 text-green-500',
  'volcano-ark': 'bg-purple-500/10 text-purple-500',
  'openai-compatible': 'bg-gray-500/10 text-gray-500',
}

export function ModelConfigTab() {
  const profiles = useAppStore((s) => s.modelProfiles)
  const setModelProfiles = useAppStore((s) => s.setModelProfiles)
  const upsertModelProfile = useAppStore((s) => s.upsertModelProfile)
  const removeModelProfile = useAppStore((s) => s.removeModelProfile)
  const [loading, setLoading] = useState(true)
  const [editOpen, setEditOpen] = useState(false)
  const [editingProfile, setEditingProfile] = useState<ModelProfile | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, { status: string; latencyMs?: number; error?: string } | undefined>>({})
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set())
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchModelProfiles()
      .then(setModelProfiles)
      .catch(console.error)
      .finally(() => setLoading(false))
    fetchUsageSummary().then(setUsageSummary).catch(() => {})
  }, [setModelProfiles])

  const profileList = useMemo(
    () => Object.values(profiles).sort((a, b) => b.createdAt - a.createdAt),
    [profiles],
  )

  // Build a map of modelId → totalTokens from usage data
  const modelUsageMap = useMemo(() => {
    const map = new Map<string, number>()
    if (usageSummary) {
      for (const m of usageSummary.byModel) {
        map.set(m.model, m.totalTokens)
      }
    }
    return map
  }, [usageSummary])

  const maxUsage = useMemo(() => {
    if (modelUsageMap.size === 0) return 1
    return Math.max(...modelUsageMap.values(), 1)
  }, [modelUsageMap])

  const handleTest = useCallback(async (profileId: string) => {
    setTestingIds((prev) => new Set(prev).add(profileId))
    try {
      const result = await testModelProfile(profileId)
      setTestResult((prev) => ({ ...prev, [profileId]: result }))
      const updated = await fetchModelProfiles()
      setModelProfiles(updated)
    } catch (err) {
      setTestResult((prev) => ({
        ...prev,
        [profileId]: { status: 'fail', error: err instanceof Error ? err.message : 'Request failed' },
      }))
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev)
        next.delete(profileId)
        return next
      })
    }
  }, [setModelProfiles])

  const handleSetDefault = useCallback(async (profileId: string) => {
    try {
      const updated = await updateModelProfile(profileId, { isDefault: true })
      upsertModelProfile(updated)
      const all = await fetchModelProfiles()
      setModelProfiles(all)
    } catch (err) {
      console.error('[ModelConfig] set default failed', err)
    }
  }, [upsertModelProfile, setModelProfiles])

  const handleDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteModelProfile(deleteTargetId)
      removeModelProfile(deleteTargetId)
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[ModelConfig] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Title and description + action */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">模型配置</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            管理模型配置档，独立于 Agent 实体，可在输入栏按消息切换
          </p>
        </div>
        <Button
          size="sm"
          className="gap-1.5 text-xs"
          onClick={() => { setEditingProfile(null); setEditOpen(true) }}
        >
          <Plus className="size-3.5" />
          添加
        </Button>
      </div>

      {/* Grid */}
      {loading && profileList.length === 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-48 animate-pulse rounded-xl border border-border/40 bg-card" />
          ))}
        </div>
      ) : profileList.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
            <Plus className="size-8 text-muted-foreground opacity-50" />
          </div>
          <h3 className="mt-4 text-sm font-medium">还没有模型配置</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            点击右上角「添加」创建第一个模型档
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {profileList.map((p) => {
            const testing = testingIds.has(p.id)
            const result = testResult[p.id]
            const usage = modelUsageMap.get(p.modelId) ?? 0
            const usagePct = maxUsage > 0 ? (usage * 100) / maxUsage : 0
            const providerColor = PROVIDER_COLORS[p.provider] ?? 'bg-gray-500/10 text-gray-500'
            return (
              <div
                key={p.id}
                className="group relative flex cursor-pointer flex-col rounded-xl border bg-card p-4 transition-all hover:border-primary/50 hover:shadow-sm"
                onClick={() => { setEditingProfile(p); setEditOpen(true) }}
              >
                {/* Header */}
                <div className="flex items-start gap-3">
                  <div className={cn('flex size-10 shrink-0 items-center justify-center rounded-lg', providerColor)}>
                    <span className="text-xs font-bold uppercase">{p.provider.slice(0, 2)}</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {p.isDefault && (
                        <Star className="size-3 shrink-0 fill-warning text-warning" />
                      )}
                      <h4 className="truncate text-sm font-medium">{p.name}</h4>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{p.modelId}</p>
                  </div>
                </div>

                {/* Provider label */}
                <div className="mt-2 text-xs text-muted-foreground">
                  {PROVIDERS.find((x) => x.value === p.provider)?.label ?? p.provider}
                </div>

                {/* API key hint */}
                {p.apiKeyLast4 && (
                  <div className="mt-1 text-xs text-muted-foreground/70">
                    key ••••{p.apiKeyLast4}
                  </div>
                )}

                {/* Test status */}
                <div className="mt-2 flex items-center gap-1.5">
                  {testing ? (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" /> 测试中…
                    </span>
                  ) : result ? (
                    <span className={cn(
                      'inline-flex items-center gap-1 text-xs',
                      result.status === 'ok' ? 'text-success' : 'text-destructive',
                    )}>
                      {result.status === 'ok' ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
                      {result.status === 'ok' ? `${result.latencyMs}ms` : result.error ?? '失败'}
                    </span>
                  ) : p.lastTestStatus === 'ok' ? (
                    <span className="inline-flex items-center gap-1 text-xs text-success">
                      <CheckCircle2 className="size-3" /> 已通过
                    </span>
                  ) : p.lastTestStatus === 'fail' ? (
                    <span className="inline-flex items-center gap-1 text-xs text-destructive">
                      <XCircle className="size-3" /> 未通过
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground/50">未测试</span>
                  )}
                </div>

                {/* Usage bar */}
                {usage > 0 && (
                  <div className="mt-3 space-y-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <Coins className="size-2.5" /> 用量
                      </span>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {formatTok(usage)}
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-primary/70 via-primary/50 to-primary/30 transition-all duration-500"
                        style={{ width: `${usagePct}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Hover actions */}
                <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    onClick={(e) => { e.stopPropagation(); void handleTest(p.id) }}
                    disabled={testing}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                    title="测试连通性"
                  >
                    {testing ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Loader2 className="size-3.5" />
                    )}
                  </button>
                  {!p.isDefault && (
                    <button
                      onClick={(e) => { e.stopPropagation(); void handleSetDefault(p.id) }}
                      className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                      title="设为默认"
                    >
                      <Star className="size-3.5" />
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); setEditingProfile(p); setEditOpen(true) }}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                    title="编辑"
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeleteTargetId(p.id) }}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive"
                    title="删除"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Edit / Create dialog */}
      <EditDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        profile={editingProfile}
        onSaved={(p) => {
          upsertModelProfile(p)
          setEditOpen(false)
        }}
      />

      {/* Delete confirmation */}
      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除模型配置</DialogTitle>
            <DialogDescription>
              确定要删除「{profiles[deleteTargetId ?? '']?.name}」吗？
              {profiles[deleteTargetId ?? '']?.isDefault && '该配置是默认模型，删除后将自动选择最早的配置作为默认。'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTargetId(null)}>取消</Button>
            <Button variant="default" className="bg-destructive hover:bg-destructive/90" onClick={() => void handleDelete()} disabled={deleting}>
              {deleting ? '删除中...' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function EditDialog({
  open,
  onOpenChange,
  profile,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  profile: ModelProfile | null
  onSaved: (profile: ModelProfile) => void
}) {
  const [name, setName] = useState('')
  const [provider, setProvider] = useState<ModelProvider>('deepseek')
  const [modelId, setModelId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiBaseUrl, setApiBaseUrl] = useState('')
  const [isDefault, setIsDefault] = useState(false)
  const [supportsVision, setSupportsVision] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setError(null)
      if (profile) {
        setName(profile.name)
        setProvider(profile.provider)
        setModelId(profile.modelId)
        setApiKey('')
        setApiBaseUrl(profile.apiBaseUrl ?? '')
        setIsDefault(profile.isDefault)
        setSupportsVision(profile.supportsVision)
      } else {
        setName('')
        setProvider('deepseek')
        setModelId('')
        setApiKey('')
        setApiBaseUrl('')
        setIsDefault(false)
        setSupportsVision(false)
      }
    }
  }, [open, profile])

  const handleSave = async () => {
    setError(null)
    const trimmedName = name.trim()
    const trimmedModelId = modelId.trim()
    if (!trimmedName || !trimmedModelId) {
      setError('名称和模型 ID 不能为空')
      return
    }
    setSaving(true)
    try {
      const body = {
        name: trimmedName,
        provider,
        modelId: trimmedModelId,
        apiKey: apiKey.trim() || undefined,
        apiBaseUrl: apiBaseUrl.trim() || undefined,
        isDefault,
        supportsVision,
      }
      const saved = profile
        ? await updateModelProfile(profile.id, body)
        : await createModelProfile(body)
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{profile ? '编辑模型配置' : '新建模型配置'}</DialogTitle>
          <DialogDescription>
            模型配置独立于 Agent 管理，可在输入栏按消息切换。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
          <div>
            <label className="text-xs font-medium text-muted-foreground">名称</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：DeepSeek Chat / Claude Pro"
              className="mt-1"
              maxLength={64}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Provider</label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value as ModelProvider)}
              className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm outline-none focus:border-foreground/30"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">模型 ID</label>
            <Input
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="如：deepseek-chat / claude-opus-4-7"
              className="mt-1"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              API Key {profile && '(留空则不修改)'}
            </label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={profile ? `••••${profile.apiKeyLast4 ?? ''}` : 'sk-...'}
              className="mt-1"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">API Base URL (可选)</label>
            <Input
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              placeholder="https://api.deepseek.com/v1"
              className="mt-1"
            />
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
                className="size-4 rounded"
              />
              <span>设为默认</span>
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={supportsVision}
                onChange={(e) => setSupportsVision(e.target.checked)}
                className="size-4 rounded"
              />
              <span>支持视觉</span>
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
