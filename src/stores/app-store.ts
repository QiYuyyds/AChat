'use client'

import { enableMapSet } from 'immer'
import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'

import type { AgentRunRow, AgentRow, ArtifactRow, AttachmentRow, ConversationWithMeta, MessageRow } from '@/db/schema'
import { computeWaves, type ChildRunWaveInfo } from '@/lib/wave-utils'
import type {
  DispatchPlanItem,
  DispatchTaskStatus,
  MessagePart,
  PendingBashCommand,
  PendingDispatchPlan,
  PendingMcpCall,
  PendingQuestion,
  PendingWrite,
  PlanStep,
  StreamEvent,
  TurnMetricData,
} from '@/shared/types'
import { computeTotalTokens, computeMessageTotalTokens } from '@/shared/usage'

enableMapSet()

export type SidebarMode =
  | 'conversations'
  | 'artifacts'
  | 'agents'
  | 'analytics'
  | 'knowledge'
  | 'skills'
  | 'mcp'
  | 'memory'

export type MemoryTab = 'long-term' | 'preferences' | 'session'

/** Workspace env hint card state (per conversation). */
export interface WorkspaceEnvState {
  /** Whether the hint card is visible (language=python, no venv, user hasn't decided). */
  hintVisible: boolean
  /** Current venv creation status: idle / creating / ready / failed. */
  status: 'idle' | 'creating' | 'ready' | 'failed'
  /** Venv path (when status='ready'). */
  venvPath?: string
  /** Error message (when status='failed'). */
  error?: string
}

export interface DispatchState {
  runId: string                                    // Orchestrator 的 runId
  messageId: string                                // 触发 plan 的 Orchestrator message id
  plan: DispatchPlanItem[]
  taskStatus: Record<string, DispatchTaskStatus>
  childRunIds: Record<string, string>              // taskId → childRunId
  reviewStatus?: 'pending' | 'approved' | 'rejected'
  pendingPlanId?: string
  retryInfo?: Record<string, { attempt: number; maxAttempts: number; error?: string }>
  worktreeByTask?: Record<string, {
    branchName: string
    path: string
    mergeStatus?: 'success' | 'conflict'
  }>
}

/** Run state in store — extends DB row with in-memory turn metrics. */
export interface RunState extends AgentRunRow {
  turnMetrics?: Record<number, TurnMetricData>
  turnMetricsComplete?: boolean
  /** Custom ReAct stop reason (optional). */
  stopReason?: string | null
  /** Short Chinese label for abnormal stops (optional). */
  stopReasonLabel?: string | null
}

interface AppState {
  // ─── 实体 ──────────────────────────────────────────
  conversations: Record<string, ConversationWithMeta>
  agents: Record<string, AgentRow>
  messages: Record<string, MessageRow>
  artifacts: Record<string, ArtifactRow>

  // ─── 关系（按 conversationId 分桶）───────────────
  messageIdsByConv: Record<string, string[]>
  runsByConv: Record<string, Record<string, RunState>>

  // 压缩后「当前 ctx」的乐观覆盖值：at 比最新有 usage 的 run/message 更新时生效
  ctxOverrideByConv: Record<string, { tokens: number; at: number }>

  // Orchestrator 的调度状态，按 Orchestrator runId 索引
  dispatchesByRunId: Record<string, DispatchState>

  // ─── 当前会话 ──────────────────────────────────────
  activeConversationId: string | null

  // ─── 产物预览 ──────────────────────────────────────
  previewArtifactId: string | null

  // ─── 右侧文件浏览器面板（与 artifact preview 互斥）─
  fileExplorerOpen: boolean

  // ─── 中间 tab 容器：每个会话的「对话 + 打开的文件 tab」状态 ─
  // tab id: 'chat' 表示主对话；其它是相对 workspace 的文件路径
  openFilesByConv: Record<string, string[]>      // 文件路径列表（按打开顺序）
  activeTabByConv: Record<string, string>        // 当前 tab id

  // ─── 引用回复目标（按 conversationId 分桶）───────
  replyTargetByConv: Record<string, string | null>

  // ─── 选区改写：等待注入到 MessageInput 的引用块（全局，不分会话） ─
  pendingQuoteForInput: {
    text: string
    sourceLabel: string
    /** 选区意图：'ask' 来自聊天消息（就这段提问），'rewrite' 来自 artifact/文件（默认） */
    kind?: 'rewrite' | 'ask'
    /** 可选：选区来自哪个 artifact，方便 agent 用 read_artifact 拿完整上下文 */
    artifactId?: string
    /** 可选：选区来自哪个文件路径 */
    filePath?: string
  } | null

  // ─── 待发送的附件（按 conversationId 分桶）。文件库和 MessageInput 共享。
  pendingAttachmentsByConv: Record<string, AttachmentRow[]>

  // ─── Agent fs_write 审批等待队列（按 conversationId 分桶）─
  pendingWritesByConv: Record<string, PendingWrite[]>

  // ─── Agent bash 关键命令审批等待队列（按 conversationId 分桶）─
  pendingBashCommandsByConv: Record<string, PendingBashCommand[]>

  // ─── Agent ask_user 结构化问答等待队列（按 conversationId 分桶）─
  pendingQuestionsByConv: Record<string, PendingQuestion[]>

  // ─── MCP 工具调用审批等待队列（按 conversationId 分桶）─
  pendingMcpCallsByConv: Record<string, PendingMcpCall[]>

  // ─── Workspace 环境提示卡片状态（按 conversationId 分桶）─
  workspaceEnvByConv: Record<string, WorkspaceEnvState>

  // ─── 未读计数（流式响应到达时，非 active 会话 +1；切到该会话清零）
  unreadByConv: Record<string, number>

  // ─── 移动端 sidebar 抽屉开关 ──
  mobileSidebarOpen: boolean

  // ─── 侧边栏当前模式（conversations / agents / memory 等）──
  sidebarMode: SidebarMode
  setSidebarMode(mode: SidebarMode): void

  // ─── 记忆管理子 Tab（long-term / preferences / session）──
  memoryTab: MemoryTab
  setMemoryTab(tab: MemoryTab): void

  // ─── 知识库当前选中的文档 ID（主视图展示详情）──
  selectedKnowledgeDocId: string | null
  setSelectedKnowledgeDocId(id: string | null): void

  // ─── Guide 悬浮助手（双活跃会话模型）──────────────
  guideConversationId: string | null
  guidePanelState: {
    open: boolean
    position: { x: number; y: number }
    size: { width: number; height: number }
  }
  guideRefreshTargets: Record<string, number> // target → timestamp (用于面板刷新)
  setGuideConversationId(id: string | null): void
  setGuidePanelState(state: Partial<AppState['guidePanelState']>): void
  triggerGuideRefresh(target: string): void

  // ─── 流连接状态 ────────────────────────────────────
  streamConnected: boolean

  // ─── 当前用户 ID（从 AuthStore 同步）──────────────
  userId: string | null
  setUserId(userId: string | null): void

  // ─── actions ───────────────────────────────────────
  setStreamConnected(connected: boolean): void

  setConversations(list: ConversationWithMeta[]): void
  upsertConversation(conv: ConversationWithMeta): void
  removeConversation(id: string): void

  setAgents(list: AgentRow[]): void
  upsertAgent(agent: AgentRow): void
  removeAgent(agentId: string): void

  setMessagesForConversation(conversationId: string, list: MessageRow[]): void
  /** 单条 message upsert（编辑后重发场景：服务端写完 user message，前端要自己塞进 store）。 */
  upsertMessage(message: MessageRow): void
  setActiveConversation(id: string | null): void

  setMobileSidebarOpen(open: boolean): void

  openArtifactPreview(artifactId: string): void
  closeArtifactPreview(): void
  upsertArtifact(artifact: ArtifactRow): void
  removeArtifact(artifactId: string): void
  removeArtifacts(artifactIds: string[]): void

  setFileExplorerOpen(open: boolean): void
  openFile(conversationId: string, path: string): void
  closeFile(conversationId: string, path: string): void
  setActiveTab(conversationId: string, tab: string): void

  setReplyTarget(conversationId: string, messageId: string | null): void

  setPendingQuote(quote: AppState['pendingQuoteForInput']): void

  setBookmarkedMessageIds(conversationId: string, ids: string[]): void

