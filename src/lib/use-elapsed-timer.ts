'use client'

import { useEffect, useState } from 'react'

/**
 * 返回从 startedAt 到现在的毫秒数，每秒更新一次。
 * isActive 为 false 时不启动 interval，返回 null。
 */
export function useElapsedTimer(
  startedAt: number | undefined,
  isActive: boolean,
): number | null {
  const [elapsed, setElapsed] = useState<number | null>(
    startedAt && isActive ? Date.now() - startedAt : null,
  )

  useEffect(() => {
    if (!isActive || startedAt === undefined) {
      setElapsed(null)
      return
    }

    setElapsed(Date.now() - startedAt)

    const interval = setInterval(() => {
      setElapsed(Date.now() - startedAt)
    }, 1000)

    return () => clearInterval(interval)
  }, [startedAt, isActive])

  return elapsed
}
