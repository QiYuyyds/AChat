'use client'

import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import { MessageItem } from '@/components/message-item'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { MessageRow } from '@/db/schema'
import { fetchMessages } from '@/lib/api'
import { useAppStore, useMessagesForConversation } from '@/stores/app-store'

const STICKY_BOTTOM_THRESHOLD_PX = 120
const STREAM_SCROLL_THROTTLE_MS = 80

export function MessageList({ conversationId }: { conversationId: string }) {
  const messages = useMessagesForConversation(conversationId)
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
    scheduleScrollToBottom,
  ])

  if (messages.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        还没有消息，发一条试试。
      </div>
    )
  }

  return (
    <ScrollArea className="min-h-0 flex-1" viewportRef={viewportRef}>
      <div className="space-y-4 p-4">
        {messages.map((m) => (
          <MessageItem key={m.id} message={m} />
        ))}
      </div>
    </ScrollArea>
  )
}

function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_BOTTOM_THRESHOLD_PX
}

function getMessageContentLength(message: MessageRow | undefined): number {
  if (!message) return 0
  let length = 0
  for (const part of message.parts) {
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
