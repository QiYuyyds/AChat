'use client'

import { Cpu, MessageSquareText, SlidersHorizontal, Sparkles, User, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'

import { AgentCreateWizard } from '@/components/agent-create-wizard'
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
import { Textarea } from '@/components/ui/textarea'
import type { AgentRow } from '@/db/schema'
import {
  createAgent,
  listSkills,
  updateAgent,
  type CreateAgentBody,
  type SkillSummary,
  type UpdateAgentBody,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { pickRandomAgentIcon } from '@/shared/agent-icons'
import {
  AGENT_BUILDER_PROVIDER_DEFAULTS as PROVIDER_DEFAULTS,
  AGENT_TOOL_META as TOOL_META,
  AGENT_TOOL_PRESETS as TOOL_PRESETS,
  AVAILABLE_AGENT_TOOLS,
  CLAUDE_CODE_DEFAULT_MODEL,
  CODEX_DEFAULT_MODEL,
  DEFAULT_CUSTOM_AGENT_TOOLS,
  type AgentBuilderAdapter as AdapterKind,
  type AgentBuilderProvider as Provider,
  type AgentConfigDraft,
  type AgentToolName as ToolName,
} from '@/shared/agent-builder-config'
import { validateCodexBaseUrl } from '@/shared/codex-compat'
import {
  validateOpenAICompatibleApiKey,
  validateOpenAICompatibleBaseUrl,
} from '@/shared/openai-compatible'
import { useAppStore } from '@/stores/app-store'

type AgentTab = 'basic' | 'model' | 'toolsPrompt'
type CreateStep = 'choose' | 'wizard' | 'detail'

const DEFAULT_CUSTOM_SYSTEM_PROMPT = `你是一个 AChat custom agent。你的任务是理解用户目标，使用已启用的工具完成工作，并把结果清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；只有在用户提到附件、已有产物或工作区文件时，才调用对应读取工具。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出代码、网页、文档或设计稿时，优先用 write_artifact 创建结构化产物；网页产物完成后再调用 deploy_artifact。
5. 探索项目目录时优先用 fs_list，再用 fs_read 读取具体文件；使用 fs_write 或 bash 前确认确有必要，并只在当前 workspace 范围内操作。
6. 最终回复保持简洁，说明完成了什么、产物在哪里、还剩什么需要用户决策。`

/**
 * 创建 / 编辑 Agent 的对话框。
 *
 * 传入 `agent` 进入编辑模式，未传则为创建模式。两种模式公用同一套字段、
 * 同一套校验，只是 submit 路径与文案不同。
 */
export function CreateAgentDialog({
  open,
  onOpenChange,
  agent,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  agent?: AgentRow
}) {
  const upsertAgent = useAppStore((s) => s.upsertAgent)
  const isEdit = !!agent

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [capabilitiesText, setCapabilitiesText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [adapterKind, setAdapterKind] = useState<AdapterKind>('custom')
  const [provider, setProvider] = useState<Provider>('deepseek')
  const [modelId, setModelId] = useState(PROVIDER_DEFAULTS.deepseek.defaultModel)
  const [toolNames, setToolNames] = useState<Set<string>>(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))
  const [skillNames, setSkillNames] = useState<Set<string>>(new Set())
  const [availableSkills, setAvailableSkills] = useState<SkillSummary[]>([])
  const [supportsVision, setSupportsVision] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [apiBaseUrl, setApiBaseUrl] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [isOrchestrator, setIsOrchestrator] = useState(false)
  const [executablePath, setExecutablePath] = useState('')
  const [customArgsText, setCustomArgsText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<AgentTab>('basic')
  const [createStep, setCreateStep] = useState<CreateStep>('choose')

  // 每次打开 / 切换 agent 时，重置表单到该 agent 的当前值（或创建态的默认）。
  useEffect(() => {
    if (!open) return
    if (agent) {
      const kind: AdapterKind =
        agent.adapterName === 'claude-code'
          ? 'claude-code'
          : agent.adapterName === 'codex'
            ? 'codex'
            : 'custom'
      setAdapterKind(kind)
      setName(agent.name)
      setDescription(agent.description)
      setCapabilitiesText(agent.capabilities.join(', '))
      setSystemPrompt(agent.systemPrompt)
      const p = (agent.modelProvider ?? 'deepseek') as Provider
      setProvider(p)
      setModelId(
        agent.modelId ??
          (kind === 'claude-code'
            ? CLAUDE_CODE_DEFAULT_MODEL
            : kind === 'codex'
              ? CODEX_DEFAULT_MODEL
              : PROVIDER_DEFAULTS[p].defaultModel),
      )
      setToolNames(new Set(agent.toolNames))
      setSkillNames(new Set(agent.skillNames))
      setSupportsVision(agent.supportsVision)
      setIsOrchestrator(agent.isOrchestrator)
      setApiKey(agent.apiKey ?? '')
      setApiBaseUrl(agent.apiBaseUrl ?? '')
      setExecutablePath((agent as any).executablePath ?? '')
      setCustomArgsText(((agent as any).customArgs ?? []).join('\n'))
    } else {
      setAdapterKind('custom')
      setName('')
      setDescription('')
      setCapabilitiesText('')
      setSystemPrompt(DEFAULT_CUSTOM_SYSTEM_PROMPT)
      setProvider('deepseek')
      setModelId(PROVIDER_DEFAULTS.deepseek.defaultModel)
      setToolNames(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))
      setSkillNames(new Set())
      setSupportsVision(true)
      setIsOrchestrator(false)
      setApiKey('')
      setApiBaseUrl('')
      setExecutablePath('')
      setCustomArgsText('')
      setCreateStep('choose')
    }
    if (agent) setCreateStep('detail')
    setShowApiKey(false)
    setError(null)
    setActiveTab('basic')
  }, [open, agent])

  // 打开对话框时加载可用 skills（custom adapter 才会用到）。
  useEffect(() => {
    if (!open) return
    listSkills()
      .then(setAvailableSkills)
      .catch((err) => console.error('[CreateAgentDialog] load skills failed', err))
  }, [open])

  const handleOrchestratorChange = (checked: boolean) => {
    setIsOrchestrator(checked)
    if (checked) {
      // Auto-merge plan_tasks and ask_user into toolNames
      setToolNames((prev) => {
        const next = new Set(prev)
        next.add('plan_tasks')
        next.add('ask_user')
        return next
      })
    }
    // When unchecked, do NOT auto-remove tools (user may need them for other purposes)
  }

  const handleAdapterKindChange = (kind: AdapterKind) => {
    setAdapterKind(kind)
    if (kind === 'claude-code') {
      setModelId(CLAUDE_CODE_DEFAULT_MODEL)
    } else if (kind === 'codex') {
      setModelId(CODEX_DEFAULT_MODEL)
    } else {
      setModelId(PROVIDER_DEFAULTS[provider].defaultModel)
      setToolNames((prev) => (prev.size === 0 ? new Set(DEFAULT_CUSTOM_AGENT_TOOLS) : prev))
      setSystemPrompt((prev) => (prev.trim() ? prev : DEFAULT_CUSTOM_SYSTEM_PROMPT))
    }
  }

  const handleProviderChange = (p: Provider) => {
    setProvider(p)
    // 切换 provider 时把 modelId 自动重置到该 provider 的默认（避免跨家串）
    setModelId(PROVIDER_DEFAULTS[p].defaultModel)
  }

  const toggleTool = (t: string) => {
    setToolNames((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }

  const toggleSkill = (s: string) => {
    setSkillNames((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  }

  const applyToolPreset = (tools: readonly ToolName[]) => {
    setToolNames(new Set(tools))
  }

  const isPresetActive = (tools: readonly ToolName[]) =>
    toolNames.size === tools.length && tools.every((toolName) => toolNames.has(toolName))

  const applyDraftToForm = (draft: AgentConfigDraft) => {
    const kind = draft.adapterName
    const p = draft.modelProvider ?? 'deepseek'
    setAdapterKind(kind)
    setName(draft.name)
    setDescription(draft.description)
    setCapabilitiesText(draft.capabilities.join(', '))
    setSystemPrompt(draft.systemPrompt)
    setProvider(p)
    setModelId(
      draft.modelId ??
        (kind === 'claude-code'
          ? CLAUDE_CODE_DEFAULT_MODEL
          : kind === 'codex'
            ? CODEX_DEFAULT_MODEL
            : PROVIDER_DEFAULTS[p].defaultModel),
    )
    setToolNames(new Set(draft.toolNames))
    setSkillNames(new Set())
    setSupportsVision(draft.supportsVision)
    setIsOrchestrator(false)
    setApiKey('')
    setApiBaseUrl('')
    setShowApiKey(false)
    setError(null)
    setActiveTab('basic')
  }

  const editDraftDetails = (draft: AgentConfigDraft) => {
    applyDraftToForm(draft)
    setCreateStep('detail')
  }

  const createFromDraft = async (draft: AgentConfigDraft) => {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const isSdkAgent = draft.adapterName === 'claude-code' || draft.adapterName === 'codex'
      const body: CreateAgentBody = {
        name: draft.name.trim(),
        avatar: pickRandomAgentIcon(),
        description: draft.description.trim(),
        capabilities: draft.capabilities,
        systemPrompt: draft.systemPrompt.trim(),
        adapterName: draft.adapterName,
        modelProvider: isSdkAgent ? undefined : draft.modelProvider,
        modelId: draft.modelId?.trim() || undefined,
        toolNames: isSdkAgent ? [] : draft.toolNames,
        skillNames: [],
        supportsVision: draft.supportsVision,
        isOrchestrator: isOrchestrator || undefined,
        executablePath: undefined,
        protocolFamily: isSdkAgent ? draft.adapterName : undefined,
        customArgs: undefined,
      }
      const created = await createAgent(body)
      upsertAgent(created)
      onOpenChange(false)
    } catch (err) {
      const nextError = err instanceof Error ? err : new Error(String(err))
      setError(nextError.message)
      throw nextError
    } finally {
      setSubmitting(false)
    }
  }

  const submit = async () => {
    if (submitting) return
    setError(null)

    const trimmed = name.trim()
    const fail = (tab: AgentTab, msg: string) => {
      setActiveTab(tab)
      setError(msg)
    }
    if (!trimmed) return fail('basic', '名称不能为空')
    if (!description.trim()) return fail('basic', '描述不能为空')
    if (!systemPrompt.trim()) return fail('toolsPrompt', 'System Prompt 不能为空')
    if (adapterKind === 'custom' && !modelId.trim()) return fail('model', 'Custom adapter 必须填写 Model ID')
    const trimmedApiBaseUrl = apiBaseUrl.trim()
    const trimmedApiKey = apiKey.trim()
    if (adapterKind === 'codex') {
      const baseUrlError = validateCodexBaseUrl(trimmedApiBaseUrl || null)
      if (baseUrlError) return fail('model', baseUrlError)
    }
    if (adapterKind === 'custom') {
      const baseUrlError = validateOpenAICompatibleBaseUrl(provider, trimmedApiBaseUrl || null)
      if (baseUrlError) return fail('model', baseUrlError)
      const apiKeyError = validateOpenAICompatibleApiKey(provider, trimmedApiKey || null)
      if (apiKeyError) return fail('model', apiKeyError)
    }

    const capabilities = capabilitiesText
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)

    setSubmitting(true)
    try {
      const isClaudeCode = adapterKind === 'claude-code'
      const isCodex = adapterKind === 'codex'
      const isSdkAgent = isClaudeCode || isCodex
      if (isEdit && agent) {
        const patch: UpdateAgentBody = {
          name: trimmed,
          description: description.trim(),
          capabilities,
          systemPrompt: systemPrompt.trim(),
          adapterName: adapterKind,
          modelProvider: isSdkAgent ? undefined : provider,
          modelId: isSdkAgent ? modelId.trim() || null : modelId.trim(),
          toolNames: isSdkAgent ? [] : Array.from(toolNames),
          skillNames: isSdkAgent ? [] : Array.from(skillNames),
          supportsVision,
          isOrchestrator,
          apiKey: trimmedApiKey || null,
          apiBaseUrl: trimmedApiBaseUrl || null,
          executablePath: isSdkAgent ? (executablePath.trim() || null) : null,
          protocolFamily: isSdkAgent ? adapterKind : null,
          customArgs: isSdkAgent ? (customArgsText.trim() ? customArgsText.split('\n').map(s => s.trim()).filter(Boolean) : []) : [],
        }
        const updated = await updateAgent(agent.id, patch)
        upsertAgent(updated)
      } else {
        const body: CreateAgentBody = {
          name: trimmed,
          avatar: pickRandomAgentIcon(),
          description: description.trim(),
          capabilities,
          systemPrompt: systemPrompt.trim(),
          adapterName: adapterKind,
          modelProvider: isSdkAgent ? undefined : provider,
          modelId: modelId.trim() || undefined,
          toolNames: isSdkAgent ? [] : Array.from(toolNames),
          skillNames: isSdkAgent ? [] : Array.from(skillNames),
          supportsVision,
          isOrchestrator: isOrchestrator || undefined,
          apiKey: trimmedApiKey || undefined,
          apiBaseUrl: trimmedApiBaseUrl || undefined,
          executablePath: isSdkAgent ? (executablePath.trim() || undefined) : undefined,
          protocolFamily: isSdkAgent ? adapterKind : undefined,
          customArgs: isSdkAgent ? (customArgsText.trim() ? customArgsText.split('\n').map(s => s.trim()).filter(Boolean) : []) : undefined,
        }
        const created = await createAgent(body)
        upsertAgent(created)
      }
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const showDetailForm = isEdit || createStep === 'detail'
  const descriptionText = isEdit
    ? '修改这个 Agent 的配置。保存后立即生效，已存在的会话也会用新配置回复。'
    : createStep === 'choose'
      ? '选择创建方式。可以先用描述生成草稿，也可以直接进入完整配置。'
      : createStep === 'wizard'
        ? '通过描述生成一份可确认的 Agent 配置草稿。'
        : '为这个 Agent 设定身份与能力。它会出现在新建对话的选择列表里。'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100vh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑 Agent' : '创建 Agent'}</DialogTitle>
          <DialogDescription>{descriptionText}</DialogDescription>
        </DialogHeader>

        {!showDetailForm ? (
          createStep === 'choose' ? (
            <CreateModeChoice
              onConversational={() => setCreateStep('wizard')}
              onDetailed={() => setCreateStep('detail')}
              onCancel={() => onOpenChange(false)}
            />
          ) : (
            <AgentCreateWizard
              onBack={() => {
                setError(null)
                setCreateStep('choose')
              }}
              onCancel={() => onOpenChange(false)}
              onEditDetails={editDraftDetails}
              onCreate={createFromDraft}
              creating={submitting}
            />
          )
        ) : (
        <div className="flex min-h-0 flex-col gap-2">
          <Tabs
            value={activeTab}
            onValueChange={(v) => setActiveTab(v as AgentTab)}
            className="flex min-h-0 flex-1 flex-col gap-3"
          >
            <TabsList className="self-start">
              <TabsTrigger value="basic">
                <User className="size-3.5" />
                基本信息
              </TabsTrigger>
              <TabsTrigger value="model">
                <Cpu className="size-3.5" />
                模型与适配器
              </TabsTrigger>
              <TabsTrigger value="toolsPrompt">
                <Wrench className="size-3.5" />
                工具与提示词
              </TabsTrigger>
              <TabsTrigger value="skills">
                <Sparkles className="size-3.5" />
                技能
              </TabsTrigger>
            </TabsList>

            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <TabsContent value="basic" className="mt-0 space-y-3 py-1">
                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label required>名称</Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="例：TestBot"
                  />
                </div>

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label required>描述</Label>
                  <Input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="一句话讲清楚它能做什么"
                  />
                </div>

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>能力标签</Label>
                  <div>
                    <Input
                      value={capabilitiesText}
                      onChange={(e) => setCapabilitiesText(e.target.value)}
                      placeholder="testing, react, vitest"
                    />
                    <div className="mt-1 text-[10px] text-muted-foreground">用逗号或空格分隔</div>
                  </div>
                </div>

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>角色</Label>
                  <label
                    className={cn(
                      'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                      isOrchestrator && 'border-primary bg-primary/5',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={isOrchestrator}
                      onChange={(e) => handleOrchestratorChange(e.target.checked)}
                      className="mt-0.5 accent-primary"
                    />
                    <div className="min-w-0">
                      <div className="text-xs font-medium">设为协调者 (Orchestrator)</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">
                        协调者负责群聊中的任务拆解与分派，会自动启用 <code className="font-mono">plan_tasks</code> 和 <code className="font-mono">ask_user</code> 工具。
                      </div>
                    </div>
                  </label>
                </div>
              </TabsContent>

              <TabsContent value="model" className="mt-0 space-y-3 py-1">
                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>适配器</Label>
                  <div className="flex flex-col gap-1.5">
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                        adapterKind === 'custom' && 'border-primary bg-primary/5',
                      )}
                    >
                      <input
                        type="radio"
                        name="adapterKind"
                        checked={adapterKind === 'custom'}
                        onChange={() => handleAdapterKindChange('custom')}
                        className="mt-0.5 accent-primary"
                      />
                      <div className="min-w-0">
                        <div className="text-xs font-medium">Custom Agent SDK</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          用 DeepSeek / OpenAI / 火山方舟 / 自定义 OpenAI-compatible API。可自定义工具集和模型。
                        </div>
                      </div>
                    </label>
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                        adapterKind === 'claude-code' && 'border-primary bg-primary/5',
                      )}
                    >
                      <input
                        type="radio"
                        name="adapterKind"
                        checked={adapterKind === 'claude-code'}
                        onChange={() => handleAdapterKindChange('claude-code')}
                        className="mt-0.5 accent-primary"
                      />
                      <div className="min-w-0">
                        <div className="text-xs font-medium">Claude Code SDK</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          用 @anthropic-ai/claude-agent-sdk，自带 Bash / Read / Write / Edit / Grep / Glob / WebFetch / Task 子 agent 等一整套工具。
                        </div>
                      </div>
                    </label>
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                        adapterKind === 'codex' && 'border-primary bg-primary/5',
                      )}
                    >
                      <input
                        type="radio"
                        name="adapterKind"
                        checked={adapterKind === 'codex'}
                        onChange={() => handleAdapterKindChange('codex')}
                        className="mt-0.5 accent-primary"
                      />
                      <div className="min-w-0">
                        <div className="text-xs font-medium">Codex SDK</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          用 @openai/codex-sdk，支持本地仓库读写、命令执行、线程续接和结构化事件流；需要 Codex/Responses 兼容后端。
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                {adapterKind === 'custom' ? (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>底层模型</Label>
                    <div className="flex gap-2">
                      <select
                        value={provider}
                        onChange={(e) => handleProviderChange(e.target.value as Provider)}
                        className="rounded-md border bg-background px-2 py-1.5 text-sm"
                      >
                        {(Object.keys(PROVIDER_DEFAULTS) as Provider[]).map((p) => (
                          <option key={p} value={p}>
                            {PROVIDER_DEFAULTS[p].label}
                          </option>
                        ))}
                      </select>
                      <Input
                        value={modelId}
                        onChange={(e) => setModelId(e.target.value)}
                        placeholder="model id"
                        className="flex-1 font-mono text-xs"
                      />
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>Model ID</Label>
                    <div>
                      <Input
                        value={modelId}
                        onChange={(e) => setModelId(e.target.value)}
                        placeholder={
                          adapterKind === 'claude-code' ? CLAUDE_CODE_DEFAULT_MODEL : CODEX_DEFAULT_MODEL
                        }
                        className="font-mono text-xs"
                      />
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        {adapterKind === 'claude-code' ? (
                          <>
                            Claude 模型 id，例 <code className="font-mono">claude-opus-4-7</code> /{' '}
                            <code className="font-mono">claude-sonnet-4-6</code>。留空走 SDK 默认。
                          </>
                        ) : (
                          <>
                            Codex 模型 id，例 <code className="font-mono">gpt-5-codex</code>。留空走 SDK 默认。
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {(adapterKind === 'claude-code' || adapterKind === 'codex') && (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>CLI 路径</Label>
                    <div>
                      <Input
                        value={executablePath}
                        onChange={(e) => setExecutablePath(e.target.value)}
                        placeholder={adapterKind === 'claude-code' ? 'claude（留空从 PATH 查找）' : 'codex（留空从 PATH 查找）'}
                        className="font-mono text-xs"
                      />
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        本机 {adapterKind === 'claude-code' ? 'Claude Code' : 'Codex'} CLI 的路径。留空则自动 PATH 查找。仅在非标准安装时需要填写。
                      </div>
                    </div>
                  </div>
                )}

                {(adapterKind === 'claude-code' || adapterKind === 'codex') && (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>CLI 参数</Label>
                    <div>
                      <Textarea
                        value={customArgsText}
                        onChange={(e) => setCustomArgsText(e.target.value)}
                        placeholder={'每行一个参数，例：\n--verbose\n--max-turns\n20'}
                        className="min-h-[60px] font-mono text-xs"
                      />
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        传给 {adapterKind === 'claude-code' ? 'claude' : 'codex'} CLI 的额外参数，每行一个。协议关键 flag（如 --output-format）会被自动过滤。
                      </div>
                    </div>
                  </div>
                )}

                {(adapterKind === 'claude-code' ||
                  adapterKind === 'codex' ||
                  (adapterKind === 'custom' && provider === 'openai-compatible')) && (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label required={adapterKind === 'custom' && provider === 'openai-compatible'}>Base URL</Label>
                    <div>
                      <Input
                        value={apiBaseUrl}
                        onChange={(e) => setApiBaseUrl(e.target.value)}
                        placeholder={
                          adapterKind === 'claude-code'
                            ? 'https://api.anthropic.com（默认）'
                            : adapterKind === 'codex'
                              ? 'https://api.openai.com/v1（默认，需支持 /responses）'
                              : 'https://dashscope.aliyuncs.com/compatible-mode/v1'
                        }
                        className="font-mono text-xs"
                      />
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        {adapterKind === 'claude-code' ? (
                          <>
                            指向第三方 Claude API 兼容网关（如 <code className="font-mono">https://anyrouter.top</code>）；留空走 Anthropic 官方 endpoint。配此项时下方 API Key 自动作为 <code className="font-mono">ANTHROPIC_AUTH_TOKEN</code> 传给 SDK。
                          </>
                        ) : adapterKind === 'codex' ? (
                          <>
                            必须指向 Codex/Responses 兼容 endpoint；DeepSeek / 火山方舟等 Chat Completions 兼容接口请用 Custom adapter。留空走 Codex SDK 默认 endpoint。
                          </>
                        ) : (
                          <>
                            必须指向 OpenAI Chat Completions 兼容 endpoint，例如通义千问 compatible-mode、智谱 / MiniMax / OpenRouter / SiliconFlow 的 OpenAI 兼容地址。
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>
                    {adapterKind === 'claude-code' && apiBaseUrl.trim() ? 'Auth Token' : 'API Key'}
                  </Label>
                  <div>
                    <div className="flex gap-2">
                      <Input
                        type={showApiKey ? 'text' : 'password'}
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        placeholder={
                          adapterKind === 'claude-code' && apiBaseUrl.trim()
                            ? '第三方网关的 token'
                            : adapterKind === 'codex' && apiBaseUrl.trim()
                              ? 'Codex/Responses endpoint token'
                              : adapterKind === 'custom' && provider === 'openai-compatible'
                                ? 'OpenAI-compatible endpoint token'
                              : '留空则使用环境变量'
                        }
                        className="flex-1 font-mono text-xs"
                        autoComplete="off"
                      />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setShowApiKey((v) => !v)}
                      >
                        {showApiKey ? '隐藏' : '显示'}
                      </Button>
                    </div>
                    <div className="mt-1 text-[10px] text-muted-foreground">
                      {adapterKind === 'claude-code' && apiBaseUrl.trim() ? (
                        <>填写后作为 <code className="font-mono">ANTHROPIC_AUTH_TOKEN</code> 传给 SDK，路由到自定义 Base URL；留空则透传空 token（第三方网关可能拒绝）</>
                      ) : adapterKind === 'codex' && apiBaseUrl.trim() ? (
                        <>填写后作为 <code className="font-mono">CODEX_API_KEY</code> 传给 SDK，路由到自定义 Codex/Responses Base URL；留空则走 AChat 设置或环境变量</>
                      ) : adapterKind === 'custom' && provider === 'openai-compatible' ? (
                        <>OpenAI-compatible provider 需要为该 agent 单独填写 API Key；不会使用全局 OpenAI / DeepSeek / 火山方舟 key。</>
                      ) : (
                        <>
                          填写后该 agent 优先用此 key；留空则 fallback 到{' '}
                          <code className="font-mono">
                            {adapterKind === 'claude-code'
                              ? 'ANTHROPIC_API_KEY 环境变量 / 本机 ~/.claude OAuth 登录态'
                              : adapterKind === 'codex'
                                ? 'OPENAI_API_KEY / CODEX_API_KEY 环境变量'
                              : provider === 'deepseek'
                                ? 'DEEPSEEK_API_KEY'
                                : provider === 'volcano-ark'
                                  ? 'ARK_API_KEY'
                                  : provider === 'openai'
                                    ? 'OPENAI_API_KEY'
                                    : provider === 'anthropic'
                                      ? 'ANTHROPIC_API_KEY'
                                      : '该 agent 的 API Key'}
                          </code>
                          {adapterKind === 'claude-code' || adapterKind === 'codex' ? '' : ' 环境变量'}
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>视觉</Label>
                  <label
                    className={cn(
                      'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                      supportsVision && 'border-primary bg-primary/5',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={supportsVision}
                      onChange={(e) => setSupportsVision(e.target.checked)}
                      className="mt-0.5 accent-primary"
                    />
                    <div className="min-w-0">
                      <div className="text-xs font-medium">该模型支持视觉（多模态）</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">
                        {adapterKind === 'codex'
                          ? '勾选后，发图片时会以本地图片输入传给 Codex SDK。模型不支持会被拒绝，请确认 modelId 支持视觉。'
                          : '勾选后，发图片时会以 base64 注入 messages.content。模型不支持会被 API 拒绝 (400)，请确认你填的 modelId 真的支持视觉。'}
                      </div>
                    </div>
                  </label>
                </div>
              </TabsContent>

              <TabsContent value="toolsPrompt" className="mt-0 space-y-3 py-1">
                {adapterKind === 'custom' ? (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>工具集</Label>
                    <div className="space-y-2">
                      <div className="grid grid-cols-2 gap-1.5">
                        {TOOL_PRESETS.map((preset) => {
                          const active = isPresetActive(preset.tools)
                          return (
                            <button
                              key={preset.id}
                              type="button"
                              onClick={() => applyToolPreset(preset.tools)}
                              className={cn(
                                'rounded-md border px-2.5 py-2 text-left transition hover:border-foreground/30',
                                active && 'border-primary bg-primary/5',
                              )}
                            >
                              <div className="text-xs font-medium">{preset.label}</div>
                              <div className="mt-0.5 text-[10px] text-muted-foreground">
                                {preset.desc}
                              </div>
                            </button>
                          )
                        })}
                      </div>
                      {AVAILABLE_AGENT_TOOLS.map((t) => {
                        const meta = TOOL_META[t]
                        return (
                          <label
                            key={t}
                            className={cn(
                              'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                              toolNames.has(t) && 'border-primary bg-primary/5',
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={toolNames.has(t)}
                              onChange={() => toggleTool(t)}
                              className="mt-0.5 accent-primary"
                            />
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium">{meta.label}</span>
                                <code className="font-mono text-[10px] text-muted-foreground">{t}</code>
                              </div>
                              <div className="mt-0.5 text-[10px] text-muted-foreground">{meta.desc}</div>
                            </div>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>工具集</Label>
                    <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                      {adapterKind === 'claude-code' ? (
                        <>
                          Claude Code agent 使用 SDK 内置工具集：Bash / Read / Write / Edit / Grep / Glob /
                          WebFetch / WebSearch / Task / TodoWrite 等。审批 / 沙箱 / 黑名单仍由 AChat 接管。
                        </>
                      ) : (
                        <>
                          Codex agent 使用 Codex SDK 内置的本地命令、文件修改、MCP 调用和计划事件。
                          Review 模式下以只读沙箱运行；Auto 模式下允许 workspace-write。运行时使用 AChat 隔离配置，不读取本机 ~/.codex。
                        </>
                      )}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label required>System Prompt</Label>
                  <Textarea
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    placeholder="你是…&#10;你的核心产出是…&#10;遵守以下原则…"
                    className="min-h-[160px] font-mono text-xs"
                  />
                </div>
              </TabsContent>

              <TabsContent value="skills" className="mt-0 space-y-3 py-1">
                {adapterKind === 'custom' ? (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>技能</Label>
                    {availableSkills.length === 0 ? (
                      <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                        还没有技能，去左侧 Skills 上传。
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {availableSkills.map((skill) => (
                          <label
                            key={skill.slug}
                            className={cn(
                              'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition hover:border-foreground/30',
                              skillNames.has(skill.slug) && 'border-primary bg-primary/5',
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={skillNames.has(skill.slug)}
                              onChange={() => toggleSkill(skill.slug)}
                              className="mt-0.5 accent-primary"
                            />
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium">{skill.name}</span>
                                <code className="font-mono text-[10px] text-muted-foreground">{skill.slug}</code>
                              </div>
                              <div className="mt-0.5 text-[10px] text-muted-foreground">{skill.description}</div>
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>技能</Label>
                    <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                      仅自建（custom）Agent 支持技能。SDK Agent（Claude Code / Codex）使用各自内置能力。
                    </div>
                  </div>
                )}
              </TabsContent>
            </div>
          </Tabs>

          {error && (
            <div className="shrink-0 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
        </div>
        )}

        {showDetailForm && (
          <DialogFooter>
            {!isEdit && (
              <Button
                variant="outline"
                onClick={() => {
                  setError(null)
                  setCreateStep('choose')
                }}
              >
                返回
              </Button>
            )}
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button onClick={() => void submit()} disabled={submitting}>
              {submitting ? (isEdit ? '保存中...' : '创建中...') : isEdit ? '保存' : '创建'}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CreateModeChoice({
  onConversational,
  onDetailed,
  onCancel,
}: {
  onConversational: () => void
  onDetailed: () => void
  onCancel: () => void
}) {
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="grid gap-2">
        <button
          type="button"
          onClick={onConversational}
          className="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 text-left transition hover:border-primary hover:bg-primary/5"
        >
          <div className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <MessageSquareText className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              对话创建
              <Sparkles className="size-3.5 text-primary" />
            </div>
            <div className="mt-1 text-xs leading-5 text-muted-foreground">
              描述想要的角色、任务和交付物，先生成可审阅的配置草稿。
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={onDetailed}
          className="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 text-left transition hover:border-foreground/30"
        >
          <div className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <SlidersHorizontal className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium">详细配置</div>
            <div className="mt-1 text-xs leading-5 text-muted-foreground">
              直接编辑名称、模型、API Key、工具权限和 System Prompt。
            </div>
          </div>
        </button>
      </div>

      <div className="flex justify-end">
        <Button variant="outline" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
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
