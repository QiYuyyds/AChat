'use client'

import { CheckCircle2, ChevronRight, Coins, Loader2, Pencil, Plus, Star, Trash2, XCircle, Zap } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
import type { CacheStyle, ModelProfile, ModelProvider } from '@/shared/types'
import type { UsageSummary } from '@/lib/api'

const PROVIDERS: { value: ModelProvider; label: string }[] = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'volcano-ark', label: '火山方舟 (豆包)' },
  { value: 'openai-compatible', label: 'OpenAI-compatible' },
]

const PROVIDER_DOT: Record<ModelProvider, string> = {
  deepseek: 'bg-blue-500/40',
  anthropic: 'bg-orange-500/40',
  openai: 'bg-green-500/40',
  'volcano-ark': 'bg-purple-500/40',
  'openai-compatible': 'bg-gray-500/40',
}

const CACHE_STYLE_LABELS: Record<CacheStyle, string> = {
  deepseek: 'DeepSeek 风格',
  anthropic: 'Anthropic 风格',
  none: '不支持缓存',
}

interface TestState {
  status: string
  latencyMs?: number
  error?: string
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
  const [testResult, setTestResult] = useState<Record<string, TestState | undefined>>({})
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set())
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

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

  const modelUsageMap = useMemo(() => {
    const map = new Map<string, number>()
    if (usageSummary) {
      for (const m of usageSummary.byModel) {
        map.set(m.model, m.totalTokens)
      }
    }
    return map
  }, [usageSummary])

  const stats = useMemo(() => {
    const passed = profileList.filter((p) => testResult[p.id]?.status === 'ok' || p.lastTestStatus === 'ok').length
    const untested = profileList.filter(
      (p) => !testResult[p.id] && p.lastTestStatus === 'untested',
    ).length
    return { total: profileList.length, passed, untested }
  }, [profileList, testResult])

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
      setExpandedId(null)
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[ModelConfig] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="agent-fade-up flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <h2 className="text-base font-medium">模型配置</h2>
          <span className="text-xs tabular-nums text-muted-foreground">
            {stats.total} 个配置
          </span>
          {stats.total > 0 && (
            <div className="hidden items-center gap-2 text-xs tabular-nums text-muted-foreground sm:flex">
              <span className="text-border">|</span>
              <span className="text-success">{stats.passed} 已通过</span>
              {stats.untested > 0 && (
                <>
                  <span className="text-border">|</span>
                  <span>{stats.untested} 待测试</span>
                </>
              )}
            </div>
          )}
        </div>
        <Button
          size="sm"
          className="group/add gap-1.5 overflow-hidden shadow-[var(--shadow-sm),var(--inset-hi)] transition-all duration-200 hover:shadow-[var(--shadow-md),var(--inset-hi)] hover:brightness-110 active:scale-[0.97]"
          onClick={() => { setEditingProfile(null); setEditOpen(true) }}
        >
          <span className="relative flex size-3.5 items-center justify-center">
            <Plus className="size-3.5 transition-transform duration-300 group-hover/add:rotate-90" />
          </span>
          添加配置
        </Button>
      </div>

      {/* List */}
      {loading && profileList.length === 0 ? (
        <div className="agent-fade-up agent-fade-up-delay-1 space-y-1.5">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg bg-muted/40" />
          ))}
        </div>
      ) : profileList.length === 0 ? (
        <EmptyState
          onCTA={() => { setEditingProfile(null); setEditOpen(true) }}
          delayClass="agent-fade-up agent-fade-up-delay-1"
        />
      ) : (
        <div className="agent-fade-up agent-fade-up-delay-1 overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow-sm)]">
          <div className="divide-y divide-border">
            {profileList.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                expanded={expandedId === p.id}
                onToggle={() => setExpandedId(expandedId === p.id ? null : p.id)}
                onEdit={() => { setEditingProfile(p); setEditOpen(true) }}
                onDelete={() => setDeleteTargetId(p.id)}
                onTest={() => void handleTest(p.id)}
                onSetDefault={() => void handleSetDefault(p.id)}
                testing={testingIds.has(p.id)}
                testResult={testResult[p.id]}
                usage={modelUsageMap.get(p.modelId) ?? 0}
              />
            ))}
          </div>
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

