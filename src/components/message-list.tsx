'use client'

import { Loader2, MessagesSquare } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { AgentWorkingIndicator } from '@/components/agent-working-indicator'
import { MessageItem } from '@/components/message-item'
import { ScrollArea } from '@/components/ui/scroll-area'
import { WaveColumnHeader } from '@/components/wave-column-header'
import type { MessageRow } from '@/db/schema'
import { fetchMessages } from '@/lib/api'
import { buildSegments } from '@/lib/wave-utils'
import { cn } from '@/lib/utils'
import { useAppStore, useChildRunWaveMap, useMessagesForConversation, useTopLevelRunningRuns } from '@/stores/app-store'

const STICKY_BOTTOM_THRESHOLD_PX = 120
const STREAM_SCROLL_THROTTLE_MS = 80

export function MessageList({ conversationId }: { conversationId: string }) {
  const messages = useMessagesForConversation(conversationId)
  const childRunWaveMap = useChildRunWaveMap(conversationId)
  const runningRuns = useTopLevelRunningRuns(conversationId)
  const segments = useMemo(
    () => buildSegments(messages.filter((m) => !m.hidden), childRunWaveMap),
    [messages, childRunWaveMap],
  )
  const setMessagesForConversation = useAppStore((s) => s.setMessagesForConversation)
  const messageIdsByConv = useAppStore((s) => s.messageIdsByConv[conversationId])

  const viewportRef = useRef<HTMLDivElement>(null)
  const scrollFrameRef = useRef<number | null>(null)
  const scrollTimerRef = useRef<number | null>(null)
  const stickToBottomRef = useRef(true)
  const initialScrolledConvRef = useRef<string | null>(null)
  const lastMessageIdRef = useRef<string | null>(null)
  const lastMessage = messages[messages.length - 1]
  const lastMessageId = lastMessage?.id ?? null
  const lastMessageRole = lastMessage?.role ?? null
  const lastMessageStatus = lastMessage?.status ?? null
  const lastMessagePartCount = lastMessage?.parts.length ?? 0
  const lastMessageContentLength = getMessageContentLength(lastMessage)
  const hasMessages = messages.length > 0
  const runningRunsCount = runningRuns.length

  const cancelScheduledScroll = useCallback(() => {
    if (scrollTimerRef.current !== null) {
      window.clearTimeout(scrollTimerRef.current)
      scrollTimerRef.current = null
    }
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
      scrollFrameRef.current = null
    }
  }, [])

  const scheduleScrollToBottom = useCallback(
    (force = false) => {
      if (!force && !stickToBottomRef.current) return
      if (force) cancelScheduledScroll()
      if (scrollTimerRef.current !== null || scrollFrameRef.current !== null) return

      const delay = force ? 0 : STREAM_SCROLL_THROTTLE_MS
      scrollTimerRef.current = window.setTimeout(() => {
        scrollTimerRef.current = null
        scrollFrameRef.current = window.requestAnimationFrame(() => {
          scrollFrameRef.current = null
          const viewport = viewportRef.current
          if (!viewport) return

          viewport.scrollTop = viewport.scrollHeight
          stickToBottomRef.current = true
        })
      }, delay)
    },
    [cancelScheduledScroll],
  )

  useLayoutEffect(() => {
    stickToBottomRef.current = true
    initialScrolledConvRef.current = null
    lastMessageIdRef.current = null
    cancelScheduledScroll()
    return cancelScheduledScroll
  }, [cancelScheduledScroll, conversationId])

  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return

    const updateStickiness = () => {
      stickToBottomRef.current = isNearBottom(viewport)
    }

    viewport.addEventListener('scroll', updateStickiness, { passive: true })
    updateStickiness()
    return () => {
      viewport.removeEventListener('scroll', updateStickiness)
    }
  }, [conversationId, hasMessages])

  useEffect(() => {
    if (messageIdsByConv) return
    let cancelled = false
    fetchMessages(conversationId)
      .then((list) => {
        if (!cancelled) setMessagesForConversation(conversationId, list)
      })
      .catch((err) => {
        console.error('[MessageList] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, messageIdsByConv, setMessagesForConversation])

  useLayoutEffect(() => {
    if (messages.length === 0) return

    const needsInitialScroll = initialScrolledConvRef.current !== conversationId
    const previousLastMessageId = lastMessageIdRef.current
    const isNewUserMessage =
      lastMessageRole === 'user' &&
      previousLastMessageId !== null &&
      previousLastMessageId !== lastMessageId

    if (needsInitialScroll) {
      initialScrolledConvRef.current = conversationId
      scheduleScrollToBottom(true)
    } else if (isNewUserMessage) {
      scheduleScrollToBottom(true)
    } else {
      scheduleScrollToBottom()
    }

    lastMessageIdRef.current = lastMessageId
  }, [
    conversationId,
    lastMessageContentLength,
    lastMessageId,
    lastMessagePartCount,
    lastMessageRole,
    lastMessageStatus,
    messages.length,
    runningRunsCount,
    scheduleScrollToBottom,
  ])

  if (messages.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex size-14 items-center justify-center rounded-2xl bg-muted/60 shadow-[var(--shadow-sm)]">
            <MessagesSquare className="size-6 text-muted-foreground" />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium text-foreground">还没有消息</p>
            <p className="text-xs text-muted-foreground">发一条试试，或 / 查看命令</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <ScrollArea className="min-h-0 flex-1" viewportRef={viewportRef}>
      <div className="mx-auto max-w-3xl px-4 py-6 md:px-6 md:py-8">
        {segments.map((seg, si) => {
          const segMargin = si === 0 ? '' : 'mt-4'

          if (seg.kind === 'single') {
            return (
              <div key={si} className={segMargin}>
                {seg.messages.map((m, mi) => {
                  const grouped = mi > 0 && isGroupedWithPrev(seg.messages[mi - 1], m)
                  return (
                  <div
                    key={m.id}
                    className={cn(mi === 0 && si === 0 ? '' : grouped ? 'mt-1' : 'mt-6')}
                  >
                      <MessageItem message={m} grouped={grouped} />
                    </div>
                  )
                })}
              </div>
            )
          }

          return (
            <div key={si} className={cn(segMargin, 'flex gap-3 max-md:flex-col')}>
              {seg.columns.map((col) => (
                <div key={col.taskId} className="min-w-0 flex-1">
                  <WaveColumnHeader agentId={col.agentId} taskId={col.taskId} />
                  {col.messages.length === 0 ? (
                    <div className="flex items-center justify-center py-4 text-xs text-muted-foreground">
                      <Loader2 className="mr-1 size-3 animate-spin" />
                      等待消息…
                    </div>
                  ) : (
                    col.messages.map((m, mi) => (
                      <div key={m.id} className={cn(mi === 0 ? 'mt-2' : 'mt-1')}>
                        <MessageItem message={m} grouped={true} />
                      </div>
                    ))
                  )}
                </div>
              ))}
            </div>
          )
        })}
        {runningRuns.map((run) => (
          <div key={`indicator-${run.id}`} className="mt-2">
            <AgentWorkingIndicator run={run} conversationId={conversationId} />
          </div>
        ))}
      </div>
    </ScrollArea>
  )
}

function isGroupedWithPrev(prev: MessageRow, curr: MessageRow): boolean {
  return (
    prev.role === 'agent' &&
    curr.role === 'agent' &&
    prev.agentId === curr.agentId &&
    prev.runId === curr.runId &&
    prev.runId !== null
  )
}

function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_BOTTOM_THRESHOLD_PX
}

function getMessageContentLength(message: MessageRow | undefined): number {
  if (!message) return 0
  let length = 0
  const parts = message.parts
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]
    switch (part.type) {
      case 'text':
      case 'thinking':
      case 'code':
        length += part.content.length
        break
      default:
        break
    }
  }
  return length
}
