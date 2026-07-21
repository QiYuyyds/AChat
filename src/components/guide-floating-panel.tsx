'use client'

import { nanoid } from 'nanoid'
import {
  Bot,
  ChevronDown,
  GripVertical,
  Send,
  Square,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { AgentWorkingIndicator } from '@/components/agent-working-indicator'
import { Markdown } from '@/components/markdown'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { createConversation, fetchMessages, sendMessage as sendMessageAPI, abortRun, submitQuestionAnswers } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { AskUserAnswer, AskUserQuestionItem, MessagePart, MessageRecord } from '@/shared/types'
import {
  useAppStore,
  useMessagesForConversation,
  usePendingQuestions,
  useTopLevelRunningRuns,
} from '@/stores/app-store'

// ─── localStorage keys ──────────────────────────────────────
const LS_KEY_PANEL = 'ach_guide_panel_state'

function loadPanelState(): {
  open: boolean
  position: { x: number; y: number }
  size: { width: number; height: number }
  collapsedPosition: { x: number; y: number } | null
} {
  try {
    const raw = localStorage.getItem(LS_KEY_PANEL)
    if (raw) {
      // 旧数据没有 collapsedPosition，用 null 兜底（表示默认右下角）
      const parsed = JSON.parse(raw)
      return {
        open: false,
        position: { x: 16, y: 16 },
        size: { width: 400, height: 600 },
        collapsedPosition: null,
        ...parsed,
      }
    }
  } catch { /* ignore */ }
  return { open: false, position: { x: 16, y: 16 }, size: { width: 400, height: 600 }, collapsedPosition: null }
}

function savePanelState(state: { open: boolean; position: { x: number; y: number }; size: { width: number; height: number } }) {
  try {
    localStorage.setItem(LS_KEY_PANEL, JSON.stringify(state))
  } catch { /* ignore */ }
}

// ─── Size constraints ───────────────────────────────────────
const MIN_WIDTH = 320
const MIN_HEIGHT = 400
const MAX_WIDTH = 600
const MAX_HEIGHT = 800

// ─── Main component ─────────────────────────────────────────
export function GuideFloatingPanel() {
  const userId = useAppStore((s) => s.userId)
  const guideConversationId = useAppStore((s) => s.guideConversationId)
  const guideAgent = useAppStore((s) => Object.values(s.agents).find((a) => a.isGuide))
  const setGuideConversationId = useAppStore((s) => s.setGuideConversationId)
  const setGuidePanelState = useAppStore((s) => s.setGuidePanelState)
  const setMessagesForConversation = useAppStore((s) => s.setMessagesForConversation)

  // Local state from localStorage for persistence across sessions
  const [localState, setLocalState] = useState(loadPanelState)
  const isMobile = useIsMobile()

  // Auto-create guide conversation on first login
  useEffect(() => {
    if (!userId || guideConversationId) return
    let cancelled = false
    let createdConvId: string | undefined
    createConversation({
      mode: 'guide',
      agentIds: ['ag_guide_builtin'],
    })
      .then((conv) => {
        if (!cancelled) {
          createdConvId = conv.id
          setGuideConversationId(conv.id)
          setLocalState((prev) => {
            const next = { ...prev, open: true }
            savePanelState(next)
            return next
          })
          // Load initial messages
          return fetchMessages(conv.id)
        }
      })
      .then((messages) => {
        if (!cancelled && messages && createdConvId) {
          setMessagesForConversation(createdConvId, messages)
        }
      })
      .catch((err) => {
        console.error('[GuideFloatingPanel] failed to create guide conversation', err)
      })
    return () => { cancelled = true }
  }, [userId, guideConversationId, setGuideConversationId, setMessagesForConversation])

  // Sync local state to store（不含 collapsedPosition，那是收起按钮的纯 UI 状态）
  useEffect(() => {
    setGuidePanelState({
      open: localState.open,
      position: localState.position,
      size: localState.size,
    })
  }, [localState.open, localState.position, localState.size, setGuidePanelState])

  // Ctrl/Cmd+G toggle
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'g') {
        e.preventDefault()
        setLocalState((prev) => {
          const next = { ...prev, open: !prev.open }
          savePanelState(next)
          return next
        })
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Drag state
  const [dragging, setDragging] = useState(false)
  const dragOffset = useRef({ x: 0, y: 0 })

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if (isMobile) return
    e.preventDefault()
    setDragging(true)
    dragOffset.current = {
      x: e.clientX - localState.position.x,
      y: e.clientY - localState.position.y,
    }
  }, [isMobile, localState.position])

  useEffect(() => {
    if (!dragging) return
    const onMove = (e: MouseEvent) => {
      setLocalState((prev) => {
        const x = Math.max(0, Math.min(e.clientX - dragOffset.current.x, window.innerWidth - prev.size.width))
        const y = Math.max(0, Math.min(e.clientY - dragOffset.current.y, window.innerHeight - prev.size.height))
        const next = { ...prev, position: { x, y } }
        savePanelState(next)
        return next
      })
    }
    const onUp = () => setDragging(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [dragging])

  // Resize state
  const [resizing, setResizing] = useState(false)
  const resizeStart = useRef({ x: 0, y: 0, w: 0, h: 0 })

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    if (isMobile) return
    e.preventDefault()
    e.stopPropagation()
    setResizing(true)
    resizeStart.current = {
      x: e.clientX,
      y: e.clientY,
      w: localState.size.width,
      h: localState.size.height,
    }
  }, [isMobile, localState.size])

  useEffect(() => {
    if (!resizing) return
    const onMove = (e: MouseEvent) => {
      setLocalState((prev) => {
        const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, resizeStart.current.w + (e.clientX - resizeStart.current.x)))
        const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, resizeStart.current.h + (e.clientY - resizeStart.current.y)))
        const next = { ...prev, size: { width, height } }
        savePanelState(next)
        return next
      })
    }
    const onUp = () => setResizing(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [resizing])

  // Unread count for collapsed state
  const unreadCount = useAppStore((s) =>
    guideConversationId ? s.unreadByConv[guideConversationId] ?? 0 : 0,
  )

  const toggleOpen = useCallback(() => {
    setLocalState((prev) => {
      const next = { ...prev, open: !prev.open }
      savePanelState(next)
      return next
    })
  }, [])

  // Collapsed button drag state（收起态浮动按钮可拖动到任意位置）
  const [collapsedDragging, setCollapsedDragging] = useState(false)
  const collapsedDragOffset = useRef({ x: 0, y: 0 })
  const collapsedDragStart = useRef({ x: 0, y: 0 })
  const collapsedDragMoved = useRef(false)
  const COLLAPSED_DRAG_THRESHOLD = 4
  const COLLAPSED_BTN_SIZE = 48 // size-12

  const handleCollapsedDragStart = useCallback((e: React.MouseEvent) => {
    if (isMobile) return
    if (e.button !== 0) return
    e.preventDefault()
    // 首次拖动时把当前默认右下角位置固化为绝对坐标
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const cur = localState.collapsedPosition ?? { x: rect.left, y: rect.top }
    collapsedDragOffset.current = { x: e.clientX - cur.x, y: e.clientY - cur.y }
    collapsedDragStart.current = { x: e.clientX, y: e.clientY }
    collapsedDragMoved.current = false
    setCollapsedDragging(true)
  }, [isMobile, localState.collapsedPosition])

  useEffect(() => {
    if (!collapsedDragging) return
    const onMove = (e: MouseEvent) => {
      const dx = e.clientX - collapsedDragStart.current.x
      const dy = e.clientY - collapsedDragStart.current.y
      // 移动未超阈值时不进入拖动态，保留纯点击语义
      if (!collapsedDragMoved.current && Math.hypot(dx, dy) < COLLAPSED_DRAG_THRESHOLD) return
      collapsedDragMoved.current = true
      setLocalState((prev) => {
        const x = Math.max(0, Math.min(e.clientX - collapsedDragOffset.current.x, window.innerWidth - COLLAPSED_BTN_SIZE))
        const y = Math.max(0, Math.min(e.clientY - collapsedDragOffset.current.y, window.innerHeight - COLLAPSED_BTN_SIZE))
        const next = { ...prev, collapsedPosition: { x, y } }
        savePanelState(next)
        return next
      })
    }
    const onUp = () => setCollapsedDragging(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [collapsedDragging])

  const handleCollapsedClick = useCallback(() => {
    // 拖动后不触发展开，避免拖完就误开
    if (collapsedDragMoved.current) {
      collapsedDragMoved.current = false
      return
    }
    toggleOpen()
  }, [toggleOpen])

  // Mobile: full screen overlay
  if (isMobile && localState.open && guideConversationId) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-background">
        <GuidePanelHeader
          onClose={toggleOpen}
          draggable={false}
          onDragStart={() => {}}
        />
        <GuideMessageList conversationId={guideConversationId} />
        <GuideMessageInput conversationId={guideConversationId} />
      </div>
    )
  }

  // Collapsed: floating button with unread indicator（可拖动到任意位置）
  if (!localState.open) {
    const collapsedPos = localState.collapsedPosition
    return (
      <button
        type="button"
        onMouseDown={handleCollapsedDragStart}
        onClick={handleCollapsedClick}
        className={cn(
          'fixed z-50 flex size-12 items-center justify-center rounded-full',
          'bg-gradient-to-br from-primary to-primary/80 text-primary-foreground',
          'shadow-lg shadow-primary/30 ring-1 ring-white/20',
          'transition-all duration-300 ease-out',
          collapsedDragging
            ? 'cursor-grabbing scale-110 select-none'
            : cn('hover:scale-110 hover:shadow-xl hover:shadow-primary/40', collapsedPos ? 'cursor-grab' : 'cursor-pointer'),
        )}
        style={collapsedPos ? { left: collapsedPos.x, top: collapsedPos.y } : { right: 24, bottom: 24 }}
        title="打开小A (Ctrl+G)"
        aria-label="打开小A"
      >
        {guideAgent ? (
          <AgentAvatar agent={guideAgent} size="lg" className="size-10 ring-2 ring-white/30 drop-shadow-sm" />
        ) : (
          <Bot className="size-5 drop-shadow-sm" />
        )}
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex size-5 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-white ring-2 ring-background">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>
    )
  }

  if (!guideConversationId) return null

  // Expanded: floating panel
  return (
    <div
      className={cn(
        'fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-border/60 bg-background shadow-2xl shadow-black/10 ring-1 ring-black/5',
        dragging && 'cursor-grabbing select-none',
        resizing && 'select-none',
      )}
      style={{
        left: localState.position.x,
        top: localState.position.y,
        width: localState.size.width,
        height: localState.size.height,
      }}
    >
      <GuidePanelHeader
        onClose={toggleOpen}
        draggable
        onDragStart={handleDragStart}
      />
      <GuideMessageList conversationId={guideConversationId} />
      <GuideMessageInput conversationId={guideConversationId} />

      {/* Resize handle */}
      <div
        className="absolute bottom-0 right-0 size-5 cursor-se-resize opacity-0 transition-opacity hover:opacity-100"
        onMouseDown={handleResizeStart}
      >
        <GripVertical className="size-3 -rotate-45 text-muted-foreground/60" />
      </div>
    </div>
  )
}