  setPinnedMessageIds(conversationId: string, ids: string[]): void

  /** 批量删除消息（撤回 / 编辑场景）。同时清理 messageIdsByConv 对应桶 + replyTarget。 */
  removeMessages(conversationId: string, messageIds: string[]): void
  clearConversationHistory(conversationId: string, conversation: ConversationWithMeta): void

  /** 压缩后乐观刷新「当前 ctx」：写入按会话隔离的覆盖值，下一次真实 run 用实测值接管。 */
  setCtxOverride(conversationId: string, tokens: number, at: number): void

  addPendingAttachment(conversationId: string, attachment: AttachmentRow): void
  removePendingAttachment(conversationId: string, attachmentId: string): void
  clearPendingAttachments(conversationId: string): void

  setPendingWritesForConversation(conversationId: string, list: PendingWrite[]): void

  setPendingBashCommandsForConversation(
    conversationId: string,
    list: PendingBashCommand[],
  ): void

  setPendingQuestionsForConversation(conversationId: string, list: PendingQuestion[]): void

  setPendingMcpCallsForConversation(conversationId: string, list: PendingMcpCall[]): void

  setPendingDispatchPlansForConversation(
    conversationId: string,
    list: PendingDispatchPlan[],
  ): void

  /** 高亮指定消息 1.5 秒（点击「引用」预览时的跳转反馈） */
  highlightedMessageId: string | null
  highlightMessage(messageId: string): void

  addLocalUserMessage(args: {
    tempId: string
    conversationId: string
    content: string
    mentionedAgentIds: string[]
    parentMessageId?: string | null
    attachments?: AttachmentRow[]
  }): void
  replaceLocalMessageId(tempId: string, realId: string): void

  applyEvent(event: StreamEvent): void
}

