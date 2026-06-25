'use client'

import { useEffect } from 'react'

import { useSearchStore } from '@/stores/search-store'
import { useAppStore } from '@/stores/app-store'

/**
 * Listens to searchStore.highlightedMessageId. When set, scrolls the message
 * into view and applies a 2s highlight class. Auto-clears via the store timer.
 *
 * Also handles "pending jump" — when a search hit is clicked, the store
 * records which conversation to switch to. This layer consumes that and
 * calls setActiveConversation.
 */
export function MessageHighlightLayer() {
  const highlightedId = useSearchStore((s) => s.highlightedMessageId)
  const setActive = useAppStore((s) => s.setActiveConversation)
  const pendingConv = useSearchStore((s) => s.pendingJumpConversationId)
  const consume = useSearchStore((s) => s.consumePendingJump)

  // Step 1: switch to the conversation if needed
  useEffect(() => {
    if (pendingConv) {
      setActive(pendingConv)
      consume()
    }
  }, [pendingConv, setActive, consume])

  // Step 2: scroll + flash when a message id is set
  useEffect(() => {
    if (!highlightedId) return
    // Wait for next paint so the message is rendered
    const t = requestAnimationFrame(() => {
      const el = document.getElementById(`message-${highlightedId}`)
      if (!el) return
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('search-highlight-flash')
      setTimeout(() => el.classList.remove('search-highlight-flash'), 2000)
    })
    return () => cancelAnimationFrame(t)
  }, [highlightedId])

  return null
}