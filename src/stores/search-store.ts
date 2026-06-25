'use client'

import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'

import { searchMessagesApi } from '@/lib/api'
import type { SearchHit } from '@/shared/types'

const DEBOUNCE_MS = 200
const CHINESE_RE = /\p{Script=Han}/gu

function chineseCharCount(s: string): number {
  return (s.match(CHINESE_RE) ?? []).length
}

interface SearchState {
  isOpen: boolean
  query: string
  hits: SearchHit[]
  total: number
  loading: boolean
  error: string | null
  highlightedMessageId: string | null
  /** Conversation that should be active after a jump (consumed by ChatWindow). */
  pendingJumpConversationId: string | null

  openSearch: () => void
  closeSearch: () => void
  setQuery: (q: string) => void
  runSearch: () => Promise<void>
  jumpToHit: (hit: SearchHit) => void
  consumePendingJump: () => string | null
  clearHighlight: () => void
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null

export const useSearchStore = create<SearchState>()(
  immer((set, get) => ({
    isOpen: false,
    query: '',
    hits: [],
    total: 0,
    loading: false,
    error: null,
    highlightedMessageId: null,
    pendingJumpConversationId: null,

    openSearch: () => set((s) => { s.isOpen = true }),
    closeSearch: () => set((s) => { s.isOpen = false }),

    setQuery: (q) => {
      set((s) => { s.query = q })
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        void get().runSearch()
      }, DEBOUNCE_MS)
    },

    runSearch: async () => {
      const q = get().query.trim()
      if (q.length < 2) {
        set((s) => { s.hits = []; s.total = 0; s.loading = false; s.error = null })
        return
      }
      const fallback = chineseCharCount(q) < 3 ? 'like' as const : undefined
      set((s) => { s.loading = true; s.error = null })
      try {
        const r = await searchMessagesApi(q, { fallback })
        set((s) => { s.hits = r.hits; s.total = r.total; s.loading = false })
      } catch (err) {
        set((s) => {
          s.hits = []
          s.total = 0
          s.loading = false
          s.error = err instanceof Error ? err.message : 'Search failed'
        })
      }
    },

    jumpToHit: (hit) => {
      set((s) => {
        s.isOpen = false
        s.pendingJumpConversationId = hit.conversationId
        s.highlightedMessageId = hit.messageId
      })
      setTimeout(() => {
        useSearchStore.getState().clearHighlight()
      }, 2000)
    },

    consumePendingJump: () => {
      const id = get().pendingJumpConversationId
      if (id) set((s) => { s.pendingJumpConversationId = null })
      return id
    },

    clearHighlight: () => set((s) => { s.highlightedMessageId = null }),
  })),
)