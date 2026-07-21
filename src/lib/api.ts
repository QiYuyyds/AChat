import type {
  AgentRow,
  AppSettingsRow,
  ArtifactRow,
  AttachmentRow,
  ConversationWithMeta,
  ContextSummaryRow,
  MessageRow,
} from '@/db/schema'
import type {
  AskUserAnswer,
  CreateDocumentRequest,
  DeployCandidateRecord,
  DeployStatusRecord,
  DocumentRow,
  IngestResult,
  PendingBashCommand,
  PendingDispatchPlan,
  PendingMcpCall,
  PendingQuestion,
  PendingWrite,
  UploadResult,
  VersionRow,
  WriteDocumentResponse,
} from '@/shared/types'
import type { AgentConfigDraft, AgentDraftRequest } from '@/shared/agent-builder-config'

import { API_BASE_URL } from '@/lib/config'

export interface ArtifactListItem {
  id: string
  conversationId: string
  conversationTitle: string | null
  type: string
  title: string
  version: number
  parentArtifactId: string | null
  createdByAgentId: string
  createdAt: number
}

// ─── authFetch: credentials + auto-refresh on 401 ───────────────────────────

let _refreshInProgress: Promise<boolean> | null = null

function _getStoredToken(): string | null {
  try {
    return localStorage.getItem('agenthub_access_token')
  } catch {
    return null
  }
}

async function _doRefresh(): Promise<boolean> {
  if (_refreshInProgress) return _refreshInProgress
  _refreshInProgress = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (res.ok) {
        const data = await res.json()
        const token = data.tokens?.access_token
        if (token) {
          try {
            localStorage.setItem('agenthub_access_token', token)
          } catch {
            // best-effort
          }
        }
        return true
      }
      return false
    } catch {
      return false
    } finally {
      _refreshInProgress = null
    }
  })()
  return _refreshInProgress
}

