import { isValidElement, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import {
  ArrowDown,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  Clock3,
  Code2,
  FileText,
  Image as ImageIcon,
  Layers,
  Paperclip,
  Pencil,
  Rocket,
  RotateCcw,
  Send,
  Trash2,
  Wrench,
  X,
  XCircle,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { AvatarBadge, avatarInitials } from '../lib/avatar'
import { formatTime } from '../lib/format'
import { ConversationRow } from './ConversationRow'
import type {
  MobileAgent,
  MobileArtifactSummary,
  MobileConversationDetail,
  MobileMessage,
  MobileMessagePart,
  MobileSnapshot,
} from '../types'

export function ConversationsScreen({
  connected,
  loading,
  snapshot,
  detail,
  selectedConversationId,
  onOpenConversation,
  onOpenArtifact,
  onSendMessage,
  onWithdrawMessage,
  onEditMessage,
  onRegenerate,
  busy,
}: {
  connected: boolean
  loading: boolean
  snapshot: MobileSnapshot | null
  detail: MobileConversationDetail | null
  selectedConversationId: string | null
  onOpenConversation: (id: string) => void
  onOpenArtifact: (id: string) => void
  onSendMessage: (content: string) => void
  onWithdrawMessage: (messageId: string) => void
  onEditMessage: (messageId: string, content: string) => void
  onRegenerate: () => void
  busy: boolean
}) {
  const [draft, setDraft] = useState('')
  const [showScrollButton, setShowScrollButton] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const lastAutoScrolledConversationId = useRef<string | null>(null)
  const agentById = new Map((snapshot?.agents ?? []).map((agent) => [agent.id, agent]))
  const artifactById = new Map((detail?.artifacts ?? []).map((artifact) => [artifact.id, artifact]))
  const detailConversationId = detail?.conversation.id ?? null
  const scrollSignature = useMemo(() => buildConversationScrollSignature(detail), [detail])

  // 最新可操作消息：撤回/编辑只对最新 user 消息、重新生成只对最新非流式 agent 消息（后端只允许操作最新）
  const msgs = detail?.messages ?? []
  let latestUserMessageId: string | null = null
  let latestAgentMessageId: string | null = null
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]
    if (!latestUserMessageId && m.role === 'user') latestUserMessageId = m.id
    if (!latestAgentMessageId && m.role === 'agent' && m.status !== 'streaming') latestAgentMessageId = m.id
    if (latestUserMessageId && latestAgentMessageId) break
  }

  useEffect(() => {
    if (!selectedConversationId || !detailConversationId || selectedConversationId !== detailConversationId) return

    const isNewConversation = lastAutoScrolledConversationId.current !== detailConversationId
    lastAutoScrolledConversationId.current = detailConversationId
    if (!isNewConversation && !isNearWindowBottom()) return

    const frame = window.requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ block: 'end', behavior: 'auto' })
    })

    return () => window.cancelAnimationFrame(frame)
  }, [detailConversationId, scrollSignature, selectedConversationId])

  useEffect(() => {
    if (!selectedConversationId) return

    function handleScroll() {
      setShowScrollButton(!isNearWindowBottom(160))
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    handleScroll()
    return () => window.removeEventListener('scroll', handleScroll)
  }, [selectedConversationId, scrollSignature])

  function submitDraft() {
    const content = draft.trim()
    if (!content) return
    onSendMessage(content)
    setDraft('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  function handleDraftChange(value: string) {
    setDraft(value)
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${Math.min(el.scrollHeight, 140)}px`
    }
  }

  function handleComposerKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submitDraft()
    }
  }

  function scrollToBottom() {
    bottomRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }

  if (!connected) {
    return <div className="empty-state">先在设置中配对桌面端。</div>
  }

  if (selectedConversationId) {
    return (
      <div className="screen-stack conversation-screen">
        {detail ? (
          <>
            <section className="message-list">
              {detail.messages.length > 0 ? (
                detail.messages.map((message) => (
                  <MessageCard
                    key={message.id}
                    agent={message.agentId ? agentById.get(message.agentId) : undefined}
                    artifactById={artifactById}
                    message={message}
                    onOpenArtifact={onOpenArtifact}
                    isLatestUser={message.id === latestUserMessageId}
                    isLatestAgent={message.id === latestAgentMessageId}
                    busy={busy}
                    onWithdraw={onWithdrawMessage}
                    onEdit={onEditMessage}
                    onRegenerate={onRegenerate}
                  />
                ))
              ) : (
                <div className="empty-state">这个会话还没有消息。</div>
              )}
            </section>

            {showScrollButton && (
              <button type="button" className="scroll-to-bottom" aria-label="回到底部" onClick={scrollToBottom}>
                <ArrowDown className="button-icon" aria-hidden="true" />
              </button>
            )}
            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault()
                submitDraft()
              }}
            >
              <textarea
                ref={textareaRef}
                value={draft}
                rows={1}
                placeholder="输入意见或追问…"
                onChange={(event) => handleDraftChange(event.target.value)}
                onKeyDown={handleComposerKeyDown}
              />
              <button type="submit" className="primary-action icon-action" aria-label="发送" disabled={!draft.trim()}>
                <Send className="button-icon" aria-hidden="true" />
              </button>
            </form>
            <div ref={bottomRef} className="conversation-bottom-anchor" aria-hidden="true" />
          </>
        ) : (
          <div className="empty-state">{loading ? '加载会话中...' : '会话详情暂不可用。'}</div>
        )}
      </div>
    )
  }

  return (
    <section className="list-section">
      <h2 className="section-title">会话</h2>
      {snapshot && snapshot.conversations.length > 0 ? (
        <div className="group-card">
          {snapshot.conversations.map((conv) => (
            <ConversationRow key={conv.id} conv={conv} onOpen={onOpenConversation} />
          ))}
        </div>
      ) : (
        <div className="empty-state">数据同步中</div>
      )}
    </section>
  )
}

function MessageCard({
  message,
  agent,
  artifactById,
  onOpenArtifact,
  isLatestUser,
  isLatestAgent,
  busy,
  onWithdraw,
  onEdit,
  onRegenerate,
}: {
  message: MobileMessage
  agent?: MobileAgent
  artifactById: Map<string, MobileArtifactSummary>
  onOpenArtifact: (id: string) => void
  isLatestUser: boolean
  isLatestAgent: boolean
  busy: boolean
  onWithdraw: (messageId: string) => void
  onEdit: (messageId: string, content: string) => void
  onRegenerate: () => void
}) {
  const isUser = message.role === 'user'
  const displayName = isUser ? '你' : agent?.name ?? message.agentId ?? roleLabel(message.role)
  const avatar = isUser ? 'ME' : message.role === 'system' ? 'SY' : avatarInitials(displayName)
  const toneKey = isUser ? 'mobile-user' : agent?.id ?? message.agentId ?? message.role

  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState('')
  const [confirmingWithdraw, setConfirmingWithdraw] = useState(false)

  function startEdit() {
    const part = message.parts.find((p) => p.type === 'text')
    setEditDraft(part && part.type === 'text' ? part.content : '')
    setConfirmingWithdraw(false)
    setEditing(true)
  }
  function saveEdit() {
    const next = editDraft.trim()
    if (!next) return
    onEdit(message.id, next)
    setEditing(false)
  }

  return (
    <article className={`message-row ${message.role}`}>
      {!isUser && <AvatarBadge className="message-avatar" label={avatar} toneKey={toneKey} />}
      <div className="message-column">
        <div className="message-meta">
          <span>{displayName}</span>
          <time>{formatTime(message.createdAt)}</time>
        </div>
        {editing ? (
          <div className="message-edit">
            <textarea
              className="message-edit-input"
              value={editDraft}
              autoFocus
              rows={3}
              onChange={(event) => setEditDraft(event.target.value)}
            />
            <div className="message-actions">
              <button
                type="button"
                className="msg-action danger"
                aria-label="保存并重发"
                disabled={busy || !editDraft.trim()}
                onClick={saveEdit}
              >
                <Check className="button-icon" aria-hidden="true" />
              </button>
              <button type="button" className="msg-action" aria-label="取消编辑" disabled={busy} onClick={() => setEditing(false)}>
                <X className="button-icon" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : (
          <div className="message-bubble">
            <MessagePartsView artifactById={artifactById} message={message} onOpenArtifact={onOpenArtifact} />
          </div>
        )}
        <MessageStatus status={message.status} />
        {!editing && (isLatestUser || isLatestAgent) && (
          <div className="message-actions">
            {isLatestUser && !confirmingWithdraw && (
              <button type="button" className="msg-action" aria-label="编辑并重发" disabled={busy} onClick={startEdit}>
                <Pencil className="button-icon" aria-hidden="true" />
              </button>
            )}
            {isLatestUser && confirmingWithdraw ? (
              <>
                <button
                  type="button"
                  className="msg-action danger"
                  aria-label="确认撤回"
                  disabled={busy}
                  onClick={() => {
                    setConfirmingWithdraw(false)
                    onWithdraw(message.id)
                  }}
                >
                  <Check className="button-icon" aria-hidden="true" />
                </button>
                <button type="button" className="msg-action" aria-label="取消撤回" disabled={busy} onClick={() => setConfirmingWithdraw(false)}>
                  <X className="button-icon" aria-hidden="true" />
                </button>
              </>
            ) : (
              isLatestUser && (
                <button type="button" className="msg-action" aria-label="撤回" disabled={busy} onClick={() => setConfirmingWithdraw(true)}>
                  <Trash2 className="button-icon" aria-hidden="true" />
                </button>
              )
            )}
            {isLatestAgent && (
              <button type="button" className="msg-action" aria-label="重新生成" disabled={busy} onClick={onRegenerate}>
                <RotateCcw className="button-icon" aria-hidden="true" />
              </button>
            )}
          </div>
        )}
      </div>
      {isUser && <AvatarBadge className="message-avatar user-avatar" label={avatar} toneKey={toneKey} />}
    </article>
  )
}

function MessageStatus({ status }: { status: MobileMessage['status'] }) {
  if (status === 'streaming') {
    return (
      <span className="msg-status">
        正在生成
        <span className="typing-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span className="msg-status error">
        <CircleAlert className="inline-icon" aria-hidden="true" />
        出错
      </span>
    )
  }
  if (status === 'aborted') {
    return <span className="msg-status aborted">已中止</span>
  }
  return null
}

type MobileToolUsePart = Extract<MobileMessagePart, { type: 'tool_use' }>
type MobileToolResultPart = Extract<MobileMessagePart, { type: 'tool_result' }>
type ToolActivityState = 'running' | 'success' | 'error'

type MessageRenderItem =
  | { kind: 'part'; index: number; part: MobileMessagePart }
  | {
      kind: 'tool_activity'
      index: number
      tools: Array<{ index: number; part: MobileToolUsePart; completion?: MobileToolResultPart }>
      unmatchedResults: Array<{ index: number; part: MobileToolResultPart }>
    }

function MessagePartsView({
  message,
  artifactById,
  onOpenArtifact,
}: {
  message: MobileMessage
  artifactById: Map<string, MobileArtifactSummary>
  onOpenArtifact: (id: string) => void
}) {
  const items = useMemo(() => createMessageRenderItems(message.parts), [message.parts])

  return (
    <div className="message-parts">
      {items.map((item) =>
        item.kind === 'tool_activity' ? (
          <ToolActivityBlock
            key={`${message.id}-tool-${item.index}`}
            tools={item.tools}
            unmatchedResults={item.unmatchedResults}
          />
        ) : (
          <MessagePartView
            key={`${message.id}-${item.index}`}
            artifact={item.part.type === 'artifact_ref' ? artifactById.get(item.part.artifactId) : undefined}
            part={item.part}
            onOpenArtifact={onOpenArtifact}
          />
        ),
      )}
    </div>
  )
}

function MessagePartView({
  part,
  artifact,
  onOpenArtifact,
}: {
  part: MobileMessagePart
  artifact?: MobileArtifactSummary
  onOpenArtifact: (id: string) => void
}) {
  switch (part.type) {
    case 'text':
      return <MarkdownText content={part.content} />
    case 'thinking':
      return (
        <details className="thinking-block">
          <summary>
            <Brain className="inline-icon" aria-hidden="true" />
            <span>思考</span>
            <ChevronDown className="thinking-toggle-icon thinking-toggle-closed" aria-hidden="true" />
            <ChevronUp className="thinking-toggle-icon thinking-toggle-open" aria-hidden="true" />
          </summary>
          <MarkdownText content={part.content} muted />
        </details>
      )
    case 'code':
      return <CodeBlock code={part.content} language={part.language} />
    case 'tool_use':
    case 'tool_result':
      return null
    case 'artifact_ref':
      return (
        <button type="button" className="artifact-ref-card" onClick={() => onOpenArtifact(part.artifactId)}>
          <ArtifactIcon type={artifact?.type} />
          <span className="artifact-ref-main">
            <span>{artifact?.title ?? '产物'}</span>
            <small>
              {artifact ? `${artifact.type} · v${artifact.version}` : part.artifactId} · 点击预览
            </small>
          </span>
          <ChevronRight className="chevron-icon" aria-hidden="true" />
        </button>
      )
    case 'deploy_status':
      return (
        <span className="inline-chip">
          {part.status === 'ready' ? (
            <Rocket className="inline-icon" aria-hidden="true" />
          ) : (
            <XCircle className="inline-icon" aria-hidden="true" />
          )}
          {part.status === 'ready'
            ? `部署预览：${part.title} ${formatDeploySource(part)}`
            : `部署失败：${part.error ?? part.title}`}
        </span>
      )
    case 'attachment':
      return (
        <span className="inline-chip">
          {part.kind === 'image' ? (
            <ImageIcon className="inline-icon" aria-hidden="true" />
          ) : (
            <Paperclip className="inline-icon" aria-hidden="true" />
          )}
          {part.kind === 'image' ? '图片' : '文件'}：{part.fileName}
        </span>
      )
  }
}

function formatDeploySource(part: Extract<MobileMessagePart, { type: 'deploy_status' }>): string {
  if (part.sourceType === 'workspace') {
    return `工作区 ${part.workspacePath ?? '目录'}`
  }
  return `v${part.version}`
}

function ToolActivityBlock({
  tools,
  unmatchedResults,
}: {
  tools: Array<{ index: number; part: MobileToolUsePart; completion?: MobileToolResultPart }>
  unmatchedResults: Array<{ index: number; part: MobileToolResultPart }>
}) {
  const runningCount = tools.filter((tool) => !tool.completion).length
  const errorCount =
    tools.filter((tool) => tool.completion?.isError).length +
    unmatchedResults.filter((result) => result.part.isError).length
  const state: ToolActivityState = runningCount > 0 ? 'running' : errorCount > 0 ? 'error' : 'success'
  const distribution = formatToolDistribution(tools.map((tool) => tool.part.toolName))
  const title = tools.length > 1 ? `工具调用 × ${tools.length}` : tools[0]?.part.toolName ?? '工具结果'
  const statusText = formatToolActivityStatus(state, runningCount, errorCount, tools.length)
  const shouldCollapse = tools.length > 1 || unmatchedResults.length > 0

  if (!shouldCollapse) {
    return (
      <div className={`tool-activity ${state}`}>
        <div className="tool-activity-row">
          <ToolStateIcon state={state} />
          <span className="tool-activity-title">
            <Wrench className="inline-icon" aria-hidden="true" />
            {title}
          </span>
          <span className="tool-activity-status">{statusText}</span>
        </div>
      </div>
    )
  }

  return (
    <details className={`tool-activity ${state}`}>
      <summary className="tool-activity-row">
        <ToolStateIcon state={state} />
        <span className="tool-activity-title">
          <Wrench className="inline-icon" aria-hidden="true" />
          {title}
        </span>
        {distribution && <span className="tool-activity-meta">{distribution}</span>}
        <span className="tool-activity-status">{statusText}</span>
        <ChevronDown className="tool-activity-chevron" aria-hidden="true" />
      </summary>
      <div className="tool-activity-list">
        {tools.map((tool) => (
          <div key={tool.index} className="tool-activity-item">
            <ToolStateIcon state={tool.completion ? (tool.completion.isError ? 'error' : 'success') : 'running'} />
            <code>{tool.part.toolName}</code>
            <span>{tool.completion ? (tool.completion.isError ? '失败' : '完成') : '调用中'}</span>
          </div>
        ))}
        {unmatchedResults.map((result) => (
          <div key={result.index} className="tool-activity-item">
            <ToolStateIcon state={result.part.isError ? 'error' : 'success'} />
            <code>result</code>
            <span>{result.part.isError ? '失败' : '完成'}</span>
          </div>
        ))}
      </div>
    </details>
  )
}

function ToolStateIcon({ state }: { state: ToolActivityState }) {
  if (state === 'running') return <Clock3 className="tool-state-icon" aria-hidden="true" />
  if (state === 'error') return <CircleAlert className="tool-state-icon" aria-hidden="true" />
  return <CheckCircle2 className="tool-state-icon" aria-hidden="true" />
}

function ArtifactIcon({ type }: { type?: string }) {
  if (type === 'document') return <FileText className="artifact-ref-icon" aria-hidden="true" />
  if (type === 'image') return <ImageIcon className="artifact-ref-icon" aria-hidden="true" />
  if (type === 'web_app') return <Layers className="artifact-ref-icon" aria-hidden="true" />
  return <Code2 className="artifact-ref-icon" aria-hidden="true" />
}

function MarkdownText({ content, muted = false }: { content: string; muted?: boolean }) {
  if (!content) return null

  return (
    <div className={muted ? 'mobile-markdown muted-text' : 'mobile-markdown'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: ({ className, children }) => {
            const language = className?.match(/language-([^\s]+)/)?.[1]
            if (language) {
              return <CodeBlock code={reactNodeToText(children).replace(/\n$/, '')} language={language} />
            }
            return <code className="inline-code">{children}</code>
          },
          pre: ({ children }) => {
            if (isCodeBlockChild(children)) return <>{children}</>
            const block = extractPreCode(children)
            if (block) return <CodeBlock code={block.code} language={block.language} />
            return <pre className="markdown-pre">{children}</pre>
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function isCodeBlockChild(node: ReactNode): boolean {
  if (Array.isArray(node)) return node.some(isCodeBlockChild)
  return isValidElement(node) && node.type === CodeBlock
}

function CodeBlock({ code, language }: { code: string; language?: string }) {
  return (
    <figure className="code-block">
      <figcaption>
        <Code2 className="inline-icon" aria-hidden="true" />
        {language || 'code'}
      </figcaption>
      <pre>{code}</pre>
    </figure>
  )
}

function extractPreCode(node: ReactNode): { code: string; language: string } | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const block = extractPreCode(child)
      if (block) return block
    }
    return null
  }

  if (!isValidElement(node) || node.type !== 'code') return null

  const props = node.props as { className?: string; children?: ReactNode }
  const raw = reactNodeToText(props.children).replace(/\n$/, '')
  const language = props.className?.match(/language-([^\s]+)/)?.[1] ?? ''
  return { code: raw, language }
}

function reactNodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(reactNodeToText).join('')
  return ''
}

function createMessageRenderItems(parts: MobileMessagePart[]): MessageRenderItem[] {
  const items: MessageRenderItem[] = []
  let toolBlock: Array<{ index: number; part: MobileToolUsePart | MobileToolResultPart }> = []

  function flushToolBlock() {
    if (toolBlock.length === 0) return
    items.push(createToolActivityItem(toolBlock))
    toolBlock = []
  }

  parts.forEach((part, index) => {
    if (part.type === 'tool_use' || part.type === 'tool_result') {
      toolBlock.push({ index, part })
      return
    }

    flushToolBlock()
    items.push({ kind: 'part', index, part })
  })

  flushToolBlock()
  return items
}

function createToolActivityItem(
  toolBlock: Array<{ index: number; part: MobileToolUsePart | MobileToolResultPart }>,
): MessageRenderItem {
  const resultByCallId = new Map<string, MobileToolResultPart>()
  const toolCallIds = new Set<string>()

  for (const item of toolBlock) {
    if (item.part.type === 'tool_use') toolCallIds.add(item.part.callId)
    if (item.part.type === 'tool_result') resultByCallId.set(item.part.callId, item.part)
  }

  return {
    kind: 'tool_activity',
    index: toolBlock[0]?.index ?? 0,
    tools: toolBlock
      .filter((item): item is { index: number; part: MobileToolUsePart } => item.part.type === 'tool_use')
      .map((item) => ({
        index: item.index,
        part: item.part,
        completion: resultByCallId.get(item.part.callId),
      })),
    unmatchedResults: toolBlock.filter(
      (item): item is { index: number; part: MobileToolResultPart } =>
        item.part.type === 'tool_result' && !toolCallIds.has(item.part.callId),
    ),
  }
}

function formatToolDistribution(toolNames: string[]): string {
  if (toolNames.length <= 1) return ''

  const counts = new Map<string, number>()
  for (const name of toolNames) {
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }

  return Array.from(counts.entries())
    .map(([name, count]) => (count > 1 ? `${name}×${count}` : name))
    .join(' · ')
}

function formatToolActivityStatus(
  state: ToolActivityState,
  runningCount: number,
  errorCount: number,
  totalCount: number,
): string {
  if (state === 'running') return runningCount > 1 ? `${runningCount} 进行中` : '进行中'
  if (state === 'error') return errorCount > 1 ? `${errorCount} 失败` : '失败'
  return totalCount > 1 ? '全部完成' : '完成'
}

function buildConversationScrollSignature(detail: MobileConversationDetail | null): string {
  if (!detail) return ''

  const lastMessage = detail.messages.at(-1)
  if (!lastMessage) return `${detail.conversation.id}:empty`

  return [
    detail.conversation.id,
    detail.messages.length,
    lastMessage.id,
    lastMessage.status,
    lastMessage.parts.map(toMessagePartScrollKey).join('|'),
  ].join(':')
}

function toMessagePartScrollKey(part: MobileMessagePart): string {
  switch (part.type) {
    case 'text':
    case 'code':
    case 'thinking':
      return `${part.type}:${part.content.length}`
    case 'tool_use':
      return `${part.type}:${part.callId}:${part.toolName}`
    case 'tool_result':
      return `${part.type}:${part.callId}:${part.isError ? 'error' : 'ok'}`
    case 'artifact_ref':
      return `${part.type}:${part.artifactId}`
    case 'deploy_status':
      return `${part.type}:${part.status}:${part.previewPath}:${part.error ?? ''}`
    case 'attachment':
      return `${part.type}:${part.kind}:${part.fileName}`
  }
}

function isNearWindowBottom(threshold = 420): boolean {
  const scrollPosition = window.scrollY + window.innerHeight
  return document.documentElement.scrollHeight - scrollPosition <= threshold
}

function roleLabel(role: MobileMessage['role']): string {
  switch (role) {
    case 'user':
      return '你'
    case 'agent':
      return 'Agent'
    case 'system':
      return '系统'
  }
}
