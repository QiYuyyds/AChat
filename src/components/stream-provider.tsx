'use client'

import { useEffect } from 'react'

import type { StreamEvent } from '@/shared/types'
import { API_BASE_URL } from '@/lib/config'
import { useAppStore } from '@/stores/app-store'

/**
 * StreamProvider — 全局唯一 SSE 连接，把 /api/stream 推过来的事件
 * 转发到 Zustand store。详见 specs/02-stream-events.md §SSE 编码。
 *
 * 在 layout.tsx 中挂载一次。React StrictMode 在 dev 下会双 mount，
 * 这里用 module 级 ref 防止重复连接。
 */

let activeSource: EventSource | null = null
let refCount = 0

export function StreamProvider({ children }: { children: React.ReactNode }) {
  const applyEvent = useAppStore((s) => s.applyEvent)
  const setStreamConnected = useAppStore((s) => s.setStreamConnected)

  useEffect(() => {
    refCount++

    if (!activeSource) {
      activeSource = new EventSource(`${API_BASE_URL}/api/stream`)

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
        if (obj.type === 'connected') {
          setStreamConnected(true)
          return
        }

        applyEvent(parsed as StreamEvent)
      }
    }

    return () => {
      refCount--
      // 全部组件都卸载时关闭，避免 dev 模式 StrictMode 双 mount 反复断开
      if (refCount <= 0) {
        activeSource?.close()
        activeSource = null
        refCount = 0
        setStreamConnected(false)
      }
    }
  }, [applyEvent, setStreamConnected])

  return <>{children}</>
}
