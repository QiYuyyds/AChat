'use client'

import {
  ArrowLeft,
  Check,
  Loader2,
  MessageSquareText,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { createAgentDraft } from '@/lib/api'
import {
  AGENT_BUILDER_PROVIDER_DEFAULTS,
  type AgentConfigDraft,
} from '@/shared/agent-builder-config'

interface AgentCreateWizardProps {
  onBack: () => void
  onCancel: () => void
  onEditDetails: (draft: AgentConfigDraft) => void
  onCreate: (draft: AgentConfigDraft) => Promise<void>
  creating: boolean
}

export function AgentCreateWizard({
  onBack,
  onCancel,
  onEditDetails,
  onCreate,
  creating,
}: AgentCreateWizardProps) {
  const [intent, setIntent] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [draft, setDraft] = useState<AgentConfigDraft | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const generateDraft = async () => {
    const trimmedIntent = intent.trim()
    if (trimmedIntent.length < 6) {
      setError('请稍微多描述一点你想创建的 Agent。')
      return
    }

    setGenerating(true)
    setError(null)
    try {
      const next = await createAgentDraft({
        intent: trimmedIntent,
        followUp: followUp.trim() || undefined,
      })
      setDraft(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setGenerating(false)
    }
  }

  const createFromDraft = async () => {
    if (!draft) return
    setError(null)
    try {
      await onCreate(draft)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  if (draft) {
    const providerLabel = draft.modelProvider
      ? AGENT_BUILDER_PROVIDER_DEFAULTS[draft.modelProvider].label
      : 'SDK 默认'

    return (
      <div className="flex min-h-0 flex-col gap-3">
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          <div className="rounded-md border bg-muted/20 px-3 py-3">
            <div className="flex items-start gap-2">
              <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="min-w-0">
                <div className="text-sm font-medium">{draft.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">{draft.description}</div>
                {draft.capabilities.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {draft.capabilities.map((capability) => (
                      <span
                        key={capability}
                        className="rounded bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground ring-1 ring-foreground/10"
                      >
                        {capability}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-md border px-3 py-2">
              <div className="text-[10px] text-muted-foreground">模型</div>
              <div className="mt-1 text-xs font-medium">
                {providerLabel} / {draft.modelId ?? 'SDK 默认'}
              </div>
            </div>
            <div className="rounded-md border px-3 py-2">
              <div className="text-[10px] text-muted-foreground">视觉</div>
              <div className="mt-1 text-xs font-medium">
                {draft.supportsVision ? '默认开启' : '默认关闭'}
              </div>
            </div>
          </div>

          <section className="rounded-md border px-3 py-2">
            <div className="flex items-center gap-1.5 text-xs font-medium">
              <Wrench className="size-3.5" />
              工具权限
            </div>
            {draft.toolPermissionSummaries.length > 0 ? (
              <div className="mt-2 space-y-1.5">
                {draft.toolPermissionSummaries.map((tool) => (
                  <div key={tool.toolName} className="flex items-start gap-2 text-[11px]">
                    <code className="mt-0.5 shrink-0 rounded bg-muted px-1 font-mono text-[9px] text-muted-foreground">
                      {tool.toolName}
                    </code>
                    <div className="min-w-0">
                      <span className="font-medium">{tool.label}</span>
                      <span className="text-muted-foreground"> · {tool.desc}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-2 text-[11px] text-muted-foreground">
                SDK adapter 使用运行时内置工具集，不保存 AgentHub 自定义 toolNames。
              </div>
            )}
          </section>

          <section className="rounded-md border px-3 py-2">
            <div className="flex items-center gap-1.5 text-xs font-medium">
              <ShieldCheck className="size-3.5" />
              默认假设
            </div>
            <div className="mt-2 space-y-1.5">
              {draft.assumptions.map((assumption) => (
                <div key={assumption.label} className="text-[11px]">
                  <span className="font-medium">{assumption.label}</span>
                  <span className="text-muted-foreground"> · {assumption.detail}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-md border px-3 py-2">
            <div className="text-xs font-medium">System Prompt</div>
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 text-muted-foreground">
              {draft.systemPrompt}
            </pre>
          </section>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-200">
            {error}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft className="size-4" />
            返回
          </Button>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={() => void generateDraft()} disabled={generating || creating}>
              {generating ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              重新生成
            </Button>
            <Button variant="outline" onClick={() => onEditDetails(draft)} disabled={creating}>
              <Pencil className="size-4" />
              编辑详细配置
            </Button>
            <Button onClick={() => void createFromDraft()} disabled={creating || generating}>
              {creating ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
              创建
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        <div className="rounded-md border bg-muted/20 px-3 py-3">
          <div className="flex items-start gap-2">
            <MessageSquareText className="mt-0.5 size-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <div className="text-sm font-medium">描述你想要的 Agent</div>
              <div className="mt-1 text-xs text-muted-foreground">
                说明它负责什么、常见输入是什么、希望它交付什么结果。系统会生成一份可确认的配置草稿。
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Textarea
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="例：我想要一个能帮我审查本地代码、运行测试并指出风险的 Agent"
            className="min-h-[140px] text-sm"
          />
          <Textarea
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
            placeholder="可选：补充模型偏好、权限边界、输出风格或不希望它做的事"
            className="min-h-[80px] text-sm"
          />
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-200">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="size-4" />
          返回
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={() => void generateDraft()} disabled={generating}>
            {generating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            生成草稿
          </Button>
        </div>
      </div>
    </div>
  )
}
