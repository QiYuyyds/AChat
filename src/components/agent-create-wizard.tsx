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
          {/* Draft summary — premium card with gradient wash + ambient glow */}
          <div className="agent-fade-up relative overflow-hidden rounded-lg border border-primary/20 bg-primary/[0.03] px-4 py-3.5">
            <div className="agent-ambient pointer-events-none absolute -right-8 -top-8 size-28 rounded-full bg-primary/[0.06] blur-2xl" />
            <div className="relative flex items-start gap-2.5">
              <div className="relative mt-0.5 shrink-0">
                <div className="pointer-events-none absolute inset-0 rounded-lg bg-primary/10 blur-md" />
                <Sparkles className="relative size-4 text-primary" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold tracking-tight">{draft.name}</div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">{draft.description}</div>
                {draft.capabilities.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {draft.capabilities.map((capability) => (
                      <span
                        key={capability}
                        className="rounded-full border border-primary/15 bg-primary/[0.06] px-2 py-0.5 text-[10px] font-medium text-primary/80 transition-colors duration-300 hover:border-primary/30 hover:bg-primary/10"
                      >
                        {capability}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Model + Vision — gapless bento */}
          <div className="agent-fade-up-delay-1 grid grid-flow-row-dense grid-cols-2 gap-2">
            <div className="group relative overflow-hidden rounded-lg border border-border/40 bg-card px-3 py-2 transition-all duration-500 hover:border-primary/20">
              <div className="pointer-events-none absolute -right-4 -top-4 size-12 rounded-full bg-chart-1/[0.06] blur-xl transition-opacity duration-500 group-hover:opacity-100 opacity-60" />
              <div className="relative text-[10px] font-medium tracking-wide text-muted-foreground uppercase">模型</div>
              <div className="relative mt-1 text-xs font-semibold">
                {providerLabel} / {draft.modelId ?? 'SDK 默认'}
              </div>
            </div>
            <div className="group relative overflow-hidden rounded-lg border border-border/40 bg-card px-3 py-2 transition-all duration-500 hover:border-primary/20">
              <div className="pointer-events-none absolute -right-4 -top-4 size-12 rounded-full bg-chart-4/[0.06] blur-xl transition-opacity duration-500 group-hover:opacity-100 opacity-60" />
              <div className="relative text-[10px] font-medium tracking-wide text-muted-foreground uppercase">视觉</div>
              <div className="relative mt-1 text-xs font-semibold">
                {draft.supportsVision ? '默认开启' : '默认关闭'}
              </div>
            </div>
          </div>

          {/* Tool permissions */}
          <section className="agent-fade-up-delay-2 overflow-hidden rounded-lg border border-border/40 bg-card px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-semibold tracking-tight">
              <Wrench className="size-3.5 text-primary/60" />
              工具权限
            </div>
            {draft.toolPermissionSummaries.length > 0 ? (
              <div className="mt-2 space-y-1.5">
                {draft.toolPermissionSummaries.map((tool) => (
                  <div key={tool.toolName} className="flex items-start gap-2 text-[11px]">
                    <code className="mt-0.5 shrink-0 rounded border border-border/40 bg-muted/30 px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
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
                SDK adapter 使用运行时内置工具集，不保存 AChat 自定义 toolNames。
              </div>
            )}
          </section>

          {/* Assumptions */}
          <section className="agent-fade-up-delay-3 overflow-hidden rounded-lg border border-border/40 bg-card px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-semibold tracking-tight">
              <ShieldCheck className="size-3.5 text-primary/60" />
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

          {/* System Prompt */}
          <section className="agent-fade-up-delay-4 overflow-hidden rounded-lg border border-border/40 bg-card px-3 py-2.5">
            <div className="text-xs font-semibold tracking-tight">System Prompt</div>
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-border/30 bg-muted/20 p-2.5 font-mono text-[10px] leading-4 text-muted-foreground">
              {draft.systemPrompt}
            </pre>
          </section>
        </div>

        {error && (
          <div className="shrink-0 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
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
        {/* Premium header card with gradient + ambient glow */}
        <div className="agent-fade-up relative overflow-hidden rounded-lg border border-border/40 bg-muted/20 px-4 py-3.5">
          <div className="agent-ambient pointer-events-none absolute -right-6 -top-6 size-24 rounded-full bg-primary/[0.05] blur-2xl" />
          <div className="relative flex items-start gap-2.5">
            <div className="relative mt-0.5 shrink-0">
              <div className="pointer-events-none absolute inset-0 rounded-lg bg-primary/10 blur-md" />
              <MessageSquareText className="relative size-4 text-primary" />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight">描述你想要的 Agent</div>
              <div className="mt-1 text-xs leading-5 text-muted-foreground">
                说明它负责什么、常见输入是什么、希望它交付什么结果。系统会生成一份可确认的配置草稿。
              </div>
            </div>
          </div>
        </div>

        <div className="agent-fade-up-delay-1 space-y-2">
          <Textarea
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="例：我想要一个能帮我审查本地代码、运行测试并指出风险的 Agent"
            className="min-h-[140px] text-sm transition-shadow duration-300 focus-visible:ring-primary/40"
          />
          <Textarea
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
            placeholder="可选：补充模型偏好、权限边界、输出风格或不希望它做的事"
            className="min-h-[80px] text-sm transition-shadow duration-300 focus-visible:ring-primary/40"
          />
        </div>
      </div>

      {error && (
        <div className="shrink-0 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
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
