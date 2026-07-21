'use client'

import { useEffect } from 'react'
import { useAppStore } from '@/stores/app-store'

/**
 * 当 guide agent 执行管理操作后，后端发 guide_side_effect 事件 →
 * store 更新 guideRefreshTargets[target] = timestamp。
 * 面板组件用此 hook 监听 target 变化，自动 re-fetch。
 */
export function useGuideSideEffectRefresh(
  target: string,
  onRefresh: () => void,
) {
  const timestamp = useAppStore((s) => s.guideRefreshTargets[target])

  useEffect(() => {
    if (timestamp) onRefresh()
  }, [timestamp]) // eslint-disable-line react-hooks/exhaustive-deps
}
