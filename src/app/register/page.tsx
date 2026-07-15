'use client'

import { Loader2, UserPlus } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { AuthBrandPanel } from '@/components/auth-brand-panel'
import { ParticleBackground } from '@/components/particle-background'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

const formAreaBgStyle = {
  background:
    'radial-gradient(ellipse 80% 60% at 50% 0%, var(--primary) 3%, transparent 60%),' +
    'radial-gradient(ellipse 60% 50% at 80% 80%, var(--warning) 2%, transparent 50%),' +
    'var(--background)',
} as const

const cardStyle = { boxShadow: 'var(--shadow-md), var(--inset-hi)' } as const

const cardClassName =
  'auth-fade-up relative z-10 w-full max-w-sm border-border/50 bg-card/80 backdrop-blur-sm'

const authOrbs = (
  <>
    <ParticleBackground />
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
  </>
)

export default function RegisterPage() {
  const router = useRouter()
  const register = useAuthStore((s) => s.register)
  const allowRegistration = useAuthStore((s) => s.config.allowRegistration)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 6) {
      setError('密码至少需要 6 个字符')
      return
    }
    setSubmitting(true)
    try {
      await register(email, name, password)
      router.replace('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (!allowRegistration) {
    return (
      <div className="flex h-dvh">
        <AuthBrandPanel />
        <div
          className="relative flex w-full items-center justify-center overflow-hidden p-4 lg:w-2/5"
          style={formAreaBgStyle}
        >
          {authOrbs}
          <Card className={cardClassName} style={cardStyle}>
            <CardHeader className="items-center text-center">
              <div className="mx-auto mb-2 flex size-14 items-center justify-center rounded-full bg-muted ring-1 ring-border">
                <UserPlus className="size-7 text-muted-foreground" />
              </div>
              <CardTitle className="text-xl">注册已关闭</CardTitle>
              <CardDescription>管理员已禁用新用户注册</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-center text-sm text-muted-foreground">
                请联系管理员获取账户，或{' '}
                <Link href="/login" className="font-medium text-primary hover:underline">
                  返回登录
                </Link>
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-dvh">
      <AuthBrandPanel />
      <div
        className="relative flex w-full items-center justify-center overflow-hidden p-4 lg:w-2/5"
        style={formAreaBgStyle}
      >
        {authOrbs}
        <Card className={cardClassName} style={cardStyle}>
          <CardHeader className="items-center text-center">
            <img src="/favicon.ico" alt="AChat" className="mx-auto size-14" />
            <CardTitle className="text-xl">创建账户</CardTitle>
            <CardDescription>注册一个新的 AChat 账户</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="name" className="text-sm font-medium">
                  用户名
                </label>
                <Input
                  id="name"
                  type="text"
                  placeholder="你的名字"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  autoComplete="name"
                  autoFocus
                />
              </div>
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
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="password" className="text-sm font-medium">
                  密码
                </label>
                <Input
                  id="password"
                  type="password"
                  placeholder="至少 6 个字符"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="new-password"
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
                {submitting ? <Loader2 className="size-4 animate-spin" /> : '注册'}
              </Button>
            </form>
            <p className="mt-4 text-center text-sm text-muted-foreground">
              已有账户？{' '}
              <Link href="/login" className="font-medium text-primary hover:underline">
                登录
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
