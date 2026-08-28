'use client'

import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'

import { API_BASE_URL } from '@/lib/config'
import { authFetch } from '@/lib/api'
import type { SessionNote, SessionNoteResponse } from '@/shared/session-note'

interface SessionNoteState {
  note: SessionNote | null
  coversUpTo: number | null
  loading: boolean
  error: string | null

  fetchNote: (conversationId: string) => Promise<void>
  clear: () => void
}

export const useSessionNoteStore = create<SessionNoteState>()(
  immer((set) => ({
    note: null,
    coversUpTo: null,
    loading: false,
    error: null,

    fetchNote: async (conversationId: string) => {
      set((s) => { s.loading = true; s.error = null })
      try {
        const res = await authFetch(
          `${API_BASE_URL}/api/conversations/${conversationId}/session-note`,
        )
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}: ${res.statusText}`)
        }
        const body: SessionNoteResponse = await res.json()
        set((s) => {
          s.note = body.note
          s.coversUpTo = body.coversUpTo
          s.loading = false
        })
      } catch (err) {
        set((s) => {
          s.note = null
          s.coversUpTo = null
          s.loading = false
          s.error = err instanceof Error ? err.message : 'Failed to load session note'
        })
      }
    },

    clear: () => {
      set((s) => {
        s.note = null
        s.coversUpTo = null
        s.loading = false
        s.error = null
      })
    },
  })),
)
