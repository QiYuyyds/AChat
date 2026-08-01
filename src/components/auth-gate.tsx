'use client'

import { Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { useAppStore } from '@/stores/app-store'
import { hasToken, useAuthStore } from '@/stores/auth-store'

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { isLoading, initialize, user } = useAuthStore()
  const setUserId = useAppStore((s) => s.setUserId)
  const initialized = useRef(false)
  // Gate localStorage access behind mount to avoid SSR/CSR hydration mismatch:
  // hasToken() reads localStorage which returns false on server but may return
  // true on client, causing the server spinner to mismatch the client workspace.
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    if (!initialized.current) {
      initialized.current = true
      void initialize()
    }
  }, [initialize])

  useEffect(() => {
    setUserId(user?.id ?? null)
  }, [user, setUserId])

  if (!mounted || (isLoading && !hasToken())) {
    return (
      <div className="flex h-dvh items-center justify-center bg-background">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return <>{children}</>
}
