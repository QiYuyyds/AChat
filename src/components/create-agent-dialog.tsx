'use client'

import { ChevronDown, Cpu, MessageSquareText, Plug, SlidersHorizontal, Sparkles, User, Wrench } from 'lucide-react'
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
  fetchMcpServers,
  listSkills,
  updateAgent,
  type CreateAgentBody,
  type McpServerResponse,
  type SkillSummary,
  type UpdateAgentBody,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { pickRandomAgentIcon } from '@/shared/agent-icons'
import {
  AGENT_TOOL_META as TOOL_META,
  AGENT_TOOL_PRESETS as TOOL_PRESETS,
  AVAILABLE_AGENT_TOOLS,
  BASELINE_AGENT_TOOLS as BASELINE_TOOLS,
  DEFAULT_CUSTOM_AGENT_TOOLS,
  type AgentBuilderAdapter as AdapterKind,
  type AgentConfigDraft,
  type AgentToolName as ToolName,
  type AgentToolPresetId,
} from '@/shared/agent-builder-config'
import { useAppStore } from '@/stores/app-store'

type AgentTab = 'basic' | 'model' | 'toolsPrompt' | 'skills' | 'mcp'
type CreateStep = 'choose' | 'wizard' | 'detail'

/** Coder preset's system prompt template — used as the default prompt for new custom agents. */
const DEFAULT_CUSTOM_SYSTEM_PROMPT = TOOL_PRESETS[0].systemPromptTemplate

/**
 * Infer the active preset from persisted toolNames by matching only the
 * 5 UI-selectable tools (baseline tools are always-on and filtered out).
 * Returns null if no exact match is found (user's custom configuration).
 */
