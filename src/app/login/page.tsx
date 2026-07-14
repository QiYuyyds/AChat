'use client'

import { Loader2 } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { AuthBrandPanel } from '@/components/auth-brand-panel'
import { ParticleBackground } from '@/components/particle-background'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

export default function LoginPage() {
  const router = useRouter()
  const login = useAuthStore((s) => s.login)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      router.replace('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-dvh">
      <AuthBrandPanel />

      <div
        className="relative flex w-full items-center justify-center overflow-hidden p-4 lg:w-2/5"
        style={{
          background:
            'radial-gradient(ellipse 80% 60% at 50% 0%, var(--primary) 3%, transparent 60%),' +
            'radial-gradient(ellipse 60% 50% at 80% 80%, var(--warning) 2%, transparent 50%),' +
            'var(--background)',
        }}
      >
        {/* 方块粒子背景 — 鼠标移动时散开 */}
        <ParticleBackground />

        {/* 浮动光球 */}
        <div
          className="auth-orb pointer-events-none absolute size-32 rounded-full"
          style={{
            top: '10%',
            left: '15%',
            background: 'radial-gradient(circle, var(--primary) 0%, transparent 70%)',
            opacity: 0.08,
          }}
        />
        <div
          className="auth-orb pointer-events-none absolute size-24 rounded-full"
          style={{
            bottom: '15%',
            right: '10%',
            background: 'radial-gradient(circle, var(--warning) 0%, transparent 70%)',
            opacity: 0.06,
            animationDelay: '3s',
          }}
        />

        <Card
          className="auth-fade-up relative z-10 w-full max-w-sm border-border/50 bg-card/80 backdrop-blur-sm"
          style={{ boxShadow: 'var(--shadow-md), var(--inset-hi)' }}
        >
          <CardHeader className="items-center text-center">
            <img src="/favicon.ico" alt="AChat" className="mx-auto size-14" />
            <CardTitle className="text-xl">欢迎回来</CardTitle>
            <CardDescription>登录你的 AChat 账户</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="email" className="text-sm font-medium">
                  邮箱
                </label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  autoFocus
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="password" className="text-sm font-medium">
                  密码
                </label>
                <Input
                  id="password"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                />
              </div>
              {error && (
                <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </p>
              )}
              <Button
                type="submit"
                size="lg"
                disabled={submitting}
                className="mt-1 w-full bg-gradient-to-b from-primary to-primary/90 hover:brightness-110"
              >
                {submitting ? <Loader2 className="size-4 animate-spin" /> : '登录'}
              </Button>
            </form>
            <p className="mt-4 text-center text-sm text-muted-foreground">
              还没有账户？{' '}
              <Link href="/register" className="font-medium text-primary hover:underline">
                注册
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
