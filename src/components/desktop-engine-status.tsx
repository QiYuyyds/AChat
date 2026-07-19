'use client'

import { useEffect, useState } from 'react'

import {
  getLocalEngineStatus,
  isDesktopMode,
  restartLocalEngine,
  type DesktopEngineStatus,
} from '@/lib/desktop'

/**
 * Compact engine status chip for desktop shell. Renders nothing on pure web.
 */
export function DesktopEngineStatus() {
  const [status, setStatus] = useState<DesktopEngineStatus | 'web'>('web')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isDesktopMode()) return
    let cancelled = false
    const tick = async () => {
      const next = await getLocalEngineStatus()
      if (!cancelled) setStatus(next)
    }
    void tick()
    const id = window.setInterval(() => void tick(), 4000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  if (!isDesktopMode() || status === 'web') return null

  const label =
    status === 'ready' ? 'Engine ready' : status === 'starting' ? 'Engine starting…' : 'Engine error'

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        className={
          status === 'ready'
            ? 'text-emerald-500'
            : status === 'starting'
              ? 'text-amber-500'
              : 'text-red-500'
        }
      >
        {label}
      </span>
      {status === 'error' && (
        <button
          type="button"
          className="underline disabled:opacity-50"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            void restartLocalEngine()
              .catch(() => {})
              .finally(() => setBusy(false))
          }}
        >
          Restart
        </button>
      )}
    </div>
  )
}
