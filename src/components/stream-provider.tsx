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
 *
 * 断线恢复：每帧的 SSE `id:`（seq）随事件传入 store 水位线做重放去重；
 * 浏览器自动重连时回传 Last-Event-ID，服务端从重放缓冲补发缺口。若重连后
 * 宽限期内没有 seq 前进（服务端无法重放，含浏览器不回传 Last-Event-ID 的
 * 环境），主动触发一次活跃会话 REST 对账兜底。
 */

// 重连后判定「没有重放到达」的宽限期：重放帧紧随握手到达，1s 足够。
const RECONCILE_GRACE_MS = 1000

let activeSource: EventSource | null = null
let refCount = 0

/** SSE 帧的 id 字段 → seq 数字；无 id 或非数字时返回 undefined（不去重）。 */
function parseSeq(lastEventId: string): number | undefined {
  if (!lastEventId) return undefined
  const n = Number(lastEventId)
  return Number.isFinite(n) && n > 0 ? n : undefined
}

export function StreamProvider({ children }: { children: React.ReactNode }) {
  const applyEvent = useAppStore((s) => s.applyEvent)
  const setStreamConnected = useAppStore((s) => s.setStreamConnected)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  const pendingRef = useRef<{ event: StreamEvent; seq?: number }[]>([])
  const rafRef = useRef<number | null>(null)
  const hadDisconnectRef = useRef(false)
  const reconnectCheckRef = useRef<number | null>(null)

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
        for (const { event, seq } of events) {
          applyEvent(event, seq)
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
      for (const { event, seq } of events) {
        applyEvent(event, seq)
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
        if (!hadDisconnectRef.current) return
        hadDisconnectRef.current = false
        // 自动重连成功：正常情况下服务端按 Last-Event-ID 重放缺口（seq 前进）。
        // 宽限期内水位线未动 → 没有重放到达（服务端无法重放，或浏览器没回传
        // Last-Event-ID），主动对账兜底。若期间收到 resync，其对账已在途，水位线
        // 归零 ≠ 打开时的值，此处自然跳过。
        const watermarkAtOpen = useAppStore.getState().streamSeqWatermark
        if (reconnectCheckRef.current !== null) {
          window.clearTimeout(reconnectCheckRef.current)
        }
        reconnectCheckRef.current = window.setTimeout(() => {
          reconnectCheckRef.current = null
          if (useAppStore.getState().streamSeqWatermark === watermarkAtOpen) {
            void useAppStore.getState().reconcileActiveConversation()
          }
        }, RECONCILE_GRACE_MS)
      }

      activeSource.onerror = () => {
        // EventSource 会自动重连，无需我们做事
        hadDisconnectRef.current = true
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

        // All other events: batch via rAF to reduce React render passes.
        // seq 来自帧 id（重连重放时靠 store 水位线幂等去重）。
        pendingRef.current.push({ event: parsed as StreamEvent, seq: parseSeq(e.lastEventId) })
        scheduleFlush()
      }
    }

    return () => {
      refCount--
      // 全部组件都卸载时关闭，避免 dev 模式 StrictMode 双 mount 反复断开
      if (refCount <= 0) {
        // Cancel pending rAF and flush remaining events synchronously to avoid loss
        flushNow()
        if (reconnectCheckRef.current !== null) {
          window.clearTimeout(reconnectCheckRef.current)
          reconnectCheckRef.current = null
        }
        activeSource?.close()
        activeSource = null
        refCount = 0
        hadDisconnectRef.current = false
        setStreamConnected(false)
      }
    }
  }, [applyEvent, setStreamConnected, isAuthenticated])

  return <>{children}</>
}