// ─── Header ─────────────────────────────────────────────────
function GuidePanelHeader({
  onClose,
  draggable,
  onDragStart,
}: {
  onClose: () => void
  draggable: boolean
  onDragStart: (e: React.MouseEvent) => void
}) {
  const agents = useAppStore((s) => s.agents)
  const guideAgent = Object.values(agents).find((a) => a.isGuide)
  const streamConnected = useAppStore((s) => s.streamConnected)

  return (
    <div
      className={cn(
        'flex shrink-0 items-center gap-2.5 border-b border-border/50 px-3.5 py-2.5',
        'bg-gradient-to-b from-primary/[0.06] to-transparent backdrop-blur-sm',
      )}
      onMouseDown={draggable ? onDragStart : undefined}
      style={draggable ? { cursor: 'grab' } : undefined}
    >
      {guideAgent ? (
        <div className="ring-2 ring-primary/20 ring-offset-1 ring-offset-background rounded-full">
          <AgentAvatar agent={guideAgent} size="sm" />
        </div>
      ) : (
        <div className="flex size-6 items-center justify-center rounded-full bg-gradient-to-br from-primary to-primary/80 text-xs text-primary-foreground ring-2 ring-primary/20">
          🅰️
        </div>
      )}
      <span className="flex-1 truncate text-sm font-semibold tracking-tight">小A</span>
      {/* Connection status indicator with subtle pulse */}
      <span
        className={cn(
          'size-2 rounded-full',
          streamConnected
            ? 'bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.6)] animate-pulse'
            : 'bg-muted-foreground/30',
        )}
        title={streamConnected ? '已连接' : '未连接'}
      />
      <Button type="button" variant="ghost" size="icon" className="size-6 hover:bg-muted/80" onClick={onClose} title="收起 (Ctrl+G)" aria-label="收起">
        <ChevronDown className="size-3.5" />
      </Button>
    </div>
  )
}