function ProfileRow({
  profile: p,
  expanded,
  onToggle,
  onEdit,
  onDelete,
  onTest,
  onSetDefault,
  testing,
  testResult,
  usage,
}: {
  profile: ModelProfile
  expanded: boolean
  onToggle: () => void
  onEdit: () => void
  onDelete: () => void
  onTest: () => void
  onSetDefault: () => void
  testing: boolean
  testResult?: TestState
  usage: number
}) {
  const providerLabel = PROVIDERS.find((x) => x.value === p.provider)?.label ?? p.provider
  const dotColor = PROVIDER_DOT[p.provider] ?? 'bg-gray-500/40'

  return (
    <div>
      {/* Collapsed row */}
      <button
        type="button"
        onClick={onToggle}
        className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-muted/30"
      >
        {/* Provider dot */}
        <span className={cn('size-1.5 shrink-0 rounded-full', dotColor)} />

        {/* Name + default badge */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{p.name}</span>
            {p.isDefault && (
              <span className="shrink-0 rounded-4xl bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                默认
              </span>
            )}
          </div>
        </div>

        {/* Provider label */}
        <span className="hidden shrink-0 font-mono text-xs text-muted-foreground/60 sm:block">
          {providerLabel}
        </span>

        {/* Test status */}
        <TestStatusPill testing={testing} result={testResult} lastTestStatus={p.lastTestStatus} />

        {/* Usage */}
        {usage > 0 && (
          <span className="hidden shrink-0 items-center gap-1 font-mono text-[11px] tabular-nums text-muted-foreground/60 md:flex">
            <Coins className="size-2.5" />
            {formatTok(usage)}
          </span>
        )}

        {/* Chevron */}
        <ChevronRight
          className={cn(
            'size-4 shrink-0 text-muted-foreground/40 transition-transform duration-200',
            expanded && 'rotate-90',
          )}
        />
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-border bg-muted/20 px-4 py-3">
          {/* Test error */}
          {testResult?.status === 'fail' && testResult.error && (
            <div className="mb-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {testResult.error}
            </div>
          )}

          {/* Technical details */}
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-xs">
            <dt className="font-medium text-muted-foreground">Model</dt>
            <dd className="truncate font-mono">{p.modelId}</dd>

            <dt className="font-medium text-muted-foreground">Provider</dt>
            <dd>{providerLabel}</dd>

            {p.apiKeyLast4 && (
              <>
                <dt className="font-medium text-muted-foreground">API Key</dt>
                <dd className="font-mono">{'\u2022\u2022\u2022\u2022'}{p.apiKeyLast4}</dd>
              </>
            )}

            {p.apiBaseUrl && (
              <>
                <dt className="font-medium text-muted-foreground">Base URL</dt>
                <dd className="truncate font-mono">{p.apiBaseUrl}</dd>
              </>
            )}

            <dt className="font-medium text-muted-foreground">视觉</dt>
            <dd>{p.supportsVision ? '支持' : '不支持'}</dd>

            {p.provider === 'openai-compatible' && (
              <>
                <dt className="font-medium text-muted-foreground">Cache</dt>
                <dd className="font-mono">
                  {p.cacheStyle === null ? '自动探测' : CACHE_STYLE_LABELS[p.cacheStyle]}
                  {p.detectedCacheStyle && (
                    <span className="text-muted-foreground"> (检测: {CACHE_STYLE_LABELS[p.detectedCacheStyle]})</span>
                  )}
                </dd>
              </>
            )}
          </dl>

          {/* Actions */}
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={onTest} disabled={testing} className="h-7 gap-1.5 text-xs">
              {testing ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Zap className="size-3.5" />
              )}
              测试连通性
            </Button>
            {!p.isDefault && (
              <Button size="sm" variant="ghost" onClick={onSetDefault} className="h-7 gap-1.5 text-xs">
                <Star className="size-3.5" />
                设为默认
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={onEdit} className="h-7 gap-1.5 text-xs">
              <Pencil className="size-3.5" />
              编辑
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onDelete}
              className="h-7 gap-1.5 text-xs text-destructive hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
              删除
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function TestStatusPill({
  testing,
  result,
  lastTestStatus,
}: {
  testing: boolean
  result?: TestState
  lastTestStatus: 'untested' | 'ok' | 'fail'
}) {
  if (testing) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
        测试中
      </span>
    )
  }
  if (result) {
    return result.status === 'ok' ? (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-success">
        <CheckCircle2 className="size-3" />
        <span className="tabular-nums">{result.latencyMs}ms</span>
      </span>
    ) : (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-destructive">
        <XCircle className="size-3" />
        失败
      </span>
    )
  }
  if (lastTestStatus === 'ok') {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-success">
        <CheckCircle2 className="size-3" />
        已通过
      </span>
    )
  }
  if (lastTestStatus === 'fail') {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-destructive">
        <XCircle className="size-3" />
        未通过
      </span>
    )
  }
  return (
    <span className="shrink-0 text-[11px] text-muted-foreground/50">
      未测试
    </span>
  )
}

