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

    if (!isAuthenticated && !isPublicRoute) {
      router.replace('/login')
    } else if (isAuthenticated && isPublicRoute) {
      router.replace('/')
    }
  }, [isLoading, isAuthenticated, isPublicRoute, router])

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
