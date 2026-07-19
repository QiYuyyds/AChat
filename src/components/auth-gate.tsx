'use client'

import { Loader2 } from 'lucide-react'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useRef } from 'react'

import { useAuthStore } from '@/stores/auth-store'
import { useAppStore } from '@/stores/app-store'

const PUBLIC_ROUTES = ['/login', '/register']

export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const { isLoading, isAuthenticated, initialize, user } = useAuthStore()
  const setUserId = useAppStore((s) => s.setUserId)
  const initialized = useRef(false)
  const lastRedirect = useRef<string | null>(null)

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      void initialize()
    }
  }, [initialize])

  const isPublicRoute = PUBLIC_ROUTES.includes(pathname)

  useEffect(() => {
    setUserId(user?.id ?? null)
  }, [user, setUserId])

  useEffect(() => {
    if (isLoading) return

    let target: string | null = null
    if (!isAuthenticated && !isPublicRoute) {
      target = '/login'
    } else if (isAuthenticated && isPublicRoute) {
      target = '/'
    }

    // Avoid hammering router.replace on every render / unstable router identity.
    // Also ignore when we just redirected to the same target (Strict Mode / remount).
    if (!target || target === pathname || lastRedirect.current === target) {
      if (target === null) lastRedirect.current = null
      return
    }
    lastRedirect.current = target
    // Use replace with scroll:false to reduce visible "full refresh" flash.
    router.replace(target, { scroll: false })
  }, [isLoading, isAuthenticated, isPublicRoute, pathname, router])

  // Loading / redirecting: always paint a solid background so WebView is never blank.
  if (isLoading || (!isAuthenticated && !isPublicRoute) || (isAuthenticated && isPublicRoute)) {
    return (
      <div className="flex h-dvh items-center justify-center bg-background text-foreground">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return <>{children}</>
}
