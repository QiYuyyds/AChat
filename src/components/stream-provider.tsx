'use client'

import { useEffect, useRef } from 'react'

import type { StreamEvent } from '@/shared/types'
import { getApiBaseUrl } from '@/lib/config'
import {
  ENGINE_TOKEN_HEADER,
  alignLoopbackHost,
  executionBaseUrl,
  isDesktopMode,
} from '@/lib/desktop'
import { useAppStore } from '@/stores/app-store'
import { useAuthStore, getAccessToken } from '@/stores/auth-store'

/**
 * StreamProvider — 全局唯一 SSE 连接，把 /api/stream 推过来的事件
 * 转发到 Zustand store。详见 specs/02-stream-events.md §SSE 编码。
 *
 * Desktop (Tauri):
 * - 必须连本机引擎（不要走 Next rewrite → :8000，那是另一个进程的 EventBus）
 * - EventSource 不能设自定义 header，但引擎 middleware 接受 ?engineToken=
 * - 同时带 Authorization 的 fetch 流作为兜底（部分 WebView EventSource 不稳）
 */

type StreamHandlers = {
  onOpen: () => void
  onError: () => void
  onEvent: (event: StreamEvent | { type: 'connected' | 'heartbeat'; timestamp?: number }) => void
}

type StreamHandle = {
  /** Stable identity (engine base + token presence), not full JWT URL */
  key: string
  close: () => void
}

let activeHandle: StreamHandle | null = null
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

function desktopLike(): boolean {
  return isDesktopMode() || isLikelyTauriShell()
}

/** Prefer the page hostname so localhost UI does not hit 127.0.0.1 (cross-origin). */
function normalizeEngineBase(base: string): string {
  return alignLoopbackHost(base)
}

function connectionKey(): string | null {
  if (desktopLike()) {
    const bridge = window.achatDesktop
    if (!bridge?.engineBaseUrl || !bridge.engineToken) return null
    if (!getAccessToken()) return null
    return `desktop|${normalizeEngineBase(bridge.engineBaseUrl)}|${bridge.engineToken}`
  }
  const base = executionBaseUrl(getApiBaseUrl()) || 'same-origin'
  const token = getAccessToken()
  return `web|${base}|${token ? 'tok' : 'notok'}`
}

function buildStreamUrl(): string | null {
  if (desktopLike()) {
    const bridge = window.achatDesktop
    if (!bridge?.engineBaseUrl || !bridge.engineToken) return null
    const token = getAccessToken()
    if (!token) return null
    const root = normalizeEngineBase(bridge.engineBaseUrl)
    const params = new URLSearchParams({
      token,
      engineToken: bridge.engineToken,
    })
    return `${root}/api/stream?${params}`
  }

  const base = executionBaseUrl(getApiBaseUrl())
  const token = getAccessToken()
  const params = new URLSearchParams()
  if (token) params.set('token', token)
  const qs = params.toString()
  const root = base || ''
  return qs ? `${root}/api/stream?${qs}` : `${root}/api/stream`
}

function dispatchData(
  raw: string,
  handlers: Pick<StreamHandlers, 'onOpen' | 'onEvent'>,
): void {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return
  }
  if (!parsed || typeof parsed !== 'object') return
  const obj = parsed as { type?: string }
  if (obj.type === 'connected' || obj.type === 'heartbeat') {
    handlers.onOpen()
    return
  }
  handlers.onEvent(parsed as StreamEvent)
}

function parseSseBuffer(
  buffer: string,
  handlers: Pick<StreamHandlers, 'onOpen' | 'onEvent'>,
): string {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const frame of parts) {
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }
    if (dataLines.length === 0) continue
    dispatchData(dataLines.join('\n'), handlers)
  }
  return rest
}

/**
 * Primary desktop transport: EventSource with ?token=&engineToken=
 * (engine middleware accepts query engine token; verified end-to-end).
 */
