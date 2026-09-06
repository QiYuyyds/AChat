'use client'

import { useEffect } from 'react'

import { authFetch } from '@/lib/api'
import { API_BASE_URL } from '@/lib/config'
import { useAuthStore } from '@/stores/auth-store'

// 心跳间隔（分钟）：环境变量可配，默认 5，下限 1
const HEARTBEAT_MINUTES = (() => {
  const raw = Number.parseInt(process.env.NEXT_PUBLIC_STATS_HEARTBEAT_MINUTES ?? '', 10)
  return Number.isFinite(raw) && raw >= 1 ? raw : 5
})()

function isForegroundActive(): boolean {
  return document.visibilityState === 'visible' && document.hasFocus()
}

/**
 * 前台活跃心跳（usage-stats capability）。
 *
 * 仅在窗口可见且有焦点时按固定间隔上报——后台 / 最小化不计活跃；
 * 页面卸载不打扰（不补发、无 unload beacon）。web 直达云端；桌面到达
 * 本地后端聚合进队列随批量上报，前端两种形态行为一致。静默失败：
 * 统计绝不干扰应用。
 */
export function StatsHeartbeat() {
  const user = useAuthStore((s) => s.user)

  useEffect(() => {
    if (!user) return

    let timer: number | undefined
    const send = () => {
      if (!isForegroundActive()) return
      void authFetch(`${API_BASE_URL}/api/stats/heartbeat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intervalMinutes: HEARTBEAT_MINUTES }),
      }).catch(() => undefined)
    }
    const schedule = () => {
      if (timer !== undefined) window.clearInterval(timer)
      timer = window.setInterval(send, HEARTBEAT_MINUTES * 60_000)
    }
    const onVisibilityChange = () => {
      // 回前台时重置计时相位：隐藏期间错过的心跳不补发，
      // 恢复后的下一次心跳距此刻一个完整活跃间隔
      if (document.visibilityState === 'visible') schedule()
    }

    schedule()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      if (timer !== undefined) window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [user])

  return null
}