export async function authFetch(
  input: string,
  init?: RequestInit,
): Promise<Response> {
  const token = _getStoredToken()
  const merged: RequestInit = {
    ...init,
    credentials: 'include',
    headers: {
      ...(init?.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  }

  let res = await fetch(input, merged)

  if (res.status === 401) {
    const refreshed = await _doRefresh()
    if (refreshed) {
      const newToken = _getStoredToken()
      const retryInit: RequestInit = {
        ...merged,
        headers: {
          ...merged.headers,
          ...(newToken ? { Authorization: `Bearer ${newToken}` } : {}),
        },
      }
      res = await fetch(input, retryInit)
    }
  }

  return res
}

async function json<T>(req: Promise<Response>): Promise<T> {
  const res = await req
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<T>
}

async function authJson<T>(input: string, init?: RequestInit): Promise<T> {
  return json<T>(Promise.resolve(authFetch(input, init)))
}

// ─── Agents ─────────────────────────────────────
export async function fetchAgents(): Promise<AgentRow[]> {
  const { agents } = await json<{ agents: AgentRow[] }>(authFetch(API_BASE_URL + '/api/agents'))
  return agents
}

export interface CreateAgentBody {
  name: string
  avatar: string
  description: string
  capabilities: string[]
  systemPrompt: string
  /** 默认 'custom'。SDK adapter 使用各自内置工具集 */
  adapterName?: 'custom' | 'claude-code' | 'codex'
  /** custom: required；SDK adapter: 忽略 */
  modelProvider?: 'anthropic' | 'openai' | 'deepseek' | 'volcano-ark' | 'openai-compatible'
  /** custom: required；SDK adapter: 可选，默认 SDK 默认模型 */
  modelId?: string
  toolNames: string[]
  /** custom: 启用的 skill slug 列表；SDK adapter: 必须为空 */
  skillNames: string[]
  /** custom: 启用的 MCP server ID 列表；SDK adapter: 必须为空 */
  mcpServerIds?: string[]
  supportsVision?: boolean
  apiKey?: string
  /** 自定义 API base URL。Claude/Codex 对 endpoint 协议兼容性要求不同；空走默认 */
  apiBaseUrl?: string
  /** 设为协调者（Orchestrator） */
  isOrchestrator?: boolean
  // ── CLI agent fields ──────────────────────────────────────
  /** CLI 二进制路径；空则 PATH 查找。仅 claude-code / codex 有效。 */
  executablePath?: string | null
  /** 协议族。仅 claude-code / codex 有效。 */
  protocolFamily?: string | null
  /** CLI 自定义参数。仅 claude-code / codex 有效。 */
  customArgs?: string[] | null
}

export async function createAgent(body: CreateAgentBody): Promise<AgentRow> {
  const { agent } = await json<{ agent: AgentRow }>(
    authFetch(API_BASE_URL + '/api/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return agent
}

export async function createAgentDraft(body: AgentDraftRequest): Promise<AgentConfigDraft> {
  const { draft } = await json<{ draft: AgentConfigDraft }>(
    authFetch(API_BASE_URL + '/api/agents/draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return draft
}

export type UpdateAgentBody = Partial<
  Omit<CreateAgentBody, 'avatar' | 'apiKey' | 'apiBaseUrl' | 'modelId'>
> & {
  // SDK adapter 可用 null 清空，表示走 SDK 默认模型；custom 仍必须有非空 modelId
  modelId?: string | null
  // 显式 null 表示清除自定义 key；undefined 表示不改
  apiKey?: string | null
  // 同上
  apiBaseUrl?: string | null
  /** 设为协调者（Orchestrator） */
  isOrchestrator?: boolean
}

export async function updateAgent(agentId: string, patch: UpdateAgentBody): Promise<AgentRow> {
  const { agent } = await json<{ agent: AgentRow }>(
    authFetch(`${API_BASE_URL}/api/agents/${agentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  )
  return agent
}

export async function deleteAgent(agentId: string): Promise<void> {
  await json<{ ok: true }>(authFetch(`${API_BASE_URL}/api/agents/${agentId}`, { method: 'DELETE' }))
}

// ─── Conversations ──────────────────────────────
export async function fetchConversations(): Promise<ConversationWithMeta[]> {
  const { conversations } = await json<{ conversations: ConversationWithMeta[] }>(
    authFetch(API_BASE_URL + '/api/conversations'),
  )
  return conversations
}

export interface CreateConversationBody {
  title?: string
  mode: 'single' | 'group' | 'guide'
  agentIds: string[]
  boundPath?: string
}

export async function createConversation(body: CreateConversationBody): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(API_BASE_URL + '/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return conversation
}

export interface CodeIntelligenceCounts {
  files: number
  symbols: number
  relationships: number
}

export interface CodeIntelligenceStatusRecord {
  enabled: boolean
  runtimeVersion: string | null
  status:
    | 'disabled'
    | 'preparing_runtime'
    | 'queued'
    | 'indexing'
    | 'ready'
    | 'syncing'
    | 'rebuilding'
    | 'cancelling'
    | 'failed'
    | 'interrupted'
  phase: string | null
  progressPercent: number | null
  counts: CodeIntelligenceCounts
  createdAt: number | null
  updatedAt: number | null
  startedAt: number | null
  completedAt: number | null
  lastSyncAt: number | null
  error: string | null
  projectPath: string
}

export async function fetchCodeIntelligenceStatus(
  conversationId: string,
): Promise<CodeIntelligenceStatusRecord> {
  const response = await json<{ status: CodeIntelligenceStatusRecord }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/code-intelligence`),
  )
  return response.status
}
export type CodeIntelligenceAction =
  | 'enable'
  | 'disable'
  | 'cancel'
  | 'sync'
  | 'rebuild'
  | 'retry'

export async function runCodeIntelligenceAction(
  conversationId: string,
  action: CodeIntelligenceAction,
): Promise<void> {
  await json<Record<string, unknown>>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/code-intelligence/${action}`, {
      method: 'POST',
    }),
  )
}
export async function deleteConversation(conversationId: string): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, { method: 'DELETE' }),
  )
}

export async function addAgentsToConversation(
  conversationId: string,
  addAgentIds: string[],
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ addAgentIds }),
    }),
  )
  return conversation
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  )
  return conversation
}

export async function updateConversationSummary(
  conversationId: string,
  summary: string | null,
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ summary }),
    }),
  )
  return conversation
}

export async function togglePinConversation(conversationId: string): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ togglePin: true }),
    }),
  )
  return conversation
}

export async function toggleArchiveConversation(
  conversationId: string,
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ toggleArchive: true }),
    }),
  )
  return conversation
}

export async function setFsWriteApprovalMode(
  conversationId: string,
  mode: 'auto' | 'review',
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fsWriteApprovalMode: mode }),
    }),
  )
  return conversation
}

// Task 5.3: Set conversation RAG mode
export async function setRagMode(
  conversationId: string,
  enabled: boolean,
): Promise<ConversationWithMeta> {
  const { conversation } = await json<{ conversation: ConversationWithMeta }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/rag-mode`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ragEnabled: enabled }),
    }),
  )
  return conversation
}

// ─── Pending writes (fs_write review mode) ─────
export async function fetchPendingWrites(conversationId: string): Promise<PendingWrite[]> {
  const { pendingWrites } = await json<{ pendingWrites: PendingWrite[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-writes`),
  )
  return pendingWrites
}

export async function approvePendingWrite(
  conversationId: string,
  pendingId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-writes/${pendingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve' }),
    }),
  )
}

export async function rejectPendingWrite(
  conversationId: string,
  pendingId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-writes/${pendingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reject' }),
    }),
  )
}

// ─── Pending bash commands ─────
export async function fetchPendingBashCommands(
  conversationId: string,
): Promise<PendingBashCommand[]> {
  const { pendingCommands } = await json<{ pendingCommands: PendingBashCommand[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-bash-commands`),
  )
  return pendingCommands
}

export async function approvePendingBashCommand(
  conversationId: string,
  pendingId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-bash-commands/${pendingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve' }),
    }),
  )
}

