'use client'

import { useEffect, useRef } from 'react'

import type { StreamEvent } from '@/shared/types'
import { API_BASE_URL } from '@/lib/config'
import { useAppStore } from '@/stores/app-store'
import { useAuthStore, getAccessToken } from '@/stores/auth-store'

/**
 * StreamProvider — 全局唯一 SSE 连接，把 /api/stream 推过来的事件
 * 转发到 Zustand store。详见 specs/02-stream-events.md §SSE 编码。
 *
 * 在 layout.tsx 中挂载一次。React StrictMode 在 dev 下会双 mount，
 * 这里用 module 级 ref 防止重复连接。
 *
 * 仅在已认证时建立 SSE 连接（AuthGate 保证此组件只在认证后才渲染）。
 * 跨域 dev 模式下通过 ?token= 传递 JWT（EventSource 无法设置 header）。
 *
 * rAF 批处理：同一动画帧内到达的多条 SSE 事件合并为一次 applyEvent flush，
 * 减少 Zustand set 调用次数和 React 渲染轮次。heartbeat / connected 元事件
 * 立即处理，不入队（它们不影响渲染，且需要即时反映连接状态）。
 */

let activeSource: EventSource | null = null
let refCount = 0

export function StreamProvider({ children }: { children: React.ReactNode }) {
  const applyEvent = useAppStore((s) => s.applyEvent)
  const setStreamConnected = useAppStore((s) => s.setStreamConnected)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  const pendingRef = useRef<StreamEvent[]>([])
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (!isAuthenticated) return

    // Schedule a single rAF to drain all pending events in one flush.
    // Multiple SSE events within the same animation frame are coalesced
    // into a single applyEvent loop, reducing React render passes.
    const scheduleFlush = () => {
      if (rafRef.current !== null) return
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        const events = pendingRef.current
        pendingRef.current = []
        for (const e of events) {
          applyEvent(e)
        }
      })
    }

    // Synchronously flush pending events (used on unmount before closing EventSource).
    const flushNow = () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      const events = pendingRef.current
      pendingRef.current = []
      for (const e of events) {
        applyEvent(e)
      }
    }

    refCount++

    if (!activeSource) {
      // For cross-origin dev, pass token via query param (EventSource can't set headers)
      const token = getAccessToken()
      const url = token
        ? `${API_BASE_URL}/api/stream?token=${encodeURIComponent(token)}`
        : `${API_BASE_URL}/api/stream`

      activeSource = new EventSource(url, { withCredentials: true })

      activeSource.onopen = () => {
        setStreamConnected(true)
      }

      activeSource.onerror = () => {
        // EventSource 会自动重连，无需我们做事
        setStreamConnected(false)
      }

      activeSource.onmessage = (e) => {
        let parsed: unknown
        try {
          parsed = JSON.parse(e.data)
        } catch {
          return
        }
        if (!parsed || typeof parsed !== 'object') return

        const obj = parsed as { type?: string }

        // Meta events: apply immediately, bypass rAF queue
        // (they don't affect rendering and must reflect connection state without delay)
        if (obj.type === 'connected' || obj.type === 'heartbeat') {
          setStreamConnected(true)
          return
        }

        // All other events: batch via rAF to reduce React render passes
        pendingRef.current.push(parsed as StreamEvent)
        scheduleFlush()
      }
    }

    return () => {
      refCount--
      // 全部组件都卸载时关闭，避免 dev 模式 StrictMode 双 mount 反复断开
      if (refCount <= 0) {
        // Cancel pending rAF and flush remaining events synchronously to avoid loss
        flushNow()
        activeSource?.close()
        activeSource = null
        refCount = 0
        setStreamConnected(false)
      }
    }
  }, [applyEvent, setStreamConnected, isAuthenticated])

  return <>{children}</>
}