// ─── Simplified Message List ────────────────────────────────
function GuideMessageList({ conversationId }: { conversationId: string }) {
  const messages = useMessagesForConversation(conversationId)
  const runningRuns = useTopLevelRunningRuns(conversationId)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages or working indicator appearing
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, runningRuns.length])

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2.5">
      {messages.length === 0 && runningRuns.length === 0 && (
        <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
          <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10">
            <Bot className="size-6 text-primary/70" />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium text-foreground/80">有什么我可以帮忙的？</p>
            <p className="text-xs text-muted-foreground">试试说「帮我创建一个 Agent」</p>
          </div>
        </div>
      )}
      {messages.map((msg) => (
        <GuideMessageBubble key={msg.id} message={msg} />
      ))}
      <GuideInlineAskUser conversationId={conversationId} />
      {runningRuns.map((run) => (
        <div key={`indicator-${run.id}`} className="mt-1">
          <AgentWorkingIndicator run={run} conversationId={conversationId} />
        </div>
      ))}
    </div>
  )
}

// ─── Message Bubble ─────────────────────────────────────────
function GuideMessageBubble({ message }: { message: MessageRecord }) {
  const isUser = message.role === 'user'

  return (
    <div
      className={cn(
        'flex flex-col gap-1 animate-in fade-in slide-in-from-bottom-1 duration-300',
        isUser ? 'items-end' : 'items-start',
      )}
    >
      {/* Simplified part rendering: text, tool_use (folded) */}
      {message.parts.map((part, i) => {
        switch (part.type) {
          case 'text':
            return (
              <div
                key={i}
                className={cn(
                  'max-w-[85%] px-3.5 py-2.5 text-sm leading-relaxed shadow-sm',
                  isUser
                    ? 'rounded-2xl rounded-tr-md bg-gradient-to-br from-primary to-primary/90 text-primary-foreground'
                    : 'rounded-2xl rounded-tl-md bg-gradient-to-br from-muted to-muted/60 text-foreground',
                )}
              >
                <Markdown>{part.content}</Markdown>
              </div>
            )
          case 'tool_use':
            return <GuideToolUseCard key={i} part={part} message={message} />
          case 'thinking':
            return null // 不渲染 thinking
          default:
            return null // 不渲染其他 part 类型
        }
      })}
    </div>
  )
}