export async function rejectPendingBashCommand(
  conversationId: string,
  pendingId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-bash-commands/${pendingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reject' }),
    }),
  )
}

// ─── Pending questions (ask_user) ───────────────
export async function fetchPendingQuestions(conversationId: string): Promise<PendingQuestion[]> {
  const { pendingQuestions } = await json<{ pendingQuestions: PendingQuestion[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-questions`),
  )
  return pendingQuestions
}

export async function submitQuestionAnswers(
  conversationId: string,
  questionId: string,
  answers: Record<string, AskUserAnswer>,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-questions/${questionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
    }),
  )
}

// ─── Messages ───────────────────────────────────
// ─── Pending dispatch plans (Orchestrator plan review) ───
export async function fetchPendingDispatchPlans(
  conversationId: string,
): Promise<PendingDispatchPlan[]> {
  const { pendingDispatchPlans } = await json<{ pendingDispatchPlans: PendingDispatchPlan[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-dispatch-plans`),
  )
  return pendingDispatchPlans
}

export async function approvePendingDispatchPlan(
  conversationId: string,
  planId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-dispatch-plans/${planId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve' }),
    }),
  )
}

export async function reviseDispatchPlan(
  conversationId: string,
  planId: string,
  feedback: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-dispatch-plans/${planId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'revise', feedback }),
    }),
  )
}

export async function rejectPendingDispatchPlan(
  conversationId: string,
  planId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-dispatch-plans/${planId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reject' }),
    }),
  )
}