function openEventSourceStream(url: string, key: string, handlers: StreamHandlers): StreamHandle {
  // Desktop is cross-origin to the engine — cookies are useless; omit credentials.
  const source = new EventSource(url, {
    withCredentials: !desktopLike(),
  })

  let openedOnce = false

  source.onopen = () => {
    openedOnce = true
    handlers.onOpen()
  }
  source.onerror = () => {
    // EventSource auto-reconnects; only mark disconnected if we never opened
    // or readyState is CLOSED (fatal).
    if (source.readyState === EventSource.CLOSED) {
      handlers.onError()
    } else if (!openedOnce) {
      handlers.onError()
    } else {
      // CONNECTING — temporary blip; keep badge green if we already connected.
    }
  }
  source.onmessage = (e) => {
    dispatchData(e.data, handlers)
  }

  return {
    key,
    close: () => {
      source.onopen = null
      source.onerror = null
      source.onmessage = null
      source.close()
    },
  }
}

/**
 * Fallback: fetch + ReadableStream with Authorization / X-Engine-Token headers.
 * Some WebViews buffer EventSource poorly; fetch is more controllable.
 */
function openFetchStream(urlBuilder: () => string | null, key: string, handlers: StreamHandlers): StreamHandle {
  const ac = new AbortController()
  let closed = false

  const run = async () => {
    while (!closed && !ac.signal.aborted) {
      const url = urlBuilder()
      if (!url) {
        handlers.onError()
        await sleep(800)
        continue
      }
      try {
        const headers: Record<string, string> = {
          Accept: 'text/event-stream',
          'Cache-Control': 'no-cache',
        }
        const token = getAccessToken()
        if (token) headers.Authorization = `Bearer ${token}`
        const engineToken = window.achatDesktop?.engineToken
        if (engineToken) headers[ENGINE_TOKEN_HEADER] = engineToken

        const res = await fetch(url, {
          method: 'GET',
          headers,
          credentials: 'omit',
          signal: ac.signal,
          cache: 'no-store',
        })

        if (!res.ok) {
          if (process.env.NODE_ENV === 'development') {
            console.warn('[StreamProvider] fetch-sse HTTP', res.status)
          }
          handlers.onError()
          await sleep(1500)
          continue
        }

        // Some environments expose body; if not, treat as hard failure and let
        // EventSource path take over on next ensureConnected cycle.
        if (!res.body) {
          if (process.env.NODE_ENV === 'development') {
            console.warn('[StreamProvider] fetch-sse missing body — WebView may not stream fetch')
          }
          handlers.onError()
          break
        }

        handlers.onOpen()
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (!closed && !ac.signal.aborted) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          buffer = parseSseBuffer(buffer, handlers)
        }

        if (!closed && !ac.signal.aborted) {
          handlers.onError()
          await sleep(1200)
        }
      } catch (err) {
        if (closed || ac.signal.aborted) break
        if (process.env.NODE_ENV === 'development') {
          console.warn('[StreamProvider] fetch-sse error', err)
        }
        handlers.onError()
        await sleep(1500)
      }
    }
  }

  void run()

  return {
    key,
    close: () => {
      closed = true
      ac.abort()
    },
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => {
    window.setTimeout(r, ms)
  })
}