export const useAppStore = create<AppState>()(
  immer((set) => ({
    conversations: {},
    agents: {},
    messages: {},
    artifacts: {},
    messageIdsByConv: {},
    runsByConv: {},
    ctxOverrideByConv: {},
    dispatchesByRunId: {},
    activeConversationId: null,
    previewArtifactId: null,
    fileExplorerOpen: false,
    openFilesByConv: {},
    activeTabByConv: {},
    replyTargetByConv: {},
    pendingAttachmentsByConv: {},
    pendingWritesByConv: {},
    pendingBashCommandsByConv: {},
    pendingQuestionsByConv: {},
    pendingMcpCallsByConv: {},
    workspaceEnvByConv: {},
    unreadByConv: {},
    mobileSidebarOpen: false,
    sidebarMode: 'conversations',
    memoryTab: 'long-term',
    selectedKnowledgeDocId: null,
    pendingQuoteForInput: null,
    highlightedMessageId: null,
    guideConversationId: null,
    guidePanelState: {
      open: false,
      position: { x: 16, y: 16 },
      size: { width: 400, height: 600 },
    },
    guideRefreshTargets: {},
    streamConnected: false,
    userId: null,

    setGuideConversationId: (id) =>
      set((s) => {
        s.guideConversationId = id
      }),

    setGuidePanelState: (partial) =>
      set((s) => {
        Object.assign(s.guidePanelState, partial)
      }),

    triggerGuideRefresh: (target) =>
      set((s) => {
        s.guideRefreshTargets[target] = Date.now()
      }),

    setStreamConnected: (connected) =>
      set((s) => {
        s.streamConnected = connected
      }),

    setUserId: (userId) =>
      set((s) => {
        s.userId = userId
      }),

    setConversations: (list) =>
      set((s) => {
        for (const c of list) s.conversations[c.id] = c
      }),

    upsertConversation: (conv) =>
      set((s) => {
        s.conversations[conv.id] = conv
      }),

    removeConversation: (id) =>
      set((s) => {
        delete s.conversations[id]
        // 清理该会话所有消息
        const msgIds = s.messageIdsByConv[id] ?? []
        for (const mid of msgIds) delete s.messages[mid]
        delete s.messageIdsByConv[id]
        delete s.runsByConv[id]
        delete s.ctxOverrideByConv[id]
        delete s.pendingWritesByConv[id]
        delete s.pendingBashCommandsByConv[id]
        delete s.pendingQuestionsByConv[id]
        delete s.pendingMcpCallsByConv[id]
        delete s.workspaceEnvByConv[id]
        if (s.activeConversationId === id) s.activeConversationId = null
      }),

    setAgents: (list) =>
      set((s) => {
        for (const a of list) s.agents[a.id] = a
      }),

    upsertAgent: (agent) =>
      set((s) => {
        s.agents[agent.id] = agent
      }),

    removeAgent: (agentId) =>
      set((s) => {
        delete s.agents[agentId]
      }),

    setMessagesForConversation: (conversationId, list) =>
      set((s) => {
        const nextIds = list.map((m) => m.id)
        if (!areStringArraysEqual(s.messageIdsByConv[conversationId], nextIds)) {
          s.messageIdsByConv[conversationId] = nextIds
        }
        for (const m of list) {
          const existing = s.messages[m.id]
          if (!existing || !areMessagesEquivalent(existing, m)) {
            s.messages[m.id] = m
          }
          attachDispatchToMessageForRun(s.dispatchesByRunId, m.runId, m.id)
        }
      }),

    upsertMessage: (message) =>
      set((s) => {
        s.messages[message.id] = message
        const bucket = (s.messageIdsByConv[message.conversationId] ??= [])
        if (!bucket.includes(message.id)) bucket.push(message.id)
        attachDispatchToMessageForRun(s.dispatchesByRunId, message.runId, message.id)
      }),

    setActiveConversation: (id) =>
      set((s) => {
        s.activeConversationId = id
        // 切到该会话即视为已读
        if (id) delete s.unreadByConv[id]
        // 切会话时自动收起移动 sidebar
        if (id) s.mobileSidebarOpen = false
      }),

    setMobileSidebarOpen: (open) =>
      set((s) => {
        s.mobileSidebarOpen = open
      }),

    setSidebarMode: (mode) =>
      set((s) => {
        s.sidebarMode = mode
      }),

    setMemoryTab: (tab) =>
      set((s) => {
        s.memoryTab = tab
      }),

    setSelectedKnowledgeDocId: (id) =>
      set((s) => {
        s.selectedKnowledgeDocId = id
      }),

    setPendingQuote: (quote) =>
      set((s) => {
        s.pendingQuoteForInput = quote
      }),

    openArtifactPreview: (artifactId) =>
      set((s) => {
        s.previewArtifactId = artifactId
        s.fileExplorerOpen = false // 与文件浏览器互斥
      }),

    closeArtifactPreview: () =>
      set((s) => {
        s.previewArtifactId = null
      }),

    setFileExplorerOpen: (open) =>
      set((s) => {
        s.fileExplorerOpen = open
        if (open) s.previewArtifactId = null // 与 artifact preview 互斥
      }),

    openFile: (conversationId, filePath) =>
      set((s) => {
        const list = s.openFilesByConv[conversationId] ?? []
        if (!list.includes(filePath)) {
          s.openFilesByConv[conversationId] = [...list, filePath]
        }
        s.activeTabByConv[conversationId] = filePath
      }),

    closeFile: (conversationId, filePath) =>
      set((s) => {
        const list = s.openFilesByConv[conversationId]
        if (!list) return
        const next = list.filter((p) => p !== filePath)
        if (next.length === 0) {
          delete s.openFilesByConv[conversationId]
        } else {
          s.openFilesByConv[conversationId] = next
        }
        // 若关掉的是当前 active，切回 chat
        if (s.activeTabByConv[conversationId] === filePath) {
          s.activeTabByConv[conversationId] = 'chat'
        }
      }),

    setActiveTab: (conversationId, tab) =>
      set((s) => {
        s.activeTabByConv[conversationId] = tab
      }),

    upsertArtifact: (artifact) =>
      set((s) => {
        s.artifacts[artifact.id] = artifact
      }),

    removeArtifact: (artifactId) =>
      set((s) => {
        delete s.artifacts[artifactId]
        if (s.previewArtifactId === artifactId) s.previewArtifactId = null
      }),

    removeArtifacts: (artifactIds) =>
      set((s) => {
        for (const id of artifactIds) {
          delete s.artifacts[id]
          if (s.previewArtifactId === id) s.previewArtifactId = null
        }
      }),

    removeMessages: (conversationId, messageIds) =>
      set((s) => {
        const toRemove = new Set(messageIds)
        for (const id of toRemove) delete s.messages[id]

        const bucket = s.messageIdsByConv[conversationId]
        if (bucket) {
          s.messageIdsByConv[conversationId] = bucket.filter((id) => !toRemove.has(id))
        }

        // 清理可能指向被删消息的 replyTarget
        const replyId = s.replyTargetByConv[conversationId]
        if (replyId && toRemove.has(replyId)) {
          delete s.replyTargetByConv[conversationId]
        }
      }),

    clearConversationHistory: (conversationId, conversation) =>
      set((s) => {
        const messageIds = new Set(s.messageIdsByConv[conversationId] ?? [])
        for (const id of messageIds) delete s.messages[id]
        s.messageIdsByConv[conversationId] = []

        const runIds = new Set(Object.keys(s.runsByConv[conversationId] ?? {}))
        for (const runId of runIds) delete s.dispatchesByRunId[runId]
        for (const runId in s.dispatchesByRunId) {
          if (messageIds.has(s.dispatchesByRunId[runId].messageId)) {
            delete s.dispatchesByRunId[runId]
          }
        }

        delete s.runsByConv[conversationId]
        delete s.ctxOverrideByConv[conversationId]
        delete s.replyTargetByConv[conversationId]
        delete s.pendingWritesByConv[conversationId]
        delete s.pendingBashCommandsByConv[conversationId]
        delete s.pendingQuestionsByConv[conversationId]
        delete s.pendingMcpCallsByConv[conversationId]
        delete s.unreadByConv[conversationId]
        delete s.workspaceEnvByConv[conversationId]
        if (s.highlightedMessageId && messageIds.has(s.highlightedMessageId)) {
          s.highlightedMessageId = null
        }
        s.conversations[conversationId] = conversation
      }),

    setCtxOverride: (conversationId, tokens, at) =>
      set((s) => {
        s.ctxOverrideByConv[conversationId] = { tokens, at }
      }),

    setReplyTarget: (conversationId, messageId) =>
      set((s) => {
        if (messageId) s.replyTargetByConv[conversationId] = messageId
        else delete s.replyTargetByConv[conversationId]
      }),

    setBookmarkedMessageIds: (conversationId, ids) =>
      set((s) => {
        const conv = s.conversations[conversationId]
        if (conv) conv.bookmarkedMessageIds = ids
      }),

    setPinnedMessageIds: (conversationId, ids) =>
      set((s) => {
        const conv = s.conversations[conversationId]
        if (conv) conv.pinnedMessageIds = ids
      }),

    addPendingAttachment: (conversationId, attachment) =>
      set((s) => {
        const list = s.pendingAttachmentsByConv[conversationId] ?? []
        if (list.some((a) => a.id === attachment.id)) return
        s.pendingAttachmentsByConv[conversationId] = [...list, attachment]
      }),

    removePendingAttachment: (conversationId, attachmentId) =>
      set((s) => {
        const list = s.pendingAttachmentsByConv[conversationId]
        if (!list) return
        const next = list.filter((a) => a.id !== attachmentId)
        if (next.length === 0) delete s.pendingAttachmentsByConv[conversationId]
        else s.pendingAttachmentsByConv[conversationId] = next
      }),

    clearPendingAttachments: (conversationId) =>
      set((s) => {
        delete s.pendingAttachmentsByConv[conversationId]
      }),

    setPendingWritesForConversation: (conversationId, list) =>
      set((s) => {
        if (list.length === 0) delete s.pendingWritesByConv[conversationId]
        else s.pendingWritesByConv[conversationId] = list
      }),

    setPendingBashCommandsForConversation: (conversationId, list) =>
      set((s) => {
        if (list.length === 0) delete s.pendingBashCommandsByConv[conversationId]
        else s.pendingBashCommandsByConv[conversationId] = list
      }),

    setPendingQuestionsForConversation: (conversationId, list) =>
      set((s) => {
        if (list.length === 0) delete s.pendingQuestionsByConv[conversationId]
        else s.pendingQuestionsByConv[conversationId] = list
      }),

    setPendingMcpCallsForConversation: (conversationId, list) =>
      set((s) => {
        if (list.length === 0) delete s.pendingMcpCallsByConv[conversationId]
        else s.pendingMcpCallsByConv[conversationId] = list
      }),

    setPendingDispatchPlansForConversation: (conversationId, list) =>
      set((s) => {
        for (const pending of list) {
          if (pending.conversationId !== conversationId) continue
          const status: DispatchState['taskStatus'] = {}
          for (const task of pending.plan) status[task.id] = 'pending'
          const existing = s.dispatchesByRunId[pending.runId]
          s.dispatchesByRunId[pending.runId] = {
            runId: pending.runId,
            messageId:
              existing?.messageId ||
              findLatestAgentMessageIdForRun(s.messages, pending.runId),
            plan: pending.plan,
            taskStatus: status,
            childRunIds: existing?.childRunIds ?? {},
            reviewStatus: 'pending',
            pendingPlanId: pending.id,
          }
        }
      }),

    highlightMessage: (messageId) => {
      set((s) => {
        s.highlightedMessageId = messageId
      })
      setTimeout(() => {
        // 仅在仍是同一目标时清除（避免连续点击的竞态）
        const current = useAppStore.getState().highlightedMessageId
        if (current === messageId) {
          useAppStore.setState((s) => {
            s.highlightedMessageId = null
          })
        }
      }, 2000)
    },

    addLocalUserMessage: ({ tempId, conversationId, content, mentionedAgentIds, parentMessageId, attachments }) =>
      set((s) => {
        const parts: MessagePart[] = []
        if (content) parts.push({ type: 'text', content })
        for (const a of attachments ?? []) {
          parts.push(
            a.kind === 'image'
              ? {
                  type: 'image_attachment',
                  attachmentId: a.id,
                  fileName: a.fileName,
                  size: a.size,
                  mimeType: a.mimeType,
                }
              : {
                  type: 'file_attachment',
                  attachmentId: a.id,
                  fileName: a.fileName,
                  size: a.size,
                  mimeType: a.mimeType,
                },
          )
        }
        s.messages[tempId] = {
          id: tempId,
          conversationId,
          role: 'user',
          agentId: null,
          parts,
          status: 'complete',
          parentMessageId: parentMessageId ?? null,
          mentionedAgentIds,
          runId: null,
          usage: null,
          hidden: false,
          createdAt: Date.now(),
        }
        s.messageIdsByConv[conversationId] ??= []
        s.messageIdsByConv[conversationId].push(tempId)
      }),

    replaceLocalMessageId: (tempId, realId) =>
      set((s) => {
        const msg = s.messages[tempId]
        if (!msg) return
        // realId 可能已被 message.added（SSE 早于 POST 返回）抢先插入：别覆盖权威行，也别在桶里留重复
        if (!s.messages[realId]) s.messages[realId] = { ...msg, id: realId }
        delete s.messages[tempId]
        for (const convId in s.messageIdsByConv) {
          const arr = s.messageIdsByConv[convId]
          const idx = arr.indexOf(tempId)
          if (idx < 0) continue
          if (arr.includes(realId)) arr.splice(idx, 1)
          else arr[idx] = realId
        }
      }),

    applyEvent: (event) =>
      set((s) => {
        switch (event.type) {
          case 'heartbeat':
            return

          case 'run.start': {
            s.runsByConv[event.conversationId] ??= {}
            s.runsByConv[event.conversationId][event.runId] = {
              id: event.runId,
              conversationId: event.conversationId,
              agentId: event.agentId,
              triggerMessageId: event.triggerMessageId,
              status: 'running',
              error: null,
              parentRunId: event.parentRunId ?? null,
              usage: null,
              startedAt: event.timestamp,
              finishedAt: null,
              turnMetrics: {},
              turnMetricsComplete: false,
              stopReason: null,
              stopReasonLabel: null,
            }
            return
          }

          case 'run.end': {
            const run = s.runsByConv[event.conversationId]?.[event.runId]
            if (run) {
              run.status = event.status
              run.finishedAt = event.timestamp
              run.error = event.error ?? null
              run.turnMetricsComplete = true
              if ('stopReason' in event) {
                run.stopReason = event.stopReason ?? null
              }
              if ('stopReasonLabel' in event) {
                run.stopReasonLabel = event.stopReasonLabel ?? null
              }
            }
            if (event.status === 'failed' || event.status === 'aborted') {
              closeUnresolvedToolCallsForRun(
                s.messages,
                event.conversationId,
                event.runId,
                event.status,
                event.error,
              )
            }
            return
          }

          case 'run.usage': {
            const run = s.runsByConv[event.conversationId]?.[event.runId]
            if (run) run.usage = event.usage
            return
          }

          case 'turn.metric': {
            const run = s.runsByConv[event.conversationId]?.[event.runId]
            if (run) {
              run.turnMetrics ??= {}
              run.turnMetrics[event.turn] = {
                turn: event.turn,
                tokens: event.tokens,
                toolCalls: event.toolCalls,
                durationMs: event.durationMs,
              }
            }
            return
          }

          case 'message.usage': {
            const msg = s.messages[event.messageId]
            if (msg) msg.usage = event.usage
            return
          }

          case 'message.start': {
            // 新 agent 消息（DB 端也插入了同 id 的行，前端再次接到是 idempotent）
            s.messages[event.messageId] = {
              id: event.messageId,
              conversationId: event.conversationId,
              role: 'agent',
              agentId: event.agentId,
              parts: [],
              status: 'streaming',
              parentMessageId: null,
              mentionedAgentIds: [],
              runId: event.runId,
              usage: null,
              hidden: false,
              createdAt: event.timestamp,
            }
            s.messageIdsByConv[event.conversationId] ??= []
            if (!s.messageIdsByConv[event.conversationId].includes(event.messageId)) {
              s.messageIdsByConv[event.conversationId].push(event.messageId)
            }
            attachDispatchToMessageForRun(s.dispatchesByRunId, event.runId, event.messageId)
            // 未读 +1 不在 message.start 触发：claude-code-adapter 整个 run 只发一次 message.start
            // 且发生时用户通常仍在该会话（被 activeConversationId === conv 抑制），导致后续切走再也不计未读。
            // 改在 message.end 触发，两个 adapter 都能可靠 +1，且每个 msg 仅 +1 一次。
            return
          }

          case 'message.end': {
            const msg = s.messages[event.messageId]
            if (msg) msg.status = 'complete'
            // agent 消息完成时 +1 未读；用户当前在该会话则不计入。
            if (s.activeConversationId !== event.conversationId) {
              s.unreadByConv[event.conversationId] =
                (s.unreadByConv[event.conversationId] ?? 0) + 1
            }
            return
          }

          case 'message.added': {
            // 其它客户端创建的用户消息（如手机端发、桌面端在看）。按 id 幂等 upsert：
            // 发送方自己已对账过同 id，这里无副作用；第二个客户端靠这条插入。
            s.messages[event.message.id] = { ...event.message, hidden: event.message.hidden ?? false }
            s.messageIdsByConv[event.message.conversationId] ??= []
            if (!s.messageIdsByConv[event.message.conversationId].includes(event.message.id)) {
              s.messageIdsByConv[event.message.conversationId].push(event.message.id)
            }
            return
          }

          case 'message.removed': {
            // 撤回 / 编辑 / 重新生成在别处删了消息（及其产物）。幂等移除：发起方已删过则无副作用。
            const toRemove = new Set(event.messageIds)
            for (const id of toRemove) delete s.messages[id]
            const bucket = s.messageIdsByConv[event.conversationId]
            if (bucket) {
              s.messageIdsByConv[event.conversationId] = bucket.filter((id) => !toRemove.has(id))
            }
            const replyId = s.replyTargetByConv[event.conversationId]
            if (replyId && toRemove.has(replyId)) delete s.replyTargetByConv[event.conversationId]
            for (const id of event.artifactIds) {
              delete s.artifacts[id]
              if (s.previewArtifactId === id) s.previewArtifactId = null
            }
            return
          }

          case 'part.start': {
            const msg = s.messages[event.messageId]
            if (!msg) return
            const part = event.part
            if (part.type === 'thinking' && part.startedAt === undefined) {
              part.startedAt = event.timestamp
            }
            msg.parts[event.partIndex] = part
            return
          }

          case 'part.delta': {
            const msg = s.messages[event.messageId]
            if (!msg) return
            const part = msg.parts[event.partIndex]
            if (!part) return
            if (event.delta.type === 'text.append' && part.type === 'text') {
              part.content += event.delta.text
            } else if (event.delta.type === 'thinking.append' && part.type === 'thinking') {
              part.content += event.delta.text
            } else if (event.delta.type === 'code.append' && part.type === 'code') {
              part.content += event.delta.text
            } else if (event.delta.type === 'file_write_preview.append' && part.type === 'file_write_preview') {
              part.content += event.delta.text
            }
            return
          }

          case 'part.end': {
            const msg = s.messages[event.messageId]
            if (!msg) return
            const part = msg.parts[event.partIndex]
            if (part && part.type === 'thinking') {
              part.endedAt = event.timestamp
            }
            return
          }

          case 'tool.call': {
            const msg = s.messages[event.messageId]
            if (!msg) return
            msg.parts.push({
              type: 'tool_use',
              callId: event.callId,
              toolName: event.toolName,
              args: event.args,
              startedAt: event.timestamp,
            })
            return
          }

          case 'tool.result': {
            const msg = s.messages[event.messageId]
            if (!msg) return
            const existing = msg.parts.find(
              (part) => part.type === 'tool_result' && part.callId === event.callId,
            )
            if (existing?.type === 'tool_result') {
              existing.result = event.result
              existing.isError = event.isError
              existing.endedAt = event.timestamp
              return
            }
            msg.parts.push({
              type: 'tool_result',
              callId: event.callId,
              result: event.result,
              isError: event.isError,
              endedAt: event.timestamp,
            })
            return
          }

          case 'artifact.create': {
            const a = event.artifact
            s.artifacts[a.id] = {
              ...a,
              parentArtifactId: a.parentArtifactId ?? null,
            }
            return
          }

          case 'artifact.update': {
            const art = s.artifacts[event.artifactId]
            if (!art) return
            art.content = { ...art.content, ...(event.patch as object) } as typeof art.content
            return
          }

          case 'plan.step_update': {
            // Find execution_plan part by planId in the current message and replace its steps
            for (const msg of Object.values(s.messages)) {
              const planPart = msg.parts.find(
                (p) => p.type === 'execution_plan' && p.planId === event.planId,
              )
              if (planPart && planPart.type === 'execution_plan') {
                planPart.steps = event.steps
                break
              }
            }
            return
          }

          case 'file_write_preview.complete': {
            // Find file_write_preview part by callId and update status/diff data
            for (const msg of Object.values(s.messages)) {
              const previewPart = msg.parts.find(
                (p) => p.type === 'file_write_preview' && p.callId === event.callId,
              )
              if (previewPart && previewPart.type === 'file_write_preview') {
                previewPart.status = event.status
                previewPart.path = event.path
                previewPart.oldContent = event.oldContent
                previewPart.newContent = event.newContent
                break
              }
            }
            return
          }

          case 'dispatch.plan.pending': {
            const pending = event.pendingPlan
            const status: DispatchState['taskStatus'] = {}
            for (const t of pending.plan) status[t.id] = 'pending'
            const existing = s.dispatchesByRunId[pending.runId]
            s.dispatchesByRunId[pending.runId] = {
              runId: pending.runId,
              // 跟随该 run「最新」的规划消息：revise 重排会产出新的一条 Orchestrator 消息（同 runId），
              // 计划卡片随之落到新气泡，而不是钉在第一版那条上。
              messageId:
                findLatestAgentMessageIdForRun(s.messages, pending.runId) ||
                existing?.messageId ||
                '',
              plan: pending.plan,
              taskStatus: status,
              childRunIds: existing?.childRunIds ?? {},
              reviewStatus: 'pending',
              pendingPlanId: pending.id,
            }
            return
          }

          case 'dispatch.plan.resolved': {
            const dispatch = s.dispatchesByRunId[event.runId]
            if (!dispatch) return
            if (dispatch.pendingPlanId === event.pendingId) delete dispatch.pendingPlanId
            // revising：只清掉当前 pending（计划卡先回落到只读），等 Orchestrator 重排发来新的
            // dispatch.plan.pending 再变回审批态；不要置成 rejected。
            if (!event.revising) dispatch.reviewStatus = event.approved ? 'approved' : 'rejected'
            return
          }

          case 'dispatch.plan': {
            const existing = s.dispatchesByRunId[event.runId]
            const status: DispatchState['taskStatus'] = {}
            for (const t of event.plan) status[t.id] = 'pending'
            s.dispatchesByRunId[event.runId] = {
              runId: event.runId,
              messageId:
                existing?.messageId ||
                findLatestAgentMessageIdForRun(s.messages, event.runId),
              plan: event.plan,
              taskStatus: status,
              childRunIds: existing?.childRunIds ?? {},
              reviewStatus: 'approved',
            }
            return
          }

          case 'dispatch.start': {
            const d = s.dispatchesByRunId[event.parentRunId]
            if (!d) return
            d.taskStatus[event.taskId] = 'running'
            d.childRunIds[event.taskId] = event.childRunId
            return
          }

          case 'dispatch.retry': {
            const d = s.dispatchesByRunId[event.parentRunId]
            if (!d) return
            d.retryInfo ??= {}
            d.retryInfo[event.taskId] = {
              attempt: event.attempt,
              maxAttempts: event.maxAttempts,
              error: event.error,
            }
            return
          }

          case 'dispatch.end': {
            const direct = s.dispatchesByRunId[event.parentRunId]
            if (direct) {
              direct.taskStatus[event.taskId] = event.status
              if (event.childRunId) direct.childRunIds[event.taskId] = event.childRunId
              if (direct.retryInfo) delete direct.retryInfo[event.taskId]
              return
            }

            // 兼容旧事件形态：如果没有找到 parentRunId，再通过 childRunId 反查
            for (const d of Object.values(s.dispatchesByRunId)) {
              if (event.childRunId && d.childRunIds[event.taskId] === event.childRunId) {
                d.taskStatus[event.taskId] = event.status
                return
              }
            }
            return
          }

          case 'fs_write.pending': {
            const list = s.pendingWritesByConv[event.conversationId] ?? []
            if (list.some((p) => p.id === event.pendingWrite.id)) return
            s.pendingWritesByConv[event.conversationId] = [...list, event.pendingWrite]
            return
          }

          case 'fs_write.resolved': {
            const list = s.pendingWritesByConv[event.conversationId]
            if (!list) return
            const next = list.filter((p) => p.id !== event.pendingId)
            if (next.length === 0) delete s.pendingWritesByConv[event.conversationId]
            else s.pendingWritesByConv[event.conversationId] = next
            return
          }

          case 'bash_command.pending': {
            const list = s.pendingBashCommandsByConv[event.conversationId] ?? []
            if (list.some((p) => p.id === event.pendingCommand.id)) return
            s.pendingBashCommandsByConv[event.conversationId] = [...list, event.pendingCommand]
            return
          }

          case 'bash_command.resolved': {
            const list = s.pendingBashCommandsByConv[event.conversationId]
            if (!list) return
            const next = list.filter((p) => p.id !== event.pendingId)
            if (next.length === 0) delete s.pendingBashCommandsByConv[event.conversationId]
            else s.pendingBashCommandsByConv[event.conversationId] = next
            return
          }

          case 'ask_user.pending': {
            const list = s.pendingQuestionsByConv[event.conversationId] ?? []
            if (list.some((q) => q.id === event.pendingQuestion.id)) return
            s.pendingQuestionsByConv[event.conversationId] = [...list, event.pendingQuestion]
            return
          }

          case 'ask_user.resolved': {
            const list = s.pendingQuestionsByConv[event.conversationId]
            if (!list) return
            const next = list.filter((q) => q.id !== event.pendingId)
            if (next.length === 0) delete s.pendingQuestionsByConv[event.conversationId]
            else s.pendingQuestionsByConv[event.conversationId] = next
            return
          }

          case 'mcp_call.pending': {
            const list = s.pendingMcpCallsByConv[event.conversationId] ?? []
            if (list.some((c) => c.id === event.pendingCall.id)) return
            s.pendingMcpCallsByConv[event.conversationId] = [...list, event.pendingCall]
            return
          }

          case 'mcp_call.resolved': {
            const list = s.pendingMcpCallsByConv[event.conversationId]
            if (!list) return
            const next = list.filter((c) => c.id !== event.pendingId)
            if (next.length === 0) delete s.pendingMcpCallsByConv[event.conversationId]
            else s.pendingMcpCallsByConv[event.conversationId] = next
            return
          }

          case 'worktree.created': {
            for (const d of Object.values(s.dispatchesByRunId)) {
              if (d.taskStatus[event.taskId] !== undefined) {
                d.worktreeByTask ??= {}
                d.worktreeByTask[event.taskId] = {
                  branchName: event.branchName ?? '',
                  path: event.path ?? '',
                }
                return
              }
            }
            return
          }

          case 'worktree.merged': {
            for (const d of Object.values(s.dispatchesByRunId)) {
              if (d.worktreeByTask?.[event.taskId]) {
                d.worktreeByTask[event.taskId].mergeStatus = event.mergeStatus ?? 'success'
                if (event.mergeStatus === 'conflict') {
                  d.taskStatus[event.taskId] = 'merge_conflict'
                }
                return
              }
            }
            return
          }

          case 'worktree.cleaned': {
            for (const d of Object.values(s.dispatchesByRunId)) {
              if (d.worktreeByTask) delete d.worktreeByTask[event.taskId]
            }
            return
          }

          case 'summary.updated': {
            const conv = s.conversations[event.conversationId]
            if (conv) conv.summary = event.summary
            return
          }

          case 'workspace_env_hint': {
            // Idempotent: only show the hint if the user hasn't already decided.
            const existing = s.workspaceEnvByConv[event.conversationId]
            if (existing && existing.status !== 'idle') return
            s.workspaceEnvByConv[event.conversationId] = {
              hintVisible: true,
              status: 'idle',
            }
            return
          }

          case 'workspace_env_status': {
            const prev = s.workspaceEnvByConv[event.conversationId]
            // If the user already dismissed the hint (env_preference set),
            // don't re-show it — but still update status for in-flight creation.
            const hintVisible = prev?.hintVisible ?? false
            if (event.status === 'creating') {
              s.workspaceEnvByConv[event.conversationId] = {
                hintVisible: true,
                status: 'creating',
              }
            } else if (event.status === 'ready') {
              s.workspaceEnvByConv[event.conversationId] = {
                hintVisible: false,
                status: 'ready',
                venvPath: event.venvPath,
              }
            } else {
              // failed
              s.workspaceEnvByConv[event.conversationId] = {
                hintVisible: hintVisible,
                status: 'failed',
                error: event.error,
              }
            }
            return
          }

          case 'guide_side_effect': {
            s.guideRefreshTargets[event.target] = Date.now()
            return
          }

          default:
            return
        }
      }),
  })),
)