export async function fetchMessages(conversationId: string): Promise<MessageRow[]> {
  const { messages } = await json<{ messages: MessageRow[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`),
  )
  return messages
}

// ─── Search ──────────────────────────────────────
export interface SearchApiResult {
  hits: Array<{
    messageId: string
    conversationId: string
    conversationTitle: string
    role: 'user' | 'agent' | 'system'
    agentId: string | null
    agentName: string | null
    agentAvatar: string | null
    createdAt: number
    snippetHtml: string
  }>
  total: number
  tookMs: number
}

export async function searchMessagesApi(
  query: string,
  opts: { fallback?: 'like'; conversationId?: string; role?: 'user' | 'agent' } = {},
): Promise<SearchApiResult> {
  const params = new URLSearchParams({ q: query })
  if (opts.fallback) params.set('fallback', opts.fallback)
  if (opts.conversationId) params.set('conversationId', opts.conversationId)
  if (opts.role) params.set('role', opts.role)
  const { data } = await json<{ ok: true; data: SearchApiResult }>(
    authFetch(`${API_BASE_URL}/api/search?${params}`),
  )
  return data
}

export interface ClearConversationHistoryResult {
  conversation: ConversationWithMeta
  deletedMessageCount: number
  deletedRunCount: number
  deletedSummaryCount: number
}

export async function clearConversationHistory(
  conversationId: string,
): Promise<ClearConversationHistoryResult> {
  return json<ClearConversationHistoryResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, { method: 'DELETE' }),
  )
}

export interface SendMessageBody {
  content: string
  mentionedAgentIds?: string[]
  parentMessageId?: string
  attachmentIds?: string[]
}

export interface SendMessageResult {
  messageId: string
  runIds: string[]
  messages?: MessageRow[]
  deploy?: DeployConversationResult
}

export async function sendMessage(
  conversationId: string,
  body: SendMessageBody,
): Promise<SendMessageResult> {
  return json<SendMessageResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export type DeployConversationResult =
  | {
      kind: 'no_candidates'
      candidates: []
      message: MessageRow
    }
  | {
      kind: 'candidate_selection'
      candidates: DeployCandidateRecord[]
      message: MessageRow
    }
  | {
      kind: 'deployed'
      deployment: DeployStatusRecord
      message: MessageRow
    }

export async function fetchDeployCandidates(
  conversationId: string,
): Promise<DeployCandidateRecord[]> {
  const { candidates } = await json<{ candidates: DeployCandidateRecord[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/deploy`),
  )
  return candidates
}

export async function deployConversationArtifact(
  conversationId: string,
  artifactId?: string,
): Promise<DeployConversationResult> {
  return json<DeployConversationResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/deploy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(artifactId ? { artifactId } : {}),
    }),
  )
}

// ─── Runs ───────────────────────────────────────
export interface CompactConversationResult {
  /** 良性跳过（无事可压）时为 true：仅带 message 提示，不产生摘要 */
  skipped?: boolean
  /** 跳过原因码（skipped 时存在，用于日志/兜底） */
  reason?: string
  /** 跳过时无 summary */
  summary?: ContextSummaryRow
  message: MessageRow
  /** 压缩前预估的下次 prompt token 数（跳过时缺省） */
  ctxBefore?: number
  /** 压缩后预估的下次 prompt token 数（UsageBadge 用来乐观刷新「当前 ctx」；跳过时缺省） */
  ctxAfter?: number
}

export async function compactConversation(
  conversationId: string,
): Promise<CompactConversationResult> {
  return json<CompactConversationResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/compact`, { method: 'POST' }),
  )
}

export async function abortRun(runId: string): Promise<void> {
  await json<{ ok: true }>(authFetch(`${API_BASE_URL}/api/runs/${runId}/abort`, { method: 'POST' }))
}

// ─── Messages: withdraw / edit ──────────────────
export interface WithdrawResult {
  deletedMessageIds: string[]
  deletedArtifactIds: string[]
}

export async function withdrawMessage(
  messageId: string,
  conversationId: string,
): Promise<WithdrawResult> {
  return json<WithdrawResult>(
    authFetch(`${API_BASE_URL}/api/messages/${messageId}/withdraw`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId }),
    }),
  )
}

export interface EditAndResendResult extends WithdrawResult {
  newMessage: MessageRow
  runIds: string[]
}

export interface RegenerateResult extends WithdrawResult {
  triggerMessageId: string
  runIds: string[]
}

export async function regenerateLastResponse(conversationId: string): Promise<RegenerateResult> {
  return json<RegenerateResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId }),
    }),
  )
}

export async function editAndResendMessage(
  messageId: string,
  conversationId: string,
  content: string,
): Promise<EditAndResendResult> {
  return json<EditAndResendResult>(
    authFetch(`${API_BASE_URL}/api/messages/${messageId}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId, content }),
    }),
  )
}

export interface ToggleBookmarkResult {
  bookmarkedMessageIds: string[]
  bookmarked: boolean
}

export async function toggleMessageBookmark(
  messageId: string,
  conversationId: string,
): Promise<ToggleBookmarkResult> {
  return json<ToggleBookmarkResult>(
    authFetch(`${API_BASE_URL}/api/messages/${messageId}/bookmark`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId }),
    }),
  )
}