// ─── Tool Use Card (collapsed) ──────────────────────────────
function GuideToolUseCard({
  part,
  message,
}: {
  part: Extract<MessagePart, { type: 'tool_use' }>
  message: MessageRecord
}) {
  const [expanded, setExpanded] = useState(false)
  // Find matching tool_result
  const resultPart = message.parts.find(
    (p) => p.type === 'tool_result' && p.callId === part.callId,
  )

  return (
    <div className="max-w-[85%] overflow-hidden rounded-xl border border-border/50 bg-muted/40 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/60"
      >
        {resultPart && resultPart.type === 'tool_result' && (
          resultPart.isError ? (
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-destructive/15">
              <X className="size-2.5 text-destructive" />
            </span>
          ) : (
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-green-500/15">
              <Square className="size-2 text-green-600" />
            </span>
          )
        )}
        <span className="truncate font-mono text-muted-foreground">{part.toolName}</span>
        <ChevronDown className={cn('ml-auto size-3 shrink-0 text-muted-foreground/60 transition-transform duration-200', expanded && 'rotate-180')} />
      </button>
      {expanded && (
        <div className="border-t border-border/40 bg-muted/20 px-3 py-2">
          <pre className="whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
            {JSON.stringify(part.args, null, 2)}
          </pre>
          {resultPart && resultPart.type === 'tool_result' && (
            <pre className="mt-1.5 whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
              {typeof resultPart.result === 'string' ? resultPart.result : JSON.stringify(resultPart.result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Inline ask_user（消息列表底部只渲染一次，多问题一次性提交）──
function GuideInlineAskUser({ conversationId }: { conversationId: string }) {
  const pending = usePendingQuestions(conversationId)
  const current = pending[0]

  const [draft, setDraft] = useState<Record<string, AskUserAnswer>>({})
  const [busy, setBusy] = useState(false)

  // 切换到新的 pending question 时重置 draft 和 busy
  useEffect(() => {
    setBusy(false)
    if (!current) {
      setDraft({})
      return
    }
    const init: Record<string, AskUserAnswer> = {}
    for (const q of current.questions) {
      init[q.question] = { selectedLabels: [], freeformNote: '' }
    }
    setDraft(init)
  }, [current])

  if (!current) return null

  const allAnswered = current.questions.every((q) => {
    const a = draft[q.question]
    return a && a.selectedLabels.length > 0
  })

  const handleToggle = (q: AskUserQuestionItem, label: string) => {
    setDraft((prev) => {
      const cur = prev[q.question] ?? { selectedLabels: [], freeformNote: '' }
      const exists = cur.selectedLabels.includes(label)
      let nextLabels: string[]
      if (q.multiSelect) {
        nextLabels = exists
          ? cur.selectedLabels.filter((l) => l !== label)
          : [...cur.selectedLabels, label]
      } else {
        nextLabels = exists ? [] : [label]
      }
      return { ...prev, [q.question]: { ...cur, selectedLabels: nextLabels } }
    })
  }

  const handleSubmit = async () => {
    if (busy || !allAnswered) return
    setBusy(true)
    try {
      await submitQuestionAnswers(conversationId, current.id, draft)
      // SSE 会把 pending 从 store 移除
    } catch (err) {
      console.error('[GuideInlineAskUser] submit failed', err)
      setBusy(false)
    }
  }

  return (
    <div className="max-w-[90%] animate-in fade-in slide-in-from-bottom-2 duration-300 rounded-2xl rounded-tl-md border border-primary/20 bg-primary/[0.04] p-3 space-y-3">
      {current.questions.map((q, idx) => {
        const selected = new Set(draft[q.question]?.selectedLabels ?? [])
        return (
          <div key={`${q.question}-${idx}`} className="space-y-2">
            <div className="flex items-center gap-1.5">
              <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                {q.header}
              </span>
              {q.multiSelect && (
                <span className="shrink-0 text-[10px] text-muted-foreground">多选</span>
              )}
            </div>
            <p className="text-sm font-medium leading-snug">{q.question}</p>
            <div className="flex flex-wrap gap-1.5">
              {q.options.map((opt) => {
                const isSel = selected.has(opt.label)
                return (
                  <button
                    key={opt.label}
                    type="button"
                    onClick={() => handleToggle(q, opt.label)}
                    className={cn(
                      'rounded-lg border px-3 py-1.5 text-xs font-medium transition-all duration-200',
                      isSel
                        ? 'border-primary bg-primary/10 text-primary shadow-sm'
                        : 'border-border/50 bg-background hover:border-primary/30 hover:bg-primary/5 active:scale-95',
                    )}
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
      <Button
        type="button"
        size="sm"
        onClick={() => void handleSubmit()}
        disabled={!allAnswered || busy}
        className="w-full transition-transform active:scale-95"
        title={allAnswered ? '提交答案' : '请先回答所有问题'}
      >
        {busy ? '提交中…' : '提交答案'}
      </Button>
    </div>
  )
}

// ─── Simplified Message Input ───────────────────────────────
function GuideMessageInput({ conversationId }: { conversationId: string }) {
  const [content, setContent] = useState('')
  const [sending, setSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const runningRuns = useTopLevelRunningRuns(conversationId)
  const isRunning = runningRuns.length > 0
  const addLocalUserMessage = useAppStore((s) => s.addLocalUserMessage)
  const replaceLocalMessageId = useAppStore((s) => s.replaceLocalMessageId)
  const upsertMessage = useAppStore((s) => s.upsertMessage)

  const submit = async () => {
    const text = content.trim()
    if (!text || sending || isRunning) return

    const tempId = `temp_${nanoid()}`
    addLocalUserMessage({
      tempId,
      conversationId,
      content: text,
      mentionedAgentIds: ['ag_guide_builtin'],
    })
    setContent('')
    setSending(true)

    try {
      const result = await sendMessageAPI(conversationId, {
        content: text,
        mentionedAgentIds: ['ag_guide_builtin'],
      })
      replaceLocalMessageId(tempId, result.messageId)
      if (result.messages) {
        for (const m of result.messages) upsertMessage(m)
      }
    } catch (err) {
      console.error('[GuideMessageInput] send failed', err)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  const handleAbort = async () => {
    await Promise.allSettled(runningRuns.map((r) => abortRun(r.id)))
  }

  return (
    <div className="relative shrink-0">
      {/* Top fade for smooth visual transition from message list */}
      <div className="pointer-events-none absolute -top-3 left-0 right-0 h-3 bg-gradient-to-t from-background to-transparent" />
      <div className="flex items-center gap-2 border-t border-border/50 bg-background/80 px-3 py-2.5 backdrop-blur-sm">
        <Textarea
          ref={textareaRef}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isRunning ? '小A正在响应…' : '跟小A说…'}
          className="min-h-[36px] max-h-24 resize-none border-border/50 bg-muted/30 text-sm focus-visible:ring-primary/30"
          disabled={isRunning}
        />
        {isRunning ? (
          <Button
            type="button"
            size="icon"
            variant="destructive"
            onClick={() => void handleAbort()}
            className="size-8 shrink-0 transition-transform hover:scale-105 active:scale-95"
            title="中止"
          >
            <Square className="size-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            onClick={() => void submit()}
            disabled={!content.trim() || sending}
            className="size-8 shrink-0 transition-transform hover:scale-105 active:scale-95 disabled:scale-100"
            title="发送"
          >
            <Send className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}

// ─── Mobile detection hook ──────────────────────────────────
function useIsMobile() {
  const [isMobile, setIsMobile] = useState(false)
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768)
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])
  return isMobile
}
