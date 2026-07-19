'use client'

import { useEffect } from 'react'

import type { StreamEvent } from '@/shared/types'
import { API_BASE_URL } from '@/lib/config'
import { executionBaseUrl, isDesktopMode } from '@/lib/desktop'
import { useAppStore } from '@/stores/app-store'
import { useAuthStore, getAccessToken } from '@/stores/auth-store'

/**
 * StreamProvider — 全局唯一 SSE 连接，把 /api/stream 推过来的事件
 * 转发到 Zustand store。详见 specs/02-stream-events.md §SSE 编码。
 *
 * Desktop: must subscribe to the local engine bus (not official :8000).
 * Bridge injection and JWT handoff can lag auth; wait/retry so we don't
 * permanently lock onto the wrong base URL.
 */

let activeSource: EventSource | null = null
let activeUrl: string | null = null
let refCount = 0

function isLikelyTauriShell(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as Window & {
    __TAURI_INTERNALS__?: unknown
    __TAURI__?: unknown
  }
  return (
    window.achatDesktop?.isDesktop === true ||
    w.__TAURI_INTERNALS__ != null ||
    w.__TAURI__ != null
  )
}

function buildStreamUrl(): string | null {
  const base = executionBaseUrl(API_BASE_URL)
  const token = getAccessToken()
  const params = new URLSearchParams()
  if (token) params.set('token', token)

  if (isDesktopMode()) {
    // Desktop engine requires engineToken; without bridge/token we are not ready.
    if (!token) return null
    const engineToken = window.achatDesktop?.engineToken
    if (!engineToken) return null
    // Must not fall back to official API while bridge is partial.
    if (!window.achatDesktop?.engineBaseUrl) return null
    params.set('engineToken', engineToken)
  }

  const qs = params.toString()
  return qs ? `${base}/api/stream?${qs}` : `${base}/api/stream`
}

function openStream(
  url: string,
  onOpen: () => void,
  onError: () => void,
  onEvent: (event: StreamEvent | { type: 'connected' | 'heartbeat'; timestamp?: number }) => void,
): EventSource {
  const source = new EventSource(url, {
    withCredentials: !isDesktopMode(),
  })

  source.onopen = () => {
    onOpen()
  }

  source.onerror = () => {
    // EventSource auto-reconnects for transient errors.
    onError()
  }

  source.onmessage = (e) => {
    let parsed: unknown
    try {
      parsed = JSON.parse(e.data)
    } catch {
      return
    }
    if (!parsed || typeof parsed !== 'object') return
    const obj = parsed as { type?: string }
    if (obj.type === 'connected' || obj.type === 'heartbeat') {
      onOpen()
      return
    }
    onEvent(parsed as StreamEvent)
  }

  return source
}

export function StreamProvider({ children }: { children: React.ReactNode }) {
  const applyEvent = useAppStore((s) => s.applyEvent)
  const setStreamConnected = useAppStore((s) => s.setStreamConnected)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  useEffect(() => {
    if (!isAuthenticated) return

    refCount++
    let cancelled = false
    let waitTimer: number | null = null
    let watchTimer: number | null = null

    const closeActive = () => {
      if (activeSource) {
        activeSource.close()
        activeSource = null
        activeUrl = null
      }
      setStreamConnected(false)
    }

    const ensureConnected = () => {
      if (cancelled) return

      // Tauri shell: wait for achatDesktop + engineBaseUrl + access token.
      if (isLikelyTauriShell() && !isDesktopMode()) {
        waitTimer = window.setTimeout(ensureConnected, 150)
        return
      }
      if (isDesktopMode() && !getAccessToken()) {
        waitTimer = window.setTimeout(ensureConnected, 150)
        return
      }

      const url = buildStreamUrl()
      if (!url) {
        waitTimer = window.setTimeout(ensureConnected, 150)
        return
      }

      // Already on the correct URL.
      if (activeSource && activeUrl === url) return

      // Reconnect if bridge appeared late and we were on official API.
      closeActive()
      activeUrl = url
      activeSource = openStream(
        url,
        () => {
          if (!cancelled) setStreamConnected(true)
        },
        () => {
          if (!cancelled) setStreamConnected(false)
        },
        (event) => {
          if (!cancelled) applyEvent(event as StreamEvent)
        },
      )
    }

    ensureConnected()

    // Watch for late bridge inject / token refresh while module singleton is open.
    watchTimer = window.setInterval(() => {
      if (cancelled || refCount <= 0) return
      const next = buildStreamUrl()
      if (next && next !== activeUrl) {
        ensureConnected()
      }
    }, 500)

    return () => {
      cancelled = true
      if (waitTimer != null) window.clearTimeout(waitTimer)
      if (watchTimer != null) window.clearInterval(watchTimer)
      refCount--
      if (refCount <= 0) {
        closeActive()
        refCount = 0
      }
    }
  }, [applyEvent, setStreamConnected, isAuthenticated])

  return <>{children}</>
}