export interface TogglePinResult {
  pinnedMessageIds: string[]
  pinned: boolean
}

export async function toggleMessagePin(
  messageId: string,
  conversationId: string,
): Promise<TogglePinResult> {
  return json<TogglePinResult>(
    authFetch(`${API_BASE_URL}/api/messages/${messageId}/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId }),
    }),
  )
}

// ─── Filesystem (DirPicker) ────────────────────
export interface ListDirResult {
  path: string
  parent: string | null
  entries: Array<{ name: string; isDirectory: boolean; path?: string }>
}

export type ServerPlatform = 'posix' | 'windows'

export async function getServerPlatform(): Promise<ServerPlatform> {
  const res = await json<{ platform: ServerPlatform }>(authFetch(API_BASE_URL + '/api/platform'))
  return res.platform
}

export async function listDirectory(targetPath?: string): Promise<ListDirResult> {
  const qs = targetPath ? `?path=${encodeURIComponent(targetPath)}` : ''
  return json<ListDirResult>(authFetch(`${API_BASE_URL}/api/fs/listdir${qs}`))
}

// ─── Filesystem (conversation-scoped, 文件浏览器面板用) ────────
export interface WorkspaceListResult {
  relPath: string
  absolutePath: string
  parent: string | null
  entries: Array<{ name: string; isDirectory: boolean; size?: number }>
}

export async function workspaceListDir(
  conversationId: string,
  relPath = '',
): Promise<WorkspaceListResult> {
  const qs = relPath ? `?path=${encodeURIComponent(relPath)}` : ''
  return json<WorkspaceListResult>(authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/fs/listdir${qs}`))
}

export interface WorkspaceReadResult {
  path: string
  absolutePath: string
  cwd: string
  size: number
  content: string
  truncated: boolean
}

export async function workspaceReadFile(
  conversationId: string,
  relPath: string,
): Promise<WorkspaceReadResult> {
  return json<WorkspaceReadResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/fs/read?path=${encodeURIComponent(relPath)}`),
  )
}

export interface WorkspaceWriteResult {
  path: string
  absolutePath: string
  cwd: string
  bytes: number
}

export async function workspaceWriteFile(
  conversationId: string,
  relPath: string,
  content: string,
): Promise<WorkspaceWriteResult> {
  return json<WorkspaceWriteResult>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/fs/write`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: relPath, content }),
    }),
  )
}

// ─── Artifacts ─────────────────────────────────
export async function fetchArtifacts(): Promise<ArtifactListItem[]> {
  const { artifacts } = await json<{ artifacts: ArtifactListItem[] }>(authFetch(API_BASE_URL + '/api/artifacts'))
  return artifacts
}

export async function fetchArtifact(artifactId: string): Promise<ArtifactRow> {
  const { artifact } = await json<{ artifact: ArtifactRow }>(
    authFetch(`${API_BASE_URL}/api/artifacts/${artifactId}`),
  )
  return artifact
}

export async function fetchArtifactVersions(artifactId: string): Promise<ArtifactRow[]> {
  const { versions } = await json<{ versions: ArtifactRow[] }>(
    authFetch(`${API_BASE_URL}/api/artifacts/${artifactId}/versions`),
  )
  return versions
}

/** 以 artifactId 为父，提交编辑后的内容为新版本（version+1）；返回新产物行。 */
export async function createArtifactVersion(
  artifactId: string,
  body: { content: unknown; title?: string },
): Promise<ArtifactRow> {
  const { artifact } = await json<{ artifact: ArtifactRow }>(
    authFetch(`${API_BASE_URL}/api/artifacts/${artifactId}/versions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return artifact
}

export async function deleteArtifact(artifactId: string): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/artifacts/${artifactId}`, { method: 'DELETE' }),
  )
}

