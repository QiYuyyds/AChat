'use client'

import {
  AlertTriangle,
  ArrowUp,
  Bot,
  CircleHelp,
  Cpu,
  Download,
  Plus,
  Rocket,
  Settings,
  Shield,
  Sparkles,
  Square,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { nanoid } from 'nanoid'
import { useEffect, useMemo, useRef, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { AttachmentChip, PendingAttachmentChip } from '@/components/attachment-chip'
import { QuotedMessage } from '@/components/quoted-message'
import { SlashCommandHelpDialog } from '@/components/slash-command-help-dialog'
import { SlashCommandMenu, type SlashCommandItem } from '@/components/slash-command-menu'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import type { AgentRow, ConversationWithMeta, MessageRow } from '@/db/schema'
import type { ModelProfile } from '@/shared/types'
import {
  abortRun,
  clearConversationHistory as clearConversationHistoryAPI,
  fetchMessages,
  listSkills,
  reviseDispatchPlan,
  sendMessage as sendMessageAPI,
  setFsWriteApprovalMode,
  type SkillSummary,
} from '@/lib/api'
import { getToolDisplayName } from '@/lib/tool-display'
import { emitUiCommand } from '@/lib/ui-command-events'
import { cn } from '@/lib/utils'
import { useAppStore, usePendingAttachments, usePendingPlanReviewForConversation, useTopLevelRunningRuns } from '@/stores/app-store'

interface MentionTrigger {
  start: number // textarea 中 @ 字符的 index
  query: string // @ 之后到光标之间的字符
}

interface SlashTrigger {
  start: number
  query: string
}

const SLASH_COMMANDS: SlashCommandItem[] = [
  {
    id: 'deploy',
    command: '/deploy',
    label: '部署产物',
    description: '部署当前会话的网页产物',
    icon: Rocket,
  },
  {
    id: 'help',
    command: '/help',
    label: '命令帮助',
    description: '查看可用命令',
    icon: CircleHelp,
  },
  {
    id: 'export',
    command: '/export',
    label: '导出会话',
    description: '下载当前会话的 Markdown 记录',
    icon: Download,
  },
  {
    id: 'clear',
    command: '/clear',
    label: '清空历史',
    description: '删除当前会话历史消息',
    icon: Trash2,
  },
  {
    id: 'settings',
    command: '/settings',
    label: '设置',
    description: '打开设置',
    icon: Settings,
  },
  {
    id: 'agents',
    command: '/agents',
    label: '联系人',
    description: '打开联系人管理',
    icon: Bot,
  },
]

function buildConversationExportMarkdown({
  agents,
  conversation,
  conversationId,
  messages,
}: {
  agents: Record<string, AgentRow>
  conversation: ConversationWithMeta | undefined
  conversationId: string
  messages: MessageRow[]
}): string {
  const title = conversation?.title ?? conversationId
  const lines = [
    `# ${title}`,
    '',
    `- Conversation ID: ${conversationId}`,
    `- Exported At: ${new Date().toISOString()}`,
    `- Messages: ${messages.length}`,
    '',
  ]

  messages.forEach((message, index) => {
    lines.push(`## ${index + 1}. ${messageAuthor(message, agents)}`)
    lines.push('')
    lines.push(`_Status: ${message.status} | Created: ${new Date(message.createdAt).toISOString()}_`)
    lines.push('')
    for (const part of message.parts) {
      lines.push(renderMessagePartForExport(part))
      lines.push('')
    }
  })

  return lines.join('\n').trimEnd() + '\n'
}

function messageAuthor(message: MessageRow, agents: Record<string, AgentRow>): string {
  if (message.role === 'user') return 'User'
  if (message.role === 'system') return 'System'
  return message.agentId ? (agents[message.agentId]?.name ?? `Agent ${message.agentId}`) : 'Agent'
}

function renderMessagePartForExport(part: MessageRow['parts'][number]): string {
  switch (part.type) {
    case 'text':
      return part.content
    case 'thinking':
      return `> Thinking\n>\n${blockquote(part.content)}`
    case 'code':
      return ['```' + (part.language ?? ''), part.content, '```'].join('\n')
    case 'tool_use':
      return [
        `Tool Use: ${getToolDisplayName(part.toolName, part.args)} (${part.toolName})`,
        '```json',
        stringifyForExport(part.args),
        '```',
      ].join('\n')
    case 'tool_result':
      return [
        `Tool Result${part.isError ? ' (error)' : ''}: ${part.callId}`,
        '```json',
        stringifyForExport(part.result),
        '```',
      ].join('\n')
    case 'artifact_ref':
      return `[Artifact: ${part.artifactId}]`
    case 'deploy_status':
      return part.deployment.status === 'ready'
        ? `[Deployment: ${part.deployment.title} ${formatDeploymentSourceLabel(part.deployment)} (${part.deployment.previewPath})]`
        : `[Deployment failed: ${part.deployment.title} (${part.deployment.error ?? 'unknown error'})]`
    case 'deploy_candidates':
      return `[Deployment candidates: ${part.candidates
        .map((candidate) => `${candidate.title} v${candidate.version} (${candidate.artifactId})`)
        .join(', ')}]`
    case 'image_attachment':
    case 'file_attachment':
      return `[Attachment: ${part.fileName} (${part.attachmentId}, ${part.mimeType}, ${part.size} bytes)]`
    default:
      return ''
  }
}

function formatDeploymentSourceLabel(
  deployment: Extract<MessageRow['parts'][number], { type: 'deploy_status' }>['deployment'],
): string {
  if (deployment.sourceType === 'workspace') {
    return `workspace=${deployment.workspacePath ?? 'unknown'}`
  }
  return `v${deployment.version}`
}

function blockquote(text: string): string {
  return text
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n')
}

function stringifyForExport(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function downloadMarkdownFile(title: string, content: string): void {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  const fileName = `${safeFileName(title)}-${timestamp}.md`
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function safeFileName(value: string): string {
  const cleaned = value.replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim()
  return (cleaned || 'conversation').slice(0, 80)
}

export function MessageInput({
  conversationId,
  handleFiles,
  uploading,
}: {
  conversationId: string
  handleFiles: (files: FileList | File[] | null) => Promise<void>
  uploading: Array<{ tempId: string; name: string }>
}) {
  const [content, setContent] = useState('')
  const [mentionedIds, setMentionedIds] = useState<string[]>([])
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  const [trigger, setTrigger] = useState<MentionTrigger | null>(null)
  const [highlight, setHighlight] = useState(0)
  const [slashTrigger, setSlashTrigger] = useState<SlashTrigger | null>(null)
  const [slashHighlight, setSlashHighlight] = useState(0)
  const [slashHelpOpen, setSlashHelpOpen] = useState(false)
  const [allSkills, setAllSkills] = useState<SkillSummary[]>([])
  const [clearHistoryOpen, setClearHistoryOpen] = useState(false)
  const [clearingHistory, setClearingHistory] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [sending, setSending] = useState(false)
  const [aborting, setAborting] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addLocalUserMessage = useAppStore((s) => s.addLocalUserMessage)
  const upsertMessage = useAppStore((s) => s.upsertMessage)
  const replaceLocalMessageId = useAppStore((s) => s.replaceLocalMessageId)
  const clearConversationHistory = useAppStore((s) => s.clearConversationHistory)
  const conversation = useAppStore((s) => s.conversations[conversationId])
  const upsertConversation = useAppStore((s) => s.upsertConversation)
  const agents = useAppStore((s) => s.agents)
  const modelProfiles = useAppStore((s) => s.modelProfiles)
  const selectedProfileId = useAppStore((s) => s.selectedProfileIdByConv[conversationId] ?? null)
  const setSelectedProfileId = useAppStore((s) => s.setSelectedProfileId)
  const runningRuns = useTopLevelRunningRuns(conversationId)
  const isRunning = runningRuns.length > 0
  // 计划待审批时，输入框改作「对计划提修改意见」用——即使 orchestrator run 仍在 running 也放开
  const planReview = usePendingPlanReviewForConversation(conversationId)
  const composerLocked = planReview !== null
  const pending = usePendingAttachments(conversationId)
  const removePendingAttachment = useAppStore((s) => s.removePendingAttachment)
  const clearPendingAttachments = useAppStore((s) => s.clearPendingAttachments)
  const [modeBusy, setModeBusy] = useState(false)

  // 引用回复目标
  const replyTargetId = useAppStore((s) => s.replyTargetByConv[conversationId])
  const replyMessage = useAppStore((s) => (replyTargetId ? s.messages[replyTargetId] : null))
  const setReplyTarget = useAppStore((s) => s.setReplyTarget)
  const pendingQuote = useAppStore((s) => s.pendingQuoteForInput)
  const setPendingQuote = useAppStore((s) => s.setPendingQuote)

  // 拿到 pendingQuote 后聚焦输入框，方便用户立刻输指令
  useEffect(() => {
    if (pendingQuote) textareaRef.current?.focus()
  }, [pendingQuote])

  const isGroup = conversation?.mode === 'group'

  // Check if conversation has any SDK (Custom) agents → show model selector
  const hasSdkAgent = useMemo(() => {
    if (!conversation) return false
    return conversation.agentIds.some((id) => agents[id]?.adapterName === 'custom')
  }, [conversation, agents])

  const profileList = useMemo(() => Object.values(modelProfiles).sort((a, b) => b.createdAt - a.createdAt), [modelProfiles])
  const selectedProfile = selectedProfileId ? modelProfiles[selectedProfileId] : null

  // 可被 @ 的 agent：群聊里所有成员，包含 Orchestrator
  // (@ Orchestrator 是合法语义：用户明确请求 Orchestrator 接手)
  const candidates = useMemo<AgentRow[]>(() => {
    if (!conversation) return []
    return conversation.agentIds
      .map((id) => agents[id])
      .filter((a): a is AgentRow => Boolean(a))
  }, [conversation, agents])

  // 过滤候选
  const filtered = useMemo(() => {
    if (!trigger) return []
    const q = trigger.query.toLowerCase()
    if (!q) return candidates
    return candidates.filter((a) => a.name.toLowerCase().includes(q))
  }, [trigger, candidates])

  const slashCommands = useMemo<SlashCommandItem[]>(
    () =>
      SLASH_COMMANDS.map((command) => {
        if (command.id === 'deploy') {
          return {
            ...command,
            description:
              pending.length > 0 || uploading.length > 0
                ? '请先移除附件'
                : isRunning
                  ? '请先中止或等待排队中的 Agent'
                  : command.description,
            disabled: sending || isRunning || pending.length > 0 || uploading.length > 0,
          }
        }
        if (command.id === 'export') {
          return {
            ...command,
            description: exporting ? '正在导出当前会话' : command.description,
            disabled: exporting,
          }
        }
        if (command.id === 'clear') {
          return {
            ...command,
            description: isRunning
              ? '请先中止正在运行的 Agent'
              : clearingHistory
                ? '正在清空会话历史'
                : command.description,
            disabled: isRunning || clearingHistory,
          }
        }
        return command
      }),
    [clearingHistory, exporting, isRunning, pending.length, sending, uploading.length],
  )

  // Skills equipped by this conversation's agents → /-menu entries (custom agents only).
  const skillCommands = useMemo<SlashCommandItem[]>(() => {
    if (!conversation) return []
    const slugs = new Set<string>()
    for (const id of conversation.agentIds) {
      for (const slug of agents[id]?.skillNames ?? []) slugs.add(slug)
    }
    if (slugs.size === 0) return []
    return allSkills
      .filter((s) => slugs.has(s.slug))
      .map((s) => ({
        id: `skill:${s.slug}`,
        command: `/${s.slug}`,
        label: s.name,
        description: s.description || '让当前 Agent 使用该技能',
        icon: Sparkles,
      }))
  }, [conversation, agents, allSkills])

  // Skills first so equipped skills are visible without scrolling past built-ins.
  const allSlashCommands = useMemo(
    () => [...skillCommands, ...slashCommands],
    [slashCommands, skillCommands],
  )

  const filteredSlashCommands = useMemo(() => {
    if (!slashTrigger) return []
    const q = slashTrigger.query.toLowerCase()
    if (!q) return allSlashCommands
    return allSlashCommands.filter((command) =>
      [
        command.id,
        command.command,
        command.command.slice(1),
        command.label,
        command.description,
      ].some((value) => value.toLowerCase().includes(q)),
    )
  }, [slashTrigger, allSlashCommands])

  // 候选变化时重置高亮项
  useEffect(() => {
    setHighlight(0)
  }, [trigger?.query, filtered.length])

  useEffect(() => {
    setSlashHighlight(0)
  }, [slashTrigger?.query, filteredSlashCommands.length])

  // Load skill metadata once so /-menu can show skills bound to this conversation's agents.
  useEffect(() => {
    listSkills()
      .then(setAllSkills)
      .catch((err) => console.error('[MessageInput] load skills failed', err))
  }, [])

  // 切换会话清空 state（pending 由 store 自己分桶，不需要在这里清）
  useEffect(() => {
    setContent('')
    setMentionedIds([])
    setSelectedSkills([])
    setTrigger(null)
    setSlashTrigger(null)
  }, [conversationId])

  const mentionedAgents = mentionedIds.map((id) => agents[id]).filter(Boolean)

  const detectSlashTrigger = (text: string, cursor: number): SlashTrigger | null => {
    const beforeCursor = text.slice(0, cursor)
    const slashIndex = beforeCursor.lastIndexOf('/')
    if (slashIndex < 0) return null
    if (beforeCursor.slice(0, slashIndex).trim().length > 0) return null

    const query = beforeCursor.slice(slashIndex + 1)
    if (/\s/.test(query)) return null
    return { start: slashIndex, query }
  }

  const updateInputTriggers = (text: string, cursor: number) => {
    const slash = detectSlashTrigger(text, cursor)
    if (slash) {
      setSlashTrigger(slash)
      setTrigger(null)
      return
    }

    setSlashTrigger(null)
    updateMentionTrigger(text, cursor)
  }

  // —— 触发检测：从光标往前找 @，遇 whitespace 则放弃；@ 前必须是 word boundary
  const updateMentionTrigger = (text: string, cursor: number) => {
    if (!isGroup) return setTrigger(null)
    let i = cursor - 1
    while (i >= 0) {
      const c = text[i]
      if (c === '@') {
        const before = i === 0 ? ' ' : text[i - 1]
        if (/\s/.test(before)) {
          setTrigger({ start: i, query: text.slice(i + 1, cursor) })
          return
        }
        setTrigger(null)
        return
      }
      if (/\s/.test(c)) {
        setTrigger(null)
        return
      }
      i--
    }
    setTrigger(null)
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value
    setContent(value)
    updateInputTriggers(value, e.target.selectionStart)
  }

  // 光标移动（鼠标点击 / 方向键）也要重新判断
  const handleSelect = () => {
    const cursor = textareaRef.current?.selectionStart ?? 0
    updateInputTriggers(content, cursor)
  }

  const fillMention = (agent: AgentRow) => {
    if (!trigger || !textareaRef.current) return
    const cursor = textareaRef.current.selectionStart ?? content.length
    const insertText = `@${agent.name} `
    const newContent =
      content.slice(0, trigger.start) + insertText + content.slice(cursor)
    setContent(newContent)
    setMentionedIds((prev) => (prev.includes(agent.id) ? prev : [...prev, agent.id]))
    setTrigger(null)
    setSlashTrigger(null)

    // 把光标移到插入的尾部
    requestAnimationFrame(() => {
      const newPos = trigger.start + insertText.length
      textareaRef.current?.setSelectionRange(newPos, newPos)
      textareaRef.current?.focus()
    })
  }

  // Picking a skill drops the /-fragment and adds a chip; the directive is
  // assembled into the outgoing message on send (keeps the composer clean).
  const addSkill = (slug: string) => {
    if (!slashTrigger) return
    const cursor = textareaRef.current?.selectionStart ?? content.length
    const newContent = content.slice(0, slashTrigger.start) + content.slice(cursor)
    setContent(newContent)
    setSelectedSkills((prev) => (prev.includes(slug) ? prev : [...prev, slug]))
    setTrigger(null)
    setSlashTrigger(null)
    requestAnimationFrame(() => {
      const pos = slashTrigger.start
      textareaRef.current?.setSelectionRange(pos, pos)
      textareaRef.current?.focus()
    })
  }

  const removeSkill = (slug: string) => {
    setSelectedSkills((prev) => prev.filter((s) => s !== slug))
  }

  const removeMention = (id: string) => {
    setMentionedIds((prev) => prev.filter((x) => x !== id))
  }

  const removePending = (id: string) => {
    removePendingAttachment(conversationId, id)
  }

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length === 0) return
    e.preventDefault()
    void handleFiles(imageFiles)
  }

  const clearSlashCommandInput = () => {
    setContent('')
    setMentionedIds([])
    setSelectedSkills([])
    setTrigger(null)
    setSlashTrigger(null)
  }

  const clearComposerDraft = () => {
    clearSlashCommandInput()
    clearPendingAttachments(conversationId)
    if (pendingQuote) setPendingQuote(null)
    if (replyTargetId) setReplyTarget(conversationId, null)
  }

  const executeExportCommand = async () => {
    if (exporting) return
    clearSlashCommandInput()
    setExporting(true)
    try {
      const messages = await fetchMessages(conversationId)
      const markdown = buildConversationExportMarkdown({
        agents,
        conversation,
        conversationId,
        messages,
      })
      downloadMarkdownFile(conversation?.title ?? conversationId, markdown)
    } catch (err) {
      console.error('[MessageInput] export failed', err)
    } finally {
      setExporting(false)
    }
  }

  const confirmClearHistory = async () => {
    if (clearingHistory || isRunning) return
    setClearingHistory(true)
    try {
      const result = await clearConversationHistoryAPI(conversationId)
      clearConversationHistory(conversationId, result.conversation)
      clearComposerDraft()
      setClearHistoryOpen(false)
    } catch (err) {
      console.error('[MessageInput] clear history failed', err)
    } finally {
      setClearingHistory(false)
    }
  }

  const executeDeployCommand = async () => {
    if (sending || isRunning || pending.length > 0 || uploading.length > 0) return
    clearSlashCommandInput()
    if (pendingQuote) setPendingQuote(null)
    if (replyTargetId) setReplyTarget(conversationId, null)

    const tempId = `temp_${nanoid()}`
    addLocalUserMessage({
      tempId,
      conversationId,
      content: '/deploy',
      mentionedAgentIds: [],
      attachments: [],
    })
    setSending(true)
    try {
      const result = await sendMessageAPI(conversationId, { content: '/deploy' })
      replaceLocalMessageId(tempId, result.messageId)
      upsertReturnedMessages(result.messages)
    } catch (err) {
      console.error('[MessageInput] deploy failed', err)
    } finally {
      setSending(false)
    }
  }

  const upsertReturnedMessages = (messages: MessageRow[] | undefined) => {
    for (const message of messages ?? []) upsertMessage(message)
  }

  const executeSlashCommand = async (command: SlashCommandItem) => {
    if (command.disabled) return
    if (command.id.startsWith('skill:')) {
      addSkill(command.id.slice('skill:'.length))
      return
    }
    switch (command.id) {
      case 'deploy':
        await executeDeployCommand()
        break
      case 'help':
        clearSlashCommandInput()
        setSlashHelpOpen(true)
        break
      case 'export':
        await executeExportCommand()
        break
      case 'clear':
        clearSlashCommandInput()
        setClearHistoryOpen(true)
        break
      case 'settings':
        clearSlashCommandInput()
        emitUiCommand('open-settings')
        break
      case 'agents':
        clearSlashCommandInput()
        emitUiCommand('open-agents')
        break
      default:
        break
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME 组字中（中文/日文等）不拦截按键：让 Enter 用于确认候选词，避免半句误发送
    if (e.nativeEvent.isComposing) return
    if (slashTrigger && filteredSlashCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashHighlight((i) => (i + 1) % filteredSlashCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashHighlight(
          (i) => (i - 1 + filteredSlashCommands.length) % filteredSlashCommands.length,
        )
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const command = filteredSlashCommands[slashHighlight]
        if (command) void executeSlashCommand(command)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashTrigger(null)
        return
      }
    }
    // 在 popup 打开时，方向键/Enter/Esc 走 popup
    if (trigger && filtered.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHighlight((i) => (i + 1) % filtered.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHighlight((i) => (i - 1 + filtered.length) % filtered.length)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        fillMention(filtered[highlight])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setTrigger(null)
        return
      }
    }

    // 默认 Enter 提交
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  const submit = async () => {
    const text = content.trim()
    const hasAttachments = pending.length > 0

    // 计划审批中：把输入当作对计划的自然语言修改意见，交给 Orchestrator 重排（不走普通发送）。
    // 反馈会由服务端落库 + 广播成一条 user 消息回显到对话。
    if (planReview) {
      if (!text || sending) return
      setContent('')
      setSending(true)
      try {
        await reviseDispatchPlan(conversationId, planReview.planId, text)
      } catch (err) {
        console.error('[MessageInput] revise plan failed', err)
      } finally {
        setSending(false)
      }
      return
    }

    if ((!text && !hasAttachments && selectedSkills.length === 0) || sending) return

    const exactSlashCommand = allSlashCommands.find((command) => command.command === text)
    if (exactSlashCommand) {
      await executeSlashCommand(exactSlashCommand)
      return
    }

    // 选区改写：把 pendingQuote 注入消息开头（XML 块给 LLM 当上下文）
    const baseContent = pendingQuote
      ? `<quoted_selection source="${pendingQuote.sourceLabel}"${pendingQuote.artifactId ? ` artifactId="${pendingQuote.artifactId}"` : ''}${pendingQuote.filePath ? ` filePath="${pendingQuote.filePath}"` : ''}>\n${pendingQuote.text}\n</quoted_selection>\n\n${text}`
      : text
    // 技能 chip → 指令前缀，驱动 agent load_skill 并使用对应技能
    const skillDirective =
      selectedSkills.length > 0
        ? selectedSkills.map((s) => `使用技能 ${s}`).join('；') + '：\n\n'
        : ''
    const finalContent = skillDirective + baseContent

    const tempId = `temp_${nanoid()}`
    const parentId = replyTargetId ?? undefined
    addLocalUserMessage({
      tempId,
      conversationId,
      content: finalContent,
      mentionedAgentIds: mentionedIds,
      parentMessageId: parentId,
      attachments: pending,
    })
    setContent('')
    setMentionedIds([])
    setSelectedSkills([])
    setTrigger(null)
    setSlashTrigger(null)
    if (pendingQuote) setPendingQuote(null)
    const attachmentIds = pending.map((a) => a.id)
    clearPendingAttachments(conversationId)
    if (replyTargetId) setReplyTarget(conversationId, null)
    setSending(true)

    try {
      const result = await sendMessageAPI(conversationId, {
        content: finalContent,
        mentionedAgentIds: mentionedIds,
        parentMessageId: parentId,
        attachmentIds,
        modelProfileId: hasSdkAgent ? (selectedProfileId ?? undefined) : undefined,
        // 乐观 temp id 作为回执：message.added 事件到达时即时认领，避免双行闪动
        clientMessageId: tempId,
      })
      replaceLocalMessageId(tempId, result.messageId)
      upsertReturnedMessages(result.messages)
    } catch (err) {
      console.error('[MessageInput] send failed', err)
      // POST 失败（网络/超时/非 2xx）：把乐观消息标记为发送失败，不再静默残留。
      // temp 已被 message.added 认领（后端实际已落库）时 messages[tempId] 为空，跳过。
      const temp = useAppStore.getState().messages[tempId]
      if (temp) upsertMessage({ ...temp, status: 'error' })
    } finally {
      setSending(false)
    }
  }

  const abortAll = async () => {
    if (aborting) return
    setAborting(true)
    try {
      await Promise.allSettled(runningRuns.map((r) => abortRun(r.id)))
    } finally {
      setAborting(false)
    }
  }

  const approvalMode = conversation?.fsWriteApprovalMode ?? 'review'
  const toggleApprovalMode = async () => {
    if (modeBusy || !conversation) return
    const nextMode = approvalMode === 'review' ? 'auto' : 'review'
    setModeBusy(true)
    try {
      const updated = await setFsWriteApprovalMode(conversationId, nextMode)
      upsertConversation(updated)
    } catch (err) {
      console.error('[MessageInput] toggle approval mode failed', err)
    } finally {
      setModeBusy(false)
    }
  }

  const hasComposeAttachments = pending.length > 0 || uploading.length > 0

  return (
    <div className="relative shrink-0 -translate-y-2 bg-background px-2 pb-3 pt-0.5">
      <div className="mx-auto max-w-3xl">
        {/* 引用预览 */}
        {replyMessage && (
          <div className="mb-2">
            <QuotedMessage
              message={replyMessage}
              variant="compose"
              onDismiss={() => setReplyTarget(conversationId, null)}
            />
          </div>
        )}

        {/* 选区改写引用块 */}
        {pendingQuote && (
          <div className="mb-2 flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 px-2 py-1.5 text-xs">
            <Sparkles className="mt-0.5 size-3 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <div className="font-medium text-primary">
                {pendingQuote.kind === 'ask' ? '提问' : '改写'} · {pendingQuote.sourceLabel}
              </div>
              <pre className="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words font-mono text-[10px] text-muted-foreground">
                {pendingQuote.text}
              </pre>
              <div className="mt-0.5 text-[10px] text-muted-foreground/70">
                {pendingQuote.kind === 'ask'
                  ? '在下方输入框写你的问题，发送时会带上这段引用一起发给 Agent'
                  : '在下方输入框写改写指令，发送时会作为引用一起发给 Agent'}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setPendingQuote(null)}
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="取消引用"
            >
              <X className="size-3" />
            </button>
          </div>
        )}

        {/* 已选技能 chips（浅色气泡，/slug） */}
        {selectedSkills.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {selectedSkills.map((slug) => (
              <span
                key={slug}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 py-0.5 pl-2 pr-1.5 font-mono text-xs text-primary"
              >
                <Sparkles className="size-3" />
                <span>/{slug}</span>
                <button
                  type="button"
                  onClick={() => removeSkill(slug)}
                  className="rounded-full p-0.5 hover:bg-primary/20"
                  title="移除技能"
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* 已确认的 mention chips */}
        {mentionedAgents.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] text-muted-foreground">@ 指定</span>
            {mentionedAgents.map((a) => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 py-0.5 pl-1 pr-1.5 text-xs text-primary"
              >
                <AgentAvatar agent={a} size="xs" />
                <span>{a.name}</span>
                <button
                  type="button"
                  onClick={() => removeMention(a.id)}
                  className="rounded-full p-0.5 hover:bg-primary/20"
                  title="移除"
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <Dialog
        open={clearHistoryOpen}
        onOpenChange={(open) => {
          if (!clearingHistory) setClearHistoryOpen(open)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-destructive" />
              清空会话历史？
            </DialogTitle>
            <DialogDescription>
              将删除当前会话的所有历史消息、运行记录和上下文压缩摘要，无法撤销。产物、附件和
              workspace 文件不会被删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={clearingHistory}
              onClick={() => setClearHistoryOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={clearingHistory || isRunning}
              onClick={() => void confirmClearHistory()}
            >
              {clearingHistory ? '清空中...' : '清空历史'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <SlashCommandHelpDialog
        open={slashHelpOpen}
        commands={SLASH_COMMANDS}
        onOpenChange={setSlashHelpOpen}
      />

      <SlashCommandMenu
        commands={slashTrigger ? filteredSlashCommands : []}
        highlightedIndex={slashHighlight}
        onHighlight={setSlashHighlight}
        onSelect={(command) => void executeSlashCommand(command)}
      />

      {/* @ Mention popup */}
      {trigger && filtered.length > 0 && (
        <div className="absolute bottom-full left-2 right-2 z-20 mx-auto mb-2 max-h-60 max-w-3xl overflow-y-auto rounded-md border bg-popover p-1 shadow-md">
          <div className="px-2 py-1 text-[10px] text-muted-foreground">
            选择 Agent · ↑↓ 切换 · Enter 确认 · Esc 取消
          </div>
          {filtered.map((a, i) => (
            <button
              key={a.id}
              type="button"
              onMouseDown={(e) => {
                // 阻止 textarea 失焦，否则 selectionStart 拿不到正确位置
                e.preventDefault()
                fillMention(a)
              }}
              onMouseEnter={() => setHighlight(i)}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition',
                i === highlight && 'bg-accent',
              )}
            >
              <AgentAvatar agent={a} size="xs" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{a.name}</div>
                <div className="truncate text-[10px] text-muted-foreground">{a.description}</div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 一体 composer：有附件时圆角卡片，无附件时保持胶囊 */}
      <div
        className={cn(
          'mx-auto max-w-3xl border bg-muted/50 shadow-[var(--shadow-sm)] transition-shadow focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10',
          hasComposeAttachments ? 'rounded-2xl' : 'rounded-full',
        )}
      >
        {hasComposeAttachments && (
          <div className="flex flex-wrap gap-2 px-3 pt-3 pb-1">
            {pending.map((a) => (
              <AttachmentChip
                key={a.id}
                attachment={{
                  id: a.id,
                  fileName: a.fileName,
                  size: a.size,
                  mimeType: a.mimeType,
                  kind: a.kind,
                }}
                context="compose"
                onRemove={() => removePending(a.id)}
              />
            ))}
            {uploading.map((u) => (
              <PendingAttachmentChip key={u.tempId} fileName={u.name} />
            ))}
          </div>
        )}

        <div className="flex items-center px-0.5">
          {/* 左侧附件按钮 */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              void handleFiles(e.target.files)
              e.target.value = '' // 允许同名文件再次选择
            }}
          />
          <button
            type="button"
            className="flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
            onClick={() => fileInputRef.current?.click()}
            title="附件 / 图片"
          >
            <Plus className="size-3.5" />
          </button>

          {/* 模型选择器：仅 SDK 会话显示 */}
          {hasSdkAgent && (
            <ModelSelector
              profiles={profileList}
              selectedProfile={selectedProfile}
              onSelect={(id) => setSelectedProfileId(conversationId, id)}
            />
          )}

          {/* 输入框 */}
          <Textarea
            ref={textareaRef}
            data-testid="composer-input"
            value={content}
            onChange={handleChange}
            onSelect={handleSelect}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              planReview
                ? '对计划提修改意见…'
                : isGroup
                  ? '@ 指定 Agent，Enter 发送'
                  : '输入消息…'
            }
            className="min-h-[40px] max-h-28 resize-none border-0 bg-transparent px-2 py-1.5 text-[13px] leading-6 shadow-none focus-visible:ring-0 focus-visible:border-transparent placeholder:text-muted-foreground/60"
            disabled={composerLocked}
          />

          {/* 右侧操作区 */}
          <div className="flex shrink-0 items-center">
            {/* 审批模式开关 */}
            <button
              type="button"
              className="flex size-6 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              onClick={() => void toggleApprovalMode()}
              disabled={modeBusy}
              title={
                approvalMode === 'review'
                  ? 'Review 模式 · 点击切到 Auto'
                  : '⚠ Auto 模式 · 点击切回 Review'
              }
            >
              {approvalMode === 'review' ? (
                <Shield className={cn('size-3', modeBusy && 'opacity-50')} />
              ) : (
                <Zap className={cn('size-3 text-destructive', modeBusy && 'opacity-50')} />
              )}
            </button>

            {isRunning && !composerLocked && (
              <button
                type="button"
                onClick={() => void abortAll()}
                disabled={aborting}
                className="flex size-6 items-center justify-center rounded-full text-destructive hover:bg-destructive/10 transition-colors"
                title="中止全部"
                data-testid="composer-abort"
              >
                <Square className="size-3 fill-current" />
              </button>
            )}

            {/* 发送按钮 */}
            <button
              type="button"
              onClick={() => void submit()}
              disabled={composerLocked || (!content.trim() && pending.length === 0) || sending}
              className="flex size-7 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm transition-all enabled:hover:bg-primary/90 enabled:active:scale-95 disabled:opacity-30 disabled:pointer-events-none"
              title="发送 (Enter)"
              data-testid="composer-send"
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function ModelSelector({
  profiles,
  selectedProfile,
  onSelect,
}: {
  profiles: ModelProfile[]
  selectedProfile: ModelProfile | null
  onSelect: (id: string | null) => void
}) {
  const setSidebarMode = useAppStore((s) => s.setSidebarMode)
  const [open, setOpen] = useState(false)

  // Zero profiles: show prompt to configure
  if (profiles.length === 0) {
    return (
      <button
        type="button"
        onClick={() => setSidebarMode('resources')}
        className="flex shrink-0 items-center gap-1 rounded-full border border-destructive/30 bg-destructive/5 px-2 py-0.5 text-[10px] text-destructive transition hover:bg-destructive/10"
        title="未配置模型档，点击去配置"
      >
        <Cpu className="size-3" />
        <span>配置模型</span>
      </button>
    )
  }

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground"
        title="选择模型档"
      >
        <Cpu className="size-3" />
        <span className="max-w-[80px] truncate">
          {selectedProfile?.name ?? '默认'}
        </span>
      </button>
      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
          />
          <div className="absolute bottom-full left-0 z-50 mb-2 max-h-60 min-w-[180px] overflow-y-auto rounded-md border bg-popover p-1 shadow-md">
            <div className="px-2 py-1 text-[10px] text-muted-foreground">
              选择模型档
            </div>
            {profiles.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  onSelect(p.id)
                  setOpen(false)
                }}
                className={cn(
                  'flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left transition',
                  selectedProfile?.id === p.id && 'bg-accent',
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium">
                    {p.name}
                    {p.isDefault && (
                      <span className="ml-1 text-[9px] text-warning">★</span>
                    )}
                  </div>
                  <div className="truncate text-[10px] text-muted-foreground">
                    {p.provider} / {p.modelId}
                  </div>
                </div>
                {selectedProfile?.id === p.id && (
                  <span className="shrink-0 text-[10px] text-primary">✓</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
