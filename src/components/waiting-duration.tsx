'use client'

import { Clock } from 'lucide-react'

import { useElapsedTimer } from '@/lib/use-elapsed-timer'

/** 已等待时长的展示文案：<1min 显示秒，否则显示整分。 */
export function formatWaitingLabel(elapsedMs: number): string {
  const totalSec = Math.max(0, Math.floor(elapsedMs / 1000))
  return totalSec < 60 ? `${totalSec} 秒` : `${Math.floor(totalSec / 60)} 分`
}

/**
 * WaitingDuration —— 审批卡片 waiting 态的「已等待 Xm」计时。
 *
 * 仅展示已等待时长，不显示倒计时（超时值可被 env 配置覆盖，倒计时会误导）。
 */
export function WaitingDuration({
  startedAt,
  className = 'inline-flex items-center gap-0.5',
}: {
  startedAt: number
  className?: string
}) {
  const elapsed = useElapsedTimer(startedAt, true)
  if (elapsed === null) return null
  return (
    <span className={className}>
      <Clock className="size-2.5" />
      已等待 {formatWaitingLabel(elapsed)}
    </span>
  )
}