// ─── Attachments ───────────────────────────────
export async function fetchAttachments(conversationId: string): Promise<AttachmentRow[]> {
  const { attachments } = await json<{ attachments: AttachmentRow[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/attachments`),
  )
  return attachments
}

export async function uploadAttachment(
  conversationId: string,
  file: File,
): Promise<AttachmentRow> {
  const form = new FormData()
  form.append('file', file)
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/attachments`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  const { attachment } = (await res.json()) as { attachment: AttachmentRow }
  return attachment
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  await json<{ ok: true }>(authFetch(`${API_BASE_URL}/api/attachments/${attachmentId}`, { method: 'DELETE' }))
}

export function attachmentDownloadUrl(attachmentId: string): string {
  return `/api/attachments/${attachmentId}`
}

// ─── Usage / Analytics ─────────────────────────────
export interface UsageBucket {
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
  totalTokens: number
  runs: number
}

export interface UsageSummary {
  today: UsageBucket
  week: UsageBucket
  allTime: UsageBucket
  topConversations: Array<{
    id: string
    title: string
    totalTokens: number
    runs: number
    updatedAt: number
  }>
  byAgent: Array<{ agentId: string; name: string; totalTokens: number; runs: number }>
  byModel: Array<{ model: string; totalTokens: number; runs: number }>
}

export async function fetchUsageSummary(): Promise<UsageSummary> {
  return json<UsageSummary>(authFetch(API_BASE_URL + '/api/usage/summary'))
}

export interface UsageTimeseriesPoint extends UsageBucket {
  date: string
}

export async function fetchUsageTimeseries(days: number): Promise<UsageTimeseriesPoint[]> {
  return json<UsageTimeseriesPoint[]>(authFetch(`${API_BASE_URL}/api/usage/timeseries?days=${days}`))
}

// ─── Mobile companion connection hints ─────────────
export interface ConnectionHint {
  kind: 'tailscale' | 'lan' | 'local'
  label: string
  host: string
  url: string
  interfaceName?: string
}

export async function fetchConnectionHints(): Promise<ConnectionHint[]> {
  const { hints } = await json<{ hints: ConnectionHint[] }>(authFetch(API_BASE_URL + '/api/connection-hints'))
  return hints
}

// ─── App Settings (全局 API key) ───────────────
export async function fetchAppSettings(): Promise<AppSettingsRow> {
  const { settings } = await json<{ settings: AppSettingsRow }>(authFetch(API_BASE_URL + '/api/settings'))
  return settings
}

export interface AppSettingsPatchBody {
  anthropicApiKey?: string | null
  anthropicBaseUrl?: string | null
  openaiApiKey?: string | null
  deepseekApiKey?: string | null
  arkApiKey?: string | null
  companionMode?: 'off' | 'lan' | 'tailnet'
  mobileDeviceToken?: string | null
  deploymentPublishEnabled?: boolean
  deploymentPublishDir?: string | null
  deploymentPublicBaseUrl?: string | null
  obsidianVaultPath?: string | null
}

export async function updateAppSettings(patch: AppSettingsPatchBody): Promise<AppSettingsRow> {
  const { settings } = await json<{ settings: AppSettingsRow }>(
    authFetch(API_BASE_URL + '/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  )
  return settings
}

export async function regenerateMobileDeviceToken(): Promise<AppSettingsRow> {
  const { settings } = await json<{ settings: AppSettingsRow }>(
    authFetch(API_BASE_URL + '/api/settings/mobile-token', { method: 'POST' }),
  )
  return settings
}

// ─── Documents (知识库) ──────────────────────────
export async function fetchDocuments(): Promise<DocumentRow[]> {
  const { documents } = await json<{ documents: DocumentRow[] }>(
    authFetch(API_BASE_URL + '/api/documents'),
  )
  return documents
}

export async function createDocument(body: CreateDocumentRequest): Promise<WriteDocumentResponse> {
  return json<WriteDocumentResponse>(
    authFetch(API_BASE_URL + '/api/documents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

// Import a `document`-type artifact into the knowledge base. Idempotent by artifactId.
export async function ingestArtifactToKnowledgeBase(
  artifact: ArtifactRow,
): Promise<WriteDocumentResponse> {
  const content = artifact.content
  if (content.type !== 'document' || content.format !== 'markdown' || !content.content.trim()) {
    throw new Error('只有 markdown 格式的 document 类型产物才能加入知识库')
  }
  return createDocument({
    title: artifact.title,
    docType: 'document',
    source: 'artifact_import',
    contentMd: content.content,
    ingestToRag: true,
    metadata: { artifactId: artifact.id, conversationId: artifact.conversationId },
  })
}

export async function getDocument(documentId: string): Promise<{ document: DocumentRow; version: VersionRow }> {
  return json<{ document: DocumentRow; version: VersionRow }>(
    authFetch(`${API_BASE_URL}/api/documents/${documentId}`),
  )
}

export async function listVersions(documentId: string): Promise<VersionRow[]> {
  const { versions } = await json<{ versions: VersionRow[] }>(
    authFetch(`${API_BASE_URL}/api/documents/${documentId}/versions`),
  )
  return versions
}

export async function deleteDocument(documentId: string): Promise<{ ok: boolean; deletedChunks: number }> {
  return json<{ ok: boolean; deletedChunks: number }>(
    authFetch(`${API_BASE_URL}/api/documents/${documentId}`, { method: 'DELETE' }),
  )
}

export async function ingestDocument(documentId: string, versionId: string): Promise<IngestResult> {
  return json<IngestResult>(
    authFetch(`${API_BASE_URL}/api/documents/${documentId}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ versionId }),
    }),
  )
}

export async function uploadDocument(
  file: File,
  opts?: { documentId?: string; title?: string; docType?: string },
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  if (opts?.documentId) form.append('document_id', opts.documentId)
  if (opts?.title) form.append('title', opts.title)
  if (opts?.docType) form.append('doc_type', opts.docType)
  const res = await authFetch(`${API_BASE_URL}/api/documents/upload`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<UploadResult>
}

// ─── Obsidian Sync ──────────────────────────────────────────
export async function fetchDocumentTree(path?: string): Promise<import('@/shared/types').DocumentTree> {
  const query = path ? `?path=${encodeURIComponent(path)}` : ''
  return json<import('@/shared/types').DocumentTree>(
    authFetch(API_BASE_URL + '/api/documents/tree' + query),
  )
}

export async function fetchDocumentFlat(sources?: string[]): Promise<DocumentRow[]> {
  const query = sources?.length ? `?sources=${encodeURIComponent(sources.join(','))}` : ''
  const { documents } = await json<{ documents: DocumentRow[] }>(
    authFetch(API_BASE_URL + '/api/documents/flat' + query),
  )
  return documents
}

export async function syncObsidian(): Promise<import('@/shared/types').SyncReport> {
  return json<import('@/shared/types').SyncReport>(
    authFetch(API_BASE_URL + '/api/obsidian/sync', { method: 'POST' }),
  )
}

export async function getObsidianStatus(): Promise<import('@/shared/types').SyncStatus> {
  return json<import('@/shared/types').SyncStatus>(
    authFetch(API_BASE_URL + '/api/obsidian/status'),
  )
}

// ─── Skills (技能；文件系统支撑，custom adapter 用) ──────────
export interface SkillSummary {
  slug: string
  name: string
  description: string
  triggerKeywords?: string[]
}

export async function listSkills(): Promise<SkillSummary[]> {
  const { skills } = await json<{ skills: SkillSummary[] }>(authFetch(API_BASE_URL + '/api/skills'))
  return skills
}

/**
 * 上传一个 skill。folder 上传时为每个文件传 webkitRelativePath，
 * single-file 时传文件名。files 与 paths 一一对应。
 */
export async function uploadSkill(files: File[], paths: string[]): Promise<SkillSummary> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  for (const path of paths) form.append('paths', path)
  const res = await authFetch(`${API_BASE_URL}/api/skills/upload`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  const { skill } = (await res.json()) as { skill: SkillSummary }
  return skill
}

export async function deleteSkill(slug: string): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/skills/${slug}`, { method: 'DELETE' }),
  )
}

// ─── MCP Servers (外部 MCP server 管理) ──────────
export interface McpServerResponse {
  id: string
  name: string
  transport: 'stdio' | 'sse' | 'streamable_http'
  command: string | null
  args: string[]
  env: Record<string, string> | null
  url: string | null
  headers: Record<string, string> | null
  trust: 'always' | 'ask'
  enabled: boolean
  createdAt: number
}

export interface McpServerCreateBody {
  name: string
  transport: 'stdio' | 'sse' | 'streamable_http'
  command?: string | null
  args?: string[]
  env?: Record<string, string> | null
  url?: string | null
  headers?: Record<string, string> | null
  trust?: 'always' | 'ask'
  enabled?: boolean
}

export type McpServerUpdateBody = Partial<McpServerCreateBody>

export interface McpTestResult {
  ok: boolean
  tools: Array<{ name: string; description: string }>
  error?: string
}

export async function fetchMcpServers(): Promise<McpServerResponse[]> {
  const { servers } = await json<{ servers: McpServerResponse[] }>(
    authFetch(API_BASE_URL + '/api/mcp/servers'),
  )
  return servers
}

export async function createMcpServer(body: McpServerCreateBody): Promise<McpServerResponse> {
  const { server } = await json<{ server: McpServerResponse }>(
    authFetch(API_BASE_URL + '/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return server
}

export async function updateMcpServer(serverId: string, body: McpServerUpdateBody): Promise<McpServerResponse> {
  const { server } = await json<{ server: McpServerResponse }>(
    authFetch(`${API_BASE_URL}/api/mcp/servers/${serverId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
  return server
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/mcp/servers/${serverId}`, { method: 'DELETE' }),
  )
}

export async function testMcpServer(serverId: string): Promise<McpTestResult> {
  return json<McpTestResult>(
    authFetch(`${API_BASE_URL}/api/mcp/servers/${serverId}/test`, { method: 'POST' }),
  )
}

// ─── Pending MCP calls (ask-trust 审批) ──────────
export async function fetchPendingMcpCalls(conversationId: string): Promise<PendingMcpCall[]> {
  const { pendingMcpCalls } = await json<{ pendingMcpCalls: PendingMcpCall[] }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-mcp-calls`),
  )
  return pendingMcpCalls
}

export async function approvePendingMcpCall(
  conversationId: string,
  callId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-mcp-calls/${callId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve' }),
    }),
  )
}

export async function rejectPendingMcpCall(
  conversationId: string,
  callId: string,
): Promise<void> {
  await json<{ ok: true }>(
    authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/pending-mcp-calls/${callId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reject' }),
    }),
  )
}

// ─── Profile ──────────────────────────────────────

export interface UserProfile {
  name: string | null
  location: string | null
  hometown: string | null
  preferences: string | null
  bio: string | null
  avatarUrl: string | null
}

export interface ProfileUpdateBody {
  name?: string | null
  location?: string | null
  hometown?: string | null
  preferences?: string | null
  bio?: string | null
}

export async function fetchProfile(): Promise<UserProfile> {
  return authJson<UserProfile>(API_BASE_URL + '/api/profile')
}

export async function updateProfile(body: ProfileUpdateBody): Promise<UserProfile> {
  return authJson<UserProfile>(API_BASE_URL + '/api/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function uploadAvatar(file: File): Promise<{ avatarUrl: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await authFetch(API_BASE_URL + '/api/profile/avatar', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

// ─── Workspace Env ─────────────────────────────────────

export interface WorkspaceEnvStatus {
  workspaceMode: 'sandbox' | 'local'
  language: 'python' | 'nodejs' | 'java' | 'go' | 'unknown'
  venvPresent: boolean
  envPreference: 'venv_created' | 'skip' | 'system_python' | null
}

/** POST /api/workspaces/:id/create-venv — triggers async venv creation (202). */
export async function createProjectVenv(conversationId: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/workspaces/${conversationId}/create-venv`,
    { method: 'POST' },
  )
  if (!res.ok && res.status !== 202) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`)
  }
}

/** PATCH /api/workspaces/:id/env-preference — persist user's env choice. */
export async function updateEnvPreference(
  conversationId: string,
  preference: 'venv_created' | 'skip' | 'system_python',
): Promise<void> {
  await authJson(
    `${API_BASE_URL}/api/workspaces/${conversationId}/env-preference`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preference }),
    },
  )
}

/** GET /api/workspaces/:id/env-status — current detection + preference. */
export async function fetchWorkspaceEnvStatus(
  conversationId: string,
): Promise<WorkspaceEnvStatus> {
  return authJson<WorkspaceEnvStatus>(
    `${API_BASE_URL}/api/workspaces/${conversationId}/env-status`,
  )
}