// ─── 派生 hooks ──────────────────────────────────────
// 用 useShallow 防止派生数组每次新引用导致无限渲染（Zustand 5 标准做法）。
import { useShallow } from 'zustand/react/shallow'
function findLatestAgentMessageIdForRun(
  messages: Record<string, MessageRow>,
  runId: string,
): string {
  let attachMsgId = ''
  let attachCreated = -1
  for (const message of Object.values(messages)) {
    if (
      message.runId === runId &&
      message.role === 'agent' &&
      message.createdAt > attachCreated
    ) {
      attachMsgId = message.id
      attachCreated = message.createdAt
    }
  }
  return attachMsgId
}

function attachDispatchToMessageForRun(
  dispatches: Record<string, DispatchState>,
  runId: string | null,
  messageId: string,
): void {
  if (!runId) return
  const dispatch = dispatches[runId]
  if (dispatch && !dispatch.messageId) dispatch.messageId = messageId
}

function closeUnresolvedToolCallsForRun(
  messages: Record<string, MessageRow>,
  conversationId: string,
  runId: string,
  status: 'failed' | 'aborted',
  error?: string,
): void {
  const result = buildUnresolvedToolResult(status, error)
  const messageStatus = status === 'aborted' ? 'aborted' : 'error'

  for (const message of Object.values(messages)) {
    if (message.conversationId !== conversationId || message.runId !== runId) continue
    if (message.status === 'streaming') message.status = messageStatus

    const completedCallIds = new Set<string>()
    for (const part of message.parts) {
      if (part.type === 'tool_result') completedCallIds.add(part.callId)
    }

    for (const part of message.parts) {
      if (part.type !== 'tool_use' || completedCallIds.has(part.callId)) continue
      message.parts.push({
        type: 'tool_result',
        callId: part.callId,
        result,
        isError: true,
      })
      completedCallIds.add(part.callId)
    }
  }
}