export function StreamProvider({ children }: { children: React.ReactNode }) {
  const applyEvent = useAppStore((s) => s.applyEvent)
  const setStreamConnected = useAppStore((s) => s.setStreamConnected)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  // Prefer EventSource on desktop first; flip to fetch after hard EventSource failure.
  const preferFetchRef = useRef(false)

  useEffect(() => {
    if (!isAuthenticated) {
      // Logged out: drop any stale connection.
      if (activeHandle) {
        activeHandle.close()
        activeHandle = null
      }
      setStreamConnected(false)
      return
    }

    refCount++
    let cancelled = false
    let waitTimer: number | null = null
    let watchTimer: number | null = null
    let esFailTimer: number | null = null

    const handlers: StreamHandlers = {
      onOpen: () => {
        if (!cancelled) setStreamConnected(true)
      },
      onError: () => {
        if (!cancelled) setStreamConnected(false)
      },
      onEvent: (event) => {
        if (!cancelled) applyEvent(event as StreamEvent)
      },
    }

    const closeActive = (markDisconnected: boolean) => {
      if (activeHandle) {
        activeHandle.close()
        activeHandle = null
      }
      if (markDisconnected && !cancelled) setStreamConnected(false)
    }

    const ensureConnected = () => {
      if (cancelled) return

      if (desktopLike()) {
        if (!window.achatDesktop?.engineBaseUrl || !window.achatDesktop?.engineToken) {
          waitTimer = window.setTimeout(ensureConnected, 150)
          return
        }
        if (!getAccessToken()) {
          waitTimer = window.setTimeout(ensureConnected, 400)
          return
        }
      }

      const key = connectionKey()
      const url = buildStreamUrl()
      if (!key || !url) {
        waitTimer = window.setTimeout(ensureConnected, 150)
        return
      }

      // Same logical connection already open — do not thrash (token query may change).
      if (activeHandle && activeHandle.key === key) return

      closeActive(false)
      if (process.env.NODE_ENV === 'development') {
        console.info(
          '[StreamProvider] connecting',
          url.replace(/token=[^&]+/g, 'token=***').replace(/engineToken=[^&]+/g, 'engineToken=***'),
          desktopLike()
            ? preferFetchRef.current
              ? '(fetch-sse)'
              : '(event-source)'
            : '(event-source)',
        )
      }

      if (desktopLike() && preferFetchRef.current) {
        activeHandle = openFetchStream(buildStreamUrl, key, handlers)
      } else {
        activeHandle = openEventSourceStream(url, key, handlers)
        // If EventSource never opens within 4s on desktop, switch to fetch.
        if (desktopLike()) {
          if (esFailTimer != null) window.clearTimeout(esFailTimer)
          esFailTimer = window.setTimeout(() => {
            if (cancelled) return
            const connected = useAppStore.getState().streamConnected
            if (!connected) {
              if (process.env.NODE_ENV === 'development') {
                console.warn('[StreamProvider] EventSource slow/failed — switching to fetch-sse')
              }
              preferFetchRef.current = true
              closeActive(false)
              ensureConnected()
            }
          }, 4000)
        }
      }
    }

    ensureConnected()

    watchTimer = window.setInterval(() => {
      if (cancelled || refCount <= 0) return
      const key = connectionKey()
      if (key && (!activeHandle || activeHandle.key !== key)) {
        ensureConnected()
      }
    }, 1500)

    const onBridgeReady = () => {
      // Only reconnect when engine identity actually changed; ignore no-op reinjects.
      if (cancelled) return
      const key = connectionKey()
      if (key && activeHandle && activeHandle.key === key) return
      ensureConnected()
    }
    window.addEventListener('achat-desktop-ready', onBridgeReady)

    // Visibility resume: some WebViews pause EventSource in background.
    const onVis = () => {
      if (document.visibilityState === 'visible' && !cancelled) {
        const connected = useAppStore.getState().streamConnected
        if (!connected) ensureConnected()
      }
    }
    document.addEventListener('visibilitychange', onVis)

    return () => {
      cancelled = true
      window.removeEventListener('achat-desktop-ready', onBridgeReady)
      document.removeEventListener('visibilitychange', onVis)
      if (waitTimer != null) window.clearTimeout(waitTimer)
      if (watchTimer != null) window.clearInterval(watchTimer)
      if (esFailTimer != null) window.clearTimeout(esFailTimer)
      refCount--
      if (refCount <= 0) {
        closeActive(true)
        refCount = 0
      }
    }
  }, [applyEvent, setStreamConnected, isAuthenticated])

  return <>{children}</>
}