function EmptyState({
  onCTA,
  delayClass,
}: {
  onCTA: () => void
  delayClass: string
}) {
  return (
    <div className={cn(delayClass, 'flex flex-col items-center gap-4 rounded-xl border border-dashed border-border py-20 text-center')}>
      <div className="flex size-14 items-center justify-center rounded-2xl bg-primary/5">
        <Zap className="size-7 text-primary/60" />
      </div>
      <div className="space-y-1">
        <h3 className="text-balance text-sm font-medium">还没有模型配置</h3>
        <p className="text-pretty text-xs text-muted-foreground">
          添加你的第一个模型配置档，Agent 运行时将按优先级自动解析
        </p>
      </div>
      <Button size="sm" className="gap-1.5" onClick={onCTA}>
        <Plus className="size-3.5" />
        添加配置
      </Button>
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
  const [cacheStyle, setCacheStyle] = useState<CacheStyle | null>(null)
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
        setCacheStyle(profile.cacheStyle ?? null)
      } else {
        setName('')
        setProvider('deepseek')
        setModelId('')
        setApiKey('')
        setApiBaseUrl('')
        setIsDefault(false)
        setSupportsVision(false)
        setCacheStyle(null)
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
        cacheStyle: provider === 'openai-compatible' ? cacheStyle : undefined,
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
              placeholder={profile ? `\u2022\u2022\u2022\u2022${profile.apiKeyLast4 ?? ''}` : 'sk-...'}
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
          {provider === 'openai-compatible' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Cache 语义
              </label>
              <div className="flex flex-wrap gap-2">
                {([
                  { value: null, label: '自动探测' },
                  { value: 'deepseek' as CacheStyle, label: 'DeepSeek 风格 (input 含 cache)' },
                  { value: 'anthropic' as CacheStyle, label: 'Anthropic 风格 (input 不含 cache)' },
                  { value: 'none' as CacheStyle, label: '不支持缓存' },
                ] as { value: CacheStyle | null; label: string }[]).map((opt) => (
                  <button
                    key={String(opt.value)}
                    type="button"
                    onClick={() => setCacheStyle(opt.value)}
                    className={cn(
                      'rounded-md border px-2.5 py-1 text-xs transition',
                      cacheStyle === opt.value
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border text-muted-foreground hover:border-foreground/30',
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {profile?.detectedCacheStyle && (
                <p className="text-[10px] text-muted-foreground">
                  上次自动检测: {profile.detectedCacheStyle}
                </p>
              )}
            </div>
          )}
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