function buildUnresolvedToolResult(status: 'failed' | 'aborted', error?: string): string {
  if (status === 'aborted') return '工具调用未完成：本次运行已中止。'
  return error
    ? `工具调用未完成：本次运行失败。${error}`
    : '工具调用未完成：本次运行失败。'
}

function areMessagesEquivalent(a: MessageRow, b: MessageRow): boolean {
  if (a === b) return true
  return (
    a.id === b.id &&
    a.conversationId === b.conversationId &&
    a.role === b.role &&
    a.agentId === b.agentId &&
    a.status === b.status &&
    a.parentMessageId === b.parentMessageId &&
    a.runId === b.runId &&
    a.createdAt === b.createdAt &&
    areStringArraysEqual(a.mentionedAgentIds, b.mentionedAgentIds) &&
    areMessageUsageEqual(a.usage, b.usage) &&
    areMessagePartsEqual(a.parts, b.parts)
  )
}

function areStringArraysEqual(a: readonly string[] | undefined, b: readonly string[]): boolean {
  if (!a || a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

function areMessageUsageEqual(a: MessageRow['usage'], b: MessageRow['usage']): boolean {
  if (a === b) return true
  if (!a || !b) return a === b
  return (
    a.inputTokens === b.inputTokens &&
    a.outputTokens === b.outputTokens &&
    a.cacheReadTokens === b.cacheReadTokens
  )
}

function areMessagePartsEqual(a: readonly MessagePart[], b: readonly MessagePart[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (!areMessagePartsEquivalent(a[i], b[i])) return false
  }
  return true
}

function areMessagePartsEquivalent(a: MessagePart, b: MessagePart): boolean {
  if (a === b) return true
  if (a.type !== b.type) return false
  switch (a.type) {
    case 'text':
      return b.type === 'text' && a.content === b.content
    case 'thinking':
      return b.type === 'thinking' && a.content === b.content && a.startedAt === b.startedAt && a.endedAt === b.endedAt
    case 'code':
      return b.type === 'code' && a.language === b.language && a.content === b.content
    case 'tool_use':
      return (
        b.type === 'tool_use' &&
        a.callId === b.callId &&
        a.toolName === b.toolName &&
        a.startedAt === b.startedAt &&
        areUnknownValuesEquivalent(a.args, b.args)
      )
    case 'tool_result':
      return (
        b.type === 'tool_result' &&
        a.callId === b.callId &&
        a.isError === b.isError &&
        a.endedAt === b.endedAt &&
        areUnknownValuesEquivalent(a.result, b.result)
      )
    case 'artifact_ref':
      return b.type === 'artifact_ref' && a.artifactId === b.artifactId
    case 'deploy_status':
      return (
        b.type === 'deploy_status' &&
        areUnknownValuesEquivalent(a.deployment, b.deployment)
      )
    case 'execution_plan':
      return (
        b.type === 'execution_plan' &&
        a.planId === b.planId &&
        a.steps === b.steps
      )
    case 'deploy_candidates':
      return (
        b.type === 'deploy_candidates' &&
        areUnknownValuesEquivalent(a.candidates, b.candidates)
      )
    case 'image_attachment':
    case 'file_attachment':
      return (
        (b.type === 'image_attachment' || b.type === 'file_attachment') &&
        a.type === b.type &&
        a.attachmentId === b.attachmentId &&
        a.fileName === b.fileName &&
        a.size === b.size &&
        a.mimeType === b.mimeType
      )
    default:
      return false
  }
}

function areUnknownValuesEquivalent(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== typeof b) return false
  if (a === null || b === null || typeof a !== 'object') return false
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

import { useMemo } from 'react'

export const useMessagesForConversation = (conversationId: string) =>
  useAppStore(
    useShallow((s) =>
      (s.messageIdsByConv[conversationId] ?? [])
        .map((id) => s.messages[id])
        .filter((m) => m && !m.hidden),
    ),
  )

/** 当前会话 pin 的消息（按 pinnedMessageIds 数组顺序，即用户 pin 的时间顺序）。 */
export const usePinnedMessagesForConversation = (conversationId: string) =>
  useAppStore(
    useShallow((s) => {
      const ids = s.conversations[conversationId]?.pinnedMessageIds ?? []
      return ids.map((id) => s.messages[id]).filter(Boolean)
    }),
  )

/** childRunId → { wave, taskId, orchestratorRunId, agentId, planIndex }，用于 MessageList 做 wave 分列 */
export const useChildRunWaveMap = (conversationId: string): Record<string, ChildRunWaveInfo> => {
  // Select raw store references (stable under immer — only change when data changes).
  // Avoid useShallow with derived objects/arrays: it creates new references each
  // call, causing "getSnapshot should be cached" infinite loops.
  const runs = useAppStore((s) => s.runsByConv[conversationId] ?? null)
  const dispatchesByRunId = useAppStore((s) => s.dispatchesByRunId)
  return useMemo(() => {
    const map: Record<string, ChildRunWaveInfo> = {}
    if (!runs) return map

    const dispatches: DispatchState[] = []
    const childRuns: { childRunId: string; agentId: string; startedAt: number; parentRunId: string }[] = []
    for (const run of Object.values(runs)) {
      const dispatch = dispatchesByRunId[run.id]
      if (dispatch?.plan) dispatches.push(dispatch)
      if (run.parentRunId) {
        childRuns.push({
          childRunId: run.id,
          agentId: run.agentId,
          startedAt: run.startedAt,
          parentRunId: run.parentRunId,
        })
      }
    }

    // 1. DispatchState-based wave mapping (legacy plan_tasks flow)
    for (const dispatch of dispatches) {
      const waveOf = computeWaves(dispatch.plan)
      for (const [taskId, childRunId] of Object.entries(dispatch.childRunIds)) {
        const planIndex = dispatch.plan.findIndex((p) => p.id === taskId)
        map[childRunId] = {
          wave: waveOf[taskId] ?? 0,
          taskId,
          orchestratorRunId: dispatch.runId,
          agentId: dispatch.plan[planIndex]?.agentId ?? '',
          planIndex: planIndex >= 0 ? planIndex : 0,
        }
      }
    }

    // 2. parentRunId-based wave mapping (task_dispatch flow — no DispatchState)
    //    All children of the same orchestrator run are wave 0 (parallel).
    //    Only add entries not already covered by DispatchState above.
    const parentsWithDispatch = new Set(dispatches.map((d) => d.runId))
    const groups: Record<string, { childRunId: string; agentId: string; startedAt: number }[]> = {}
    for (const cr of childRuns) {
      if (parentsWithDispatch.has(cr.parentRunId)) continue
      groups[cr.parentRunId] ??= []
      groups[cr.parentRunId].push({ childRunId: cr.childRunId, agentId: cr.agentId, startedAt: cr.startedAt })
    }
    for (const [parentRunId, children] of Object.entries(groups)) {
      children.sort((a, b) => a.startedAt - b.startedAt)
      for (let i = 0; i < children.length; i++) {
        const child = children[i]
        if (map[child.childRunId]) continue
        map[child.childRunId] = {
          wave: 0,
          taskId: `task_${i}`,
          orchestratorRunId: parentRunId,
          agentId: child.agentId,
          planIndex: i,
        }
      }
    }

    return map
  }, [runs, dispatchesByRunId])
}

export const useActiveConversation = () =>
  useAppStore((s) => (s.activeConversationId ? s.conversations[s.activeConversationId] : null))

export const useConversationList = () =>
  useAppStore(
    useShallow((s) =>
      Object.values(s.conversations).sort((a, b) => {
        // 置顶在前：相互按 pinnedAt 倒序；未置顶按 updatedAt 倒序
        if (a.pinnedAt && !b.pinnedAt) return -1
        if (!a.pinnedAt && b.pinnedAt) return 1
        if (a.pinnedAt && b.pinnedAt) return b.pinnedAt - a.pinnedAt
        return b.updatedAt - a.updatedAt
      }),
    ),
  )

export const useAgentList = () => useAppStore(useShallow((s) => Object.values(s.agents)))

export const usePendingAttachments = (conversationId: string) =>
  useAppStore(useShallow((s) => s.pendingAttachmentsByConv[conversationId] ?? []))

/** 当前会话中正在跑的顶层 run（parentRunId 为空的，用于「中止」按钮）。 */
export const useTopLevelRunningRuns = (conversationId: string) =>
  useAppStore(
    useShallow((s) => {
      const runs = s.runsByConv[conversationId]
      if (!runs) return []
      return Object.values(runs).filter((r) => r.status === 'running' && !r.parentRunId)
    }),
  )

/** 检查某个 run 是否处于 running 状态（用于 avatar 脉冲环）。 */
export function useIsRunActive(
  conversationId: string,
  runId: string | null,
): boolean {
  return useAppStore((s) => {
    if (!runId) return false
    return s.runsByConv[conversationId]?.[runId]?.status === 'running'
  })
}

/** Abnormal Custom termination Chinese label for light UI hint (null for natural complete). */
export function useRunStopHint(
  conversationId: string,
  runId: string | null,
): string | null {
  return useAppStore((s) => {
    if (!runId) return null
    const run = s.runsByConv[conversationId]?.[runId]
    if (!run) return null
    const label = run.stopReasonLabel
    if (!label) return null
    // complete / empty → no banner
    if (!run.stopReason || run.stopReason === 'complete') return null
    return label
  })
}

/** 推断当前 run 的执行阶段（用于 AgentWorkingIndicator）。 */
export function useRunPhase(
  conversationId: string,
  runId: string,
): { phase: string; toolName?: string } {
  return useAppStore(
    useShallow((s) => {
      const runs = s.runsByConv[conversationId]
      if (!runs) return { phase: '正在工作...' }
      const run = runs[runId]
      if (!run || run.status !== 'running') return { phase: '正在工作...' }

      // 找到该 run 最新的一条 agent message
      const messageIds = s.messageIdsByConv[conversationId] ?? []
      let latestMsg: MessageRow | null = null
      for (let i = messageIds.length - 1; i >= 0; i--) {
        const m = s.messages[messageIds[i]]
        if (m && m.runId === runId && m.role === 'agent') {
          latestMsg = m
          break
        }
      }
      if (!latestMsg) return { phase: '正在响应...' }

      if (latestMsg.status === 'streaming') {
        const parts = latestMsg.parts
        const lastPart = parts[parts.length - 1]
        if (lastPart) {
          if (lastPart.type === 'thinking') return { phase: '深度思考中' }
          if (lastPart.type === 'text') return { phase: '生成回答中' }
          if (lastPart.type === 'tool_use') {
            return { phase: '调用工具', toolName: lastPart.toolName }
          }
          if (lastPart.type === 'tool_result') return { phase: '准备下一轮...' }
        }
        return { phase: '正在响应...' }
      }

      return { phase: '准备下一轮...' }
    }),
  )
}

/** 获取某个 run 的 turn metrics（SDK agent ReAct 循环每轮数据）。CLI agent 无此数据。 */
export function useTurnMetrics(
  conversationId: string,
  runId: string | null,
): Record<number, TurnMetricData> | undefined {
  return useAppStore((s) => {
    if (!runId) return undefined
    return s.runsByConv[conversationId]?.[runId]?.turnMetrics
  })
}

/** 该会话是否有待审批的 Orchestrator 计划。返回 { planId, runId } 供对话式修改路由。 */
export const usePendingPlanReviewForConversation = (conversationId: string) =>
  useAppStore(
    useShallow((s): { planId: string; runId: string } | null => {
      const runs = s.runsByConv[conversationId]
      if (!runs) return null
      for (const runId in runs) {
        const d = s.dispatchesByRunId[runId]
        if (d?.reviewStatus === 'pending' && d.pendingPlanId) {
          return { planId: d.pendingPlanId, runId }
        }
      }
      return null
    }),
  )

export function selectDispatchForMessage(
  state: Pick<AppState, 'dispatchesByRunId'>,
  messageId: string,
): DispatchState | null {
  for (const id in state.dispatchesByRunId) {
    const dispatch = state.dispatchesByRunId[id]
    if (dispatch.messageId === messageId) return dispatch
  }
  return null
}

export const useDispatchForMessage = (messageId: string) =>
  useAppStore((s) => selectDispatchForMessage(s, messageId))

/** 返回该会话最后一条 user 消息的 id（用于撤回 / 编辑入口判断）。 */
export const useLatestUserMessageId = (conversationId: string): string | null =>
  useAppStore((s) => {
    const ids = s.messageIdsByConv[conversationId]
    if (!ids) return null
    for (let i = ids.length - 1; i >= 0; i--) {
      const m = s.messages[ids[i]]
      if (m && m.role === 'user') return m.id
    }
    return null
  })

/** 返回该会话最后一条 agent 消息的 id（用于「重新生成」入口判断）。 */
export const useLatestAgentMessageId = (conversationId: string): string | null =>
  useAppStore((s) => {
    const ids = s.messageIdsByConv[conversationId]
    if (!ids) return null
    for (let i = ids.length - 1; i >= 0; i--) {
      const m = s.messages[ids[i]]
      if (m && m.role === 'agent') return m.id
    }
    return null
  })

/** 该会话当前打开的文件 tab 列表。 */
export const useOpenFiles = (conversationId: string): string[] =>
  useAppStore(useShallow((s) => s.openFilesByConv[conversationId] ?? []))

/** 该会话当前激活的 tab id（'chat' 或文件路径）。 */
export const useActiveTab = (conversationId: string): string =>
  useAppStore((s) => s.activeTabByConv[conversationId] ?? 'chat')

/** 该会话当前所有待审批的 fs_write（review 模式下 agent 想改文件，等用户决定）。 */
export const usePendingWrites = (conversationId: string | null): PendingWrite[] =>
  useAppStore(useShallow((s) => (conversationId ? s.pendingWritesByConv[conversationId] ?? [] : [])))

/** 该会话当前所有待审批的关键 bash 命令。 */
export const usePendingBashCommands = (conversationId: string | null): PendingBashCommand[] =>
  useAppStore(
    useShallow((s) =>
      conversationId ? s.pendingBashCommandsByConv[conversationId] ?? [] : [],
    ),
  )

/** 该会话当前所有待回答的 ask_user（agent 通过结构化问答让用户选）。 */
export const usePendingQuestions = (conversationId: string | null): PendingQuestion[] =>
  useAppStore(
    useShallow((s) =>
      conversationId ? s.pendingQuestionsByConv[conversationId] ?? [] : [],
    ),
  )

/** 该会话当前所有待审批的 MCP 工具调用（trust='ask' 的 server 首次调用）。 */
export const usePendingMcpCalls = (conversationId: string | null): PendingMcpCall[] =>
  useAppStore(
    useShallow((s) =>
      conversationId ? s.pendingMcpCallsByConv[conversationId] ?? [] : [],
    ),
  )

/** 该会话的未读消息数。0 = 无未读。 */
export const useUnreadCount = (conversationId: string): number =>
  useAppStore((s) => s.unreadByConv[conversationId] ?? 0)

/** 单个 agent 的累计 token 用量明细。 */
export interface AgentUsageDetail {
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  totalTokens: number
  runCount: number
  /** 最近使用的 model（从 run.usage 或 agent.modelId 推断） */
  model?: string
  /** subagent runs 的 token 总量（rolled up to parent） */
  subagentTokens: number
  /** subagent runs 的数量 */
  subagentRunCount: number
}

/** 累计该会话所有 run 的 token 用量 + 上次 run 的 input prompt 长度（用于 ctx 仪表）+ per-agent 拆分。 */
export interface ConversationUsageTotal {
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  totalTokens: number
  /** 最近一次有 usage 的 run 的 input prompt token 数（context window 仪表用） */
  lastInputTokens: number
  /** 最近一次 run 的缓存命中 token 数（单次 ctx 拆解树用） */
  lastCacheReadTokens: number
  /** 最近一次 run 的 output token 数（单次拆解用） */
  lastOutputTokens: number
  /** 最近一次 run 的 ReAct 模型调用次数（顶部 “· N 轮” 标注用） */
  turnCount: number
  /** key = agentId，value = 该 agent 的累计 input+output tokens */
  byAgent: Record<string, number>
  /** key = modelId，value = 累计 input+output tokens */
  byModel: Record<string, number>
  /** key = agentId，value = 该 agent 的详细 token 拆分 */
  byAgentDetail: Record<string, AgentUsageDetail>
  /** 累计了多少个有 usage 的 run（用于显示 "N 次响应"） */
  runCount: number
}

export const useConversationUsageTotal = (conversationId: string | null): ConversationUsageTotal => {
  // 三个数据源：
  //   runs map —— streaming 时实时填，含 lastInputTokens / model / agentId（最准）
  //   messages map —— 从 DB 加载（刷新页面后唯一可用）
  //   agents map —— 取 model 兜底（messages 不存 model）
  // 用 useMemo 派生统计，避免在 store selector 里返回新对象引用导致 useShallow 死循环。
  const runs = useAppStore((s) => (conversationId ? s.runsByConv[conversationId] : undefined))
  const messageIds = useAppStore((s) =>
    conversationId ? s.messageIdsByConv[conversationId] : undefined,
  )
  const messages = useAppStore((s) => s.messages)
  const agents = useAppStore((s) => s.agents)
  const ctxOverride = useAppStore((s) =>
    conversationId ? s.ctxOverrideByConv[conversationId] : undefined,
  )
  return useMemo(() => {
    const result: ConversationUsageTotal = {
      inputTokens: 0,
      outputTokens: 0,
      cacheCreationTokens: 0,
      cacheReadTokens: 0,
      totalTokens: 0,
      lastInputTokens: 0,
      lastCacheReadTokens: 0,
      lastOutputTokens: 0,
      turnCount: 0,
      byAgent: {},
      byAgentDetail: {},
      byModel: {},
      runCount: 0,
    }
    const detail = result.byAgentDetail
    // 合并两个数据源（按 runId 去重）：
    //   Phase 1 — runs map：streaming 时实时填，含 model / agentId（最准）
    //   Phase 2 — messages map：从 DB 加载，补充没有 run.usage 的 run（如刷新页面后、
    //             或 orchestrator 的 plan/aggregate 阶段不经过 react loop 因而没有 run.usage）
    // lastInputTs：产生 lastInputTokens 的那条 run/message 的时间戳，用于和压缩覆盖值比较。
    let lastInputTs = -1

    // Phase 1: 从有 run.usage 的 run 累加，记录这些 runId 以避免 Phase 2 重复计数
    // Subagent runs (parentRunId set) roll up to the top-level parent agent.
    const runsWithUsage = new Set<string>()
    if (runs) {
      // Build run map for parent chain walking
      const runMap = new Map(Object.values(runs).map((r) => [r.id, r]))
      for (const run of Object.values(runs)) {
        const u = run.usage
        if (!u) continue
        runsWithUsage.add(run.id)

        // Walk parent chain to find top-level run
        let topRun = run
        while (topRun.parentRunId && runMap.has(topRun.parentRunId)) {
          topRun = runMap.get(topRun.parentRunId)!
        }
        const isSubagent = topRun.id !== run.id
        const targetAgentId = topRun.agentId
        const sub = u.inputTokens + u.outputTokens

        if (isSubagent) {
          // Roll up: don't count in top-level totals, attribute to parent's subagent fields
          const d = detail[targetAgentId] ??= {
            inputTokens: 0, outputTokens: 0, cacheCreationTokens: 0,
            cacheReadTokens: 0, totalTokens: 0, runCount: 0,
            subagentTokens: 0, subagentRunCount: 0,
          }
          d.subagentTokens += sub
          d.subagentRunCount++
          if (u.model) d.model = u.model
        } else {
          // Top-level run: count normally
          const runTotal = computeTotalTokens(
            u.inputTokens, u.outputTokens, u.cacheCreationTokens, u.cacheReadTokens,
          )
          result.inputTokens += u.inputTokens
          result.outputTokens += u.outputTokens
          result.cacheCreationTokens += u.cacheCreationTokens
          result.cacheReadTokens += u.cacheReadTokens
          result.totalTokens += runTotal
          result.runCount++
          result.byAgent[run.agentId] = (result.byAgent[run.agentId] ?? 0) + runTotal
          if (u.model) result.byModel[u.model] = (result.byModel[u.model] ?? 0) + runTotal
          const d = detail[run.agentId] ??= {
            inputTokens: 0, outputTokens: 0, cacheCreationTokens: 0,
            cacheReadTokens: 0, totalTokens: 0, runCount: 0,
            subagentTokens: 0, subagentRunCount: 0,
          }
          d.inputTokens += u.inputTokens
          d.outputTokens += u.outputTokens
          d.cacheCreationTokens += u.cacheCreationTokens
          d.cacheReadTokens += u.cacheReadTokens
          d.totalTokens += runTotal
          d.runCount++
          if (u.model) d.model = u.model
          if (run.startedAt > lastInputTs) {
            lastInputTs = run.startedAt
            result.lastInputTokens = u.lastInputTokens ?? u.inputTokens
            result.lastCacheReadTokens = u.lastCacheReadTokens ?? 0
            result.lastOutputTokens = u.lastOutputTokens ?? 0
            result.turnCount = u.turnCount ?? 0
          }
        }
      }
    }

    // Phase 2: 从 messages 累加，跳过已在 Phase 1 计数的 runId 和 hidden 消息
    if (messageIds) {
      const seenRunIds = new Set<string>()
      for (const mid of messageIds) {
        const m = messages[mid]
        if (!m || !m.usage || m.role !== 'agent') continue
        if (m.hidden) continue
        if (m.runId && runsWithUsage.has(m.runId)) continue

        const u = m.usage
        const provider = m.agentId ? agents[m.agentId]?.modelProvider : undefined
        const msgTotal = computeMessageTotalTokens(
          u.inputTokens, u.outputTokens, u.cacheReadTokens, provider,
        )
        result.inputTokens += u.inputTokens
        result.outputTokens += u.outputTokens
        result.cacheReadTokens += u.cacheReadTokens
        result.totalTokens += msgTotal
        if (m.runId && !seenRunIds.has(m.runId)) {
          seenRunIds.add(m.runId)
          result.runCount++
        }
        if (m.agentId) {
          result.byAgent[m.agentId] = (result.byAgent[m.agentId] ?? 0) + msgTotal
          const modelId = agents[m.agentId]?.modelId
          if (modelId) result.byModel[modelId] = (result.byModel[modelId] ?? 0) + msgTotal
          const d = detail[m.agentId] ??= {
            inputTokens: 0, outputTokens: 0, cacheCreationTokens: 0,
            cacheReadTokens: 0, totalTokens: 0, runCount: 0,
            subagentTokens: 0, subagentRunCount: 0,
          }
          d.inputTokens += u.inputTokens
          d.outputTokens += u.outputTokens
          d.cacheReadTokens += u.cacheReadTokens
          d.totalTokens += msgTotal
          if (m.runId && !seenRunIds.has(m.runId)) d.runCount++
          if (modelId) d.model = modelId
        }
        if (m.createdAt > lastInputTs) {
          lastInputTs = m.createdAt
          result.lastInputTokens = u.inputTokens
          // MessageUsage 无 turnCount / lastOutputTokens，仅能取 cacheRead 快照
          result.lastCacheReadTokens = u.cacheReadTokens
          result.lastOutputTokens = u.outputTokens
          result.turnCount = 0
        }
      }
    }

// totalTokens 和 d.totalTokens 已在循环内按 provider 语义逐条累加，此处不再重算

    // 压缩后的乐观覆盖：仅当覆盖值比最新实测的 run/message 更新时接管「当前 ctx」。
    if (ctxOverride && ctxOverride.at > lastInputTs) {
      result.lastInputTokens = ctxOverride.tokens
    }
    return result
  }, [runs, messageIds, messages, agents, ctxOverride])
}