function inferPresetFromToolNames(tools: readonly string[]): AgentToolPresetId | null {
  const baselineSet = new Set<string>(BASELINE_TOOLS)
  const optionalTools = tools.filter((t) => !baselineSet.has(t))
  return (
    TOOL_PRESETS.find(
      (p) =>
        optionalTools.length === p.tools.length &&
        p.tools.every((t) => optionalTools.includes(t)),
    )?.id ?? null
  )
}

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
  const [toolNames, setToolNames] = useState<Set<string>>(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))
  const [skillNames, setSkillNames] = useState<Set<string>>(new Set())
  const [availableSkills, setAvailableSkills] = useState<SkillSummary[]>([])
  const [mcpServerIds, setMcpServerIds] = useState<Set<string>>(new Set())
  const [availableMcpServers, setAvailableMcpServers] = useState<McpServerResponse[]>([])
  const [isOrchestrator, setIsOrchestrator] = useState(false)
  const [executablePath, setExecutablePath] = useState('')
  const [customArgsText, setCustomArgsText] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<AgentTab>('basic')
  const [createStep, setCreateStep] = useState<CreateStep>('choose')
  const [activePresetId, setActivePresetId] = useState<AgentToolPresetId | null>('coder')

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
      setToolNames(new Set(agent.toolNames))
      // Infer activePresetId from persisted toolNames by matching only the
      // 5 UI-selectable tools (baseline tools are filtered out before match).
      // Do NOT overwrite the persisted systemPrompt.
      setActivePresetId(inferPresetFromToolNames(agent.toolNames))
      setSkillNames(new Set(agent.skillNames))
      setMcpServerIds(new Set(agent.mcpServerIds ?? []))
      setIsOrchestrator(agent.isOrchestrator)
      setExecutablePath(agent.executablePath ?? '')
      setCustomArgsText((agent.customArgs ?? []).join('\n'))
    } else {
      setAdapterKind('custom')
      setName('')
      setDescription(TOOL_PRESETS[0].defaultDescription)
      setCapabilitiesText(TOOL_PRESETS[0].defaultCapabilities.join('、'))
      setSystemPrompt(DEFAULT_CUSTOM_SYSTEM_PROMPT)
      setToolNames(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))
      setActivePresetId('coder')
      setSkillNames(new Set())
      setMcpServerIds(new Set())
      setIsOrchestrator(false)
      setExecutablePath('')
      setCustomArgsText('')
      setCreateStep('choose')
    }
    if (agent) setCreateStep('detail')
    setShowAdvanced(false)
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

  // 打开对话框时加载可用 MCP servers（custom adapter 才会用到）。
  useEffect(() => {
    if (!open) return
    fetchMcpServers()
      .then((servers) => setAvailableMcpServers(servers.filter((s) => s.enabled)))
      .catch((err) => console.error('[CreateAgentDialog] load MCP servers failed', err))
  }, [open])


  const handleAdapterKindChange = (kind: AdapterKind) => {
    setAdapterKind(kind)
    if (kind === 'custom') {
      if (toolNames.size === 0) {
        setToolNames(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))
        setActivePresetId('coder')
      } else {
        setActivePresetId(inferPresetFromToolNames(Array.from(toolNames)))
      }
      setSystemPrompt((prev) => (prev.trim() ? prev : DEFAULT_CUSTOM_SYSTEM_PROMPT))
    }
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

  const toggleMcpServer = (id: string) => {
    setMcpServerIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

const applyToolPreset = (preset: {
id: AgentToolPresetId
tools: readonly ToolName[]
systemPromptTemplate: string
defaultDescription: string
defaultCapabilities: readonly string[]
}) => {
setToolNames(new Set(preset.tools))
setSystemPrompt(preset.systemPromptTemplate)
setActivePresetId(preset.id)
// 同步填充基本信息：描述 + 能力标签，与 systemPrompt 一起随预设联动
setDescription(preset.defaultDescription)
setCapabilitiesText(preset.defaultCapabilities.join('、'))
// Selecting the orchestrator preset automatically marks the agent as
// an orchestrator; selecting any other preset clears it.
setIsOrchestrator(preset.id === 'orchestrator')
}

  const applyDraftToForm = (draft: AgentConfigDraft) => {
    const kind = draft.adapterName
    setAdapterKind(kind)
    setName(draft.name)
    setDescription(draft.description)
    setCapabilitiesText(draft.capabilities.join(', '))
    setSystemPrompt(draft.systemPrompt)
    setToolNames(new Set(draft.toolNames))
    setActivePresetId(inferPresetFromToolNames(draft.toolNames))
    setSkillNames(new Set())
    setMcpServerIds(new Set())
    setIsOrchestrator(false)
    setShowAdvanced(false)
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
        toolNames: isSdkAgent ? [] : draft.toolNames,
        skillNames: [],
        mcpServerIds: isSdkAgent ? [] : Array.from(mcpServerIds),
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
          toolNames: isSdkAgent ? [] : Array.from(toolNames),
          skillNames: isSdkAgent ? [] : Array.from(skillNames),
          mcpServerIds: isSdkAgent ? [] : Array.from(mcpServerIds),
          isOrchestrator,
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
          toolNames: isSdkAgent ? [] : Array.from(toolNames),
          skillNames: isSdkAgent ? [] : Array.from(skillNames),
          mcpServerIds: isSdkAgent ? [] : Array.from(mcpServerIds),
          isOrchestrator: isOrchestrator || undefined,
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
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-2xl">
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
        <div className="agent-fade-up flex min-h-0 flex-col gap-2">
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
              <TabsTrigger value="mcp">
                <Plug className="size-3.5" />
                MCP
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

              </TabsContent>

              <TabsContent value="model" className="mt-0 space-y-3 py-1">
                <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                  <Label>适配器</Label>
                  <div className="flex flex-col gap-1.5">
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                        adapterKind === 'custom' && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
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
                        'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                        adapterKind === 'claude-code' && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
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
                        <div className="text-xs font-medium">Claude Code CLI</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          启动本机 claude CLI 子进程，自带 Bash / Read / Write / Edit / Grep / Glob / WebFetch / Task 子 agent 等一整套工具。认证走 claude login / 环境变量，无需填 Key。
                        </div>
                      </div>
                    </label>
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                        adapterKind === 'codex' && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
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
                        <div className="text-xs font-medium">Codex CLI</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          启动本机 codex CLI 子进程，支持本地仓库读写、命令执行、线程续接和结构化事件流；需要 Codex/Responses 兼容后端。认证走 codex login / 环境变量，无需填 Key。
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                {adapterKind === 'custom' && (
                  <div className="rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                    模型配置已移至侧栏「模型」面板。请在发送消息时从输入栏选择模型档。
                  </div>
                )}

                {(adapterKind === 'claude-code' || adapterKind === 'codex') && (
                  <div className="rounded-md border border-dashed bg-muted/20">
                    <button
                      type="button"
                      onClick={() => setShowAdvanced((v) => !v)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left transition hover:bg-muted/40"
                    >
                      <span className="text-xs font-medium">高级配置（均可留空，走 CLI 默认）</span>
                      <ChevronDown
                        className={cn(
                          'size-3.5 text-muted-foreground transition-transform',
                          showAdvanced && 'rotate-180',
                        )}
                      />
                    </button>
                    {showAdvanced && (
                      <div className="space-y-3 border-t px-3 py-2">
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
                              本机 {adapterKind === 'claude-code' ? 'Claude Code' : 'Codex'} CLI 的路径。留空则自动 PATH 查找。
                            </div>
                          </div>
                        </div>

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
                              传给 {adapterKind === 'claude-code' ? 'claude' : 'codex'} CLI 的额外参数，每行一个。
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </TabsContent>

              <TabsContent value="toolsPrompt" className="mt-0 space-y-3 py-1">
                {adapterKind === 'custom' ? (
                  <>
                    {/* Horizontal role bar — 4 preset buttons */}
                    <div className="flex flex-wrap gap-1.5">
                      {TOOL_PRESETS.map((preset) => (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => applyToolPreset(preset)}
                          className={cn(
                            'rounded-lg border px-2.5 py-1.5 text-left transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                            activePresetId === preset.id && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
                          )}
                        >
                          <span className="text-xs font-medium">{preset.label}</span>
                        </button>
                      ))}
                    </div>
                    {/* Left-right split: tools (left) + prompt (right) */}
                    <div className="grid grid-cols-2 gap-3">
                      {/* Left: tool checklist — 5 UI-selectable tools */}
                      <div className="space-y-2">
                        <div className="text-xs text-muted-foreground">可选工具</div>
                        <div className="grid grid-cols-1 gap-1.5">
                          {AVAILABLE_AGENT_TOOLS.map((t) => {
                            const meta = TOOL_META[t]
                            return (
                              <label
                                key={t}
                                className={cn(
                                  'flex cursor-pointer items-start gap-1.5 rounded-lg border px-2 py-1.5 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                                  toolNames.has(t) && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={toolNames.has(t)}
                                  onChange={() => toggleTool(t)}
                                  className="mt-0.5 accent-primary"
                                />
                                <div className="min-w-0">
                                  <div className="flex items-center gap-1">
                                    <span className="text-[10px] font-medium">{meta.label}</span>
                                    <code className="font-mono text-[9px] text-muted-foreground">{t}</code>
                                  </div>
                                  <div className="text-[9px] leading-tight text-muted-foreground">{meta.desc}</div>
                                </div>
                              </label>
                            )
                          })}
                        </div>
                      </div>
                      {/* Right: System Prompt editor */}
                      <div className="space-y-2">
                        <div className="text-xs text-muted-foreground">
                          System Prompt <span className="text-destructive">*</span>
                        </div>
                        <Textarea
                          value={systemPrompt}
                          onChange={(e) => setSystemPrompt(e.target.value)}
                          placeholder="你是…&#10;你的核心产出是…&#10;遵守以下原则…"
                          className="min-h-[300px] font-mono text-xs"
                        />
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                      <Label>工具集</Label>
                      <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                        {adapterKind === 'claude-code' ? (
                          <>
                            Claude Code agent 使用 CLI 内置工具集：Bash / Read / Write / Edit / Grep / Glob /
                            WebFetch / WebSearch / Task / TodoWrite 等。审批 / 沙箱 / 黑名单仍由 AChat 接管。
                          </>
                        ) : (
                          <>
                            Codex agent 使用 Codex CLI 内置的本地命令、文件修改、MCP 调用和计划事件。
                            Review 模式下以只读沙箱运行；Auto 模式下允许 workspace-write。运行时使用 AChat 隔离配置，不读取本机 ~/.codex。
                          </>
                        )}
                      </div>
                    </div>
                    <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                      <Label required>System Prompt</Label>
                      <Textarea
                        value={systemPrompt}
                        onChange={(e) => setSystemPrompt(e.target.value)}
                        placeholder="你是…&#10;你的核心产出是…&#10;遵守以下原则…"
                        className="min-h-[160px] font-mono text-xs"
                      />
                    </div>
                  </>
                )}
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
                              'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                              skillNames.has(skill.slug) && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
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
                      仅自建（custom）Agent 支持技能。CLI Agent（Claude Code / Codex）使用各自内置能力。
                    </div>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="mcp" className="mt-0 space-y-3 py-1">
                {adapterKind === 'custom' ? (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>MCP Servers</Label>
                    {availableMcpServers.length === 0 ? (
                      <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                        还没有已启用的 MCP Server，去左侧 MCP 面板添加。
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {availableMcpServers.map((server) => (
                          <label
                            key={server.id}
                            className={cn(
                              'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-all duration-300 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]',
                              mcpServerIds.has(server.id) && 'border-primary/40 bg-primary/[0.04] shadow-[var(--shadow-sm)]',
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={mcpServerIds.has(server.id)}
                              onChange={() => toggleMcpServer(server.id)}
                              className="mt-0.5 accent-primary"
                            />
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium">{server.name}</span>
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
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                    <div className="col-span-2 mt-1 text-[10px] text-muted-foreground">
                      勾选后，该 Agent 运行时会连接这些 MCP server 并将其工具注入 ReAct 循环。工具名格式为 <code className="font-mono">mcp__&lt;server&gt;__&lt;tool&gt;</code>。
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-[80px_1fr] items-start gap-3">
                    <Label>MCP Servers</Label>
                    <div className="rounded-md border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                      仅自建（custom）Agent 支持外部 MCP server。CLI Agent（Claude Code / Codex）使用各自 SDK 内置的 MCP 接入。
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
      <div className="grid gap-2.5">
        {/* Conversational creation — premium card with ambient gradient wash */}
        <button
          type="button"
          onClick={onConversational}
          className="agent-fade-up group relative flex cursor-pointer items-start gap-3 overflow-hidden rounded-lg border px-4 py-3.5 text-left transition-all duration-500 hover:border-primary/40 hover:bg-primary/[0.03] hover:shadow-[var(--shadow-md)]"
        >
          <div className="pointer-events-none absolute -right-6 -top-6 size-20 rounded-full bg-primary/[0.06] opacity-60 blur-2xl transition-opacity duration-500 group-hover:opacity-100" />
          <div className="relative mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <div className="pointer-events-none absolute inset-0 rounded-lg bg-primary/10 blur-md" />
            <MessageSquareText className="relative size-4 text-primary" />
          </div>
          <div className="relative min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-semibold tracking-tight">
              对话创建
              <Sparkles className="size-3.5 text-primary" />
            </div>
            <div className="mt-1 text-xs leading-5 text-muted-foreground">
              描述想要的角色、任务和交付物，先生成可审阅的配置草稿。
            </div>
          </div>
        </button>

        {/* Detailed configuration — premium card with ambient gradient wash */}
        <button
          type="button"
          onClick={onDetailed}
          className="agent-fade-up-delay-1 group relative flex cursor-pointer items-start gap-3 overflow-hidden rounded-lg border px-4 py-3.5 text-left transition-all duration-500 hover:border-foreground/30 hover:shadow-[var(--shadow-md)]"
        >
          <div className="pointer-events-none absolute -right-6 -top-6 size-20 rounded-full bg-muted-foreground/[0.06] opacity-60 blur-2xl transition-opacity duration-500 group-hover:opacity-100" />
          <div className="relative mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
            <SlidersHorizontal className="size-4 text-muted-foreground" />
          </div>
          <div className="relative min-w-0">
            <div className="text-sm font-semibold tracking-tight">详细配置</div>
            <div className="mt-1 text-xs leading-5 text-muted-foreground">
              直接编辑名称、模型、API Key、工具权限和 System Prompt。
            </div>
          </div>
        </button>
      </div>

      <div className="agent-fade-up-delay-2 flex justify-end">
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
