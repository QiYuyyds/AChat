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

function loadPanelState(): { open: boolean; position: { x: number; y: number }; size: { width: number; height: number } } {
  try {
    const raw = localStorage.getItem(LS_KEY_PANEL)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return { open: false, position: { x: 16, y: 16 }, size: { width: 400, height: 600 } }
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

  // Sync local state to store
  useEffect(() => {
    setGuidePanelState({
      open: localState.open,
      position: localState.position,
      size: localState.size,
    })
  }, [localState, setGuidePanelState])

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

  // Collapsed: floating button with unread indicator
  if (!localState.open) {
    return (
      <button
        type="button"
        onClick={toggleOpen}
        className="fixed bottom-6 right-6 z-50 flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105"
        title="打开小A (Ctrl+G)"
        aria-label="打开小A"
      >
        <Bot className="size-5" />
        {unreadCount > 0 && (
          <span className="absolute -right-1 -top-1 flex size-5 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-white">
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
        'fixed z-50 flex flex-col overflow-hidden rounded-lg border bg-background shadow-xl',
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
        className="absolute right-0 bottom-0 size-4 cursor-se-resize"
        onMouseDown={handleResizeStart}
      >
        <GripVertical className="size-3 -rotate-45 text-muted-foreground/50" />
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
      className="flex shrink-0 items-center gap-2 border-b px-3 py-2"
      onMouseDown={draggable ? onDragStart : undefined}
      style={draggable ? { cursor: 'grab' } : undefined}
    >
      {guideAgent ? (
        <AgentAvatar agent={guideAgent} size="sm" />
      ) : (
        <div className="flex size-6 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
          🅰️
        </div>
      )}
      <span className="flex-1 truncate text-sm font-medium">小A</span>
      {/* Connection status indicator */}
      <span
        className={cn(
          'size-2 rounded-full',
          streamConnected ? 'bg-green-500' : 'bg-muted-foreground/30',
        )}
        title={streamConnected ? '已连接' : '未连接'}
      />
      <Button type="button" variant="ghost" size="icon" className="size-6" onClick={onClose} title="收起 (Ctrl+G)" aria-label="收起">
        <ChevronDown className="size-3.5" />
      </Button>
    </div>
  )
}

// ─── Simplified Message List ────────────────────────────────
function GuideMessageList({ conversationId }: { conversationId: string }) {
  const messages = useMessagesForConversation(conversationId)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-2 space-y-3">
      {messages.length === 0 && (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          有什么我可以帮忙的？试试说「帮我创建一个 Agent」
        </div>
      )}
      {messages.map((msg) => (
        <GuideMessageBubble key={msg.id} message={msg} conversationId={conversationId} />
      ))}
    </div>
  )
}

// ─── Message Bubble ─────────────────────────────────────────
function GuideMessageBubble({ message, conversationId }: { message: MessageRecord; conversationId: string }) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('flex flex-col gap-1', isUser ? 'items-end' : 'items-start')}>
      {/* Simplified part rendering: text, tool_use (folded), ask_user (inline) */}
      {message.parts.map((part, i) => {
        switch (part.type) {
          case 'text':
            return (
              <div
                key={i}
                className={cn(
                  'max-w-[85%] rounded-lg px-3 py-2 text-sm',
                  isUser
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted',
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

      {/* Inline ask_user rendering */}
      <GuideInlineAskUser conversationId={conversationId} />
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
    <div className="max-w-[85%] rounded-lg border bg-card text-xs">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        {resultPart && resultPart.type === 'tool_result' && (
          resultPart.isError ? (
            <X className="size-3 shrink-0 text-destructive" />
          ) : (
            <Square className="size-2.5 shrink-0 text-green-500" />
          )
        )}
        <span className="truncate font-mono">{part.toolName}</span>
        <ChevronDown className={cn('ml-auto size-3 shrink-0 transition-transform', expanded && 'rotate-180')} />
      </button>
      {expanded && (
        <div className="border-t px-2.5 py-1.5">
          <pre className="whitespace-pre-wrap break-all font-mono text-[10px] text-muted-foreground">
            {JSON.stringify(part.args, null, 2)}
          </pre>
          {resultPart && resultPart.type === 'tool_result' && (
            <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] text-muted-foreground">
              {typeof resultPart.result === 'string' ? resultPart.result : JSON.stringify(resultPart.result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Inline ask_user ────────────────────────────────────────
function GuideInlineAskUser({ conversationId }: { conversationId: string }) {
  const pending = usePendingQuestions(conversationId)
  const current = pending[0]
  if (!current) return null

  return (
    <div className="max-w-[85%] rounded-lg border bg-card p-2.5 text-xs space-y-2">
      {current.questions.map((q, idx) => (
        <div key={`${q.question}-${idx}`}>
          <div className="font-medium text-sm mb-1.5">{q.question}</div>
          <div className="flex flex-wrap gap-1.5">
            {q.options.map((opt) => (
              <GuideAskUserOptionButton
                key={opt.label}
                conversationId={conversationId}
                questionId={current.id}
                question={q}
                optionLabel={opt.label}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function GuideAskUserOptionButton({
  conversationId,
  questionId,
  question,
  optionLabel,
}: {
  conversationId: string
  questionId: string
  question: AskUserQuestionItem
  optionLabel: string
}) {
  const [selected, setSelected] = useState(false)
  const [busy, setBusy] = useState(false)

  const handleClick = async () => {
    if (busy || selected) return
    setSelected(true)
    setBusy(true)
    try {
      const answers: Record<string, AskUserAnswer> = {
        [question.question]: { selectedLabels: [optionLabel] },
      }
      await submitQuestionAnswers(conversationId, questionId, answers)
    } catch (err) {
      console.error('[GuideInlineAskUser] submit failed', err)
      setSelected(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={selected || busy}
      className={cn(
        'rounded-md border px-2.5 py-1.5 text-xs transition',
        selected
          ? 'border-primary bg-primary/10 text-primary'
          : 'border-transparent bg-muted hover:border-foreground/20',
      )}
    >
      {optionLabel}
    </button>
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
    <div className="flex shrink-0 items-center gap-2 border-t px-3 py-2">
      <Textarea
        ref={textareaRef}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={isRunning ? '小A正在响应…' : '跟小A说…'}
        className="min-h-[36px] max-h-24 resize-none text-sm"
        disabled={isRunning}
      />
      {isRunning ? (
        <Button
          type="button"
          size="icon"
          variant="destructive"
          onClick={() => void handleAbort()}
          className="size-8 shrink-0"
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
          className="size-8 shrink-0"
          title="发送"
        >
          <Send className="size-3.5" />
        </Button>
      )}
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
