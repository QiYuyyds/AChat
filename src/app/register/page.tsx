'use client'

import { Loader2, UserPlus } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { AuthBackground } from '@/components/auth-background'
import { AuthBrandPanel } from '@/components/auth-brand-panel'
import { AuthLogo } from '@/components/AuthLogo'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

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
          className="relative flex w-full items-center justify-center overflow-hidden p-6 lg:w-2/5"
        >
          <AuthBackground variant="form" />

          <div className="auth-fade-up relative z-10 w-full max-w-[420px]">
            <div className="mb-8 flex flex-col items-center text-center">
              <div className="mb-5 flex size-14 items-center justify-center rounded-2xl bg-muted ring-1 ring-border">
                <UserPlus className="size-7 text-muted-foreground" />
              </div>
              <h1 className="text-2xl font-semibold tracking-tight">注册已关闭</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">管理员已禁用新用户注册</p>
            </div>

            <p className="text-center text-sm text-muted-foreground">
              请联系管理员获取账户
            </p>

            <div className="my-6 flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">已有账户</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            <Link
              href="/login"
              className="block rounded-lg border border-border py-3 text-center text-sm font-medium transition-all duration-200 hover:bg-muted hover:-translate-y-px"
            >
              返回登录
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-dvh">
      <AuthBrandPanel />
      <div className="relative flex w-full items-center justify-center overflow-hidden p-6 lg:w-2/5">
        <AuthBackground variant="form" />

        <div className="auth-fade-up relative z-10 w-full max-w-[420px]">
          {/* Logo + 标题 */}
          <div className="mb-8 flex flex-col items-center text-center">
            <AuthLogo size={56} className="mb-5" />
            <h1 className="text-2xl font-semibold tracking-tight">创建账户</h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              加入 AChat，开启多 Agent 协作
            </p>
          </div>

          {/* 表单 */}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="auth-fade-up-delay-1 flex flex-col gap-2">
              <label
                htmlFor="name"
                className="text-xs font-medium tracking-wide text-muted-foreground"
              >
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
                className="h-11 px-3.5 transition-all duration-200"
              />
            </div>
            <div className="auth-fade-up-delay-1 flex flex-col gap-2">
              <label
                htmlFor="email"
                className="text-xs font-medium tracking-wide text-muted-foreground"
              >
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
                className="h-11 px-3.5 transition-all duration-200"
              />
            </div>
            <div className="auth-fade-up-delay-2 flex flex-col gap-2">
              <label
                htmlFor="password"
                className="text-xs font-medium tracking-wide text-muted-foreground"
              >
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
                className="h-11 px-3.5 transition-all duration-200"
              />
            </div>
            {error && (
              <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <Button
              type="submit"
              disabled={submitting}
              className="auth-fade-up-delay-3 mt-2 h-12 w-full bg-gradient-to-b from-primary to-primary/90 text-sm font-medium tracking-wide transition-all duration-200 hover:shadow-md hover:brightness-110 hover:-translate-y-px active:translate-y-0"
            >
              {submitting ? <Loader2 className="size-4 animate-spin" /> : '注册'}
            </Button>
          </form>

          {/* 分隔线 */}
          <div className="my-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">已有账户</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          {/* 登录链接 — 边框按钮式 */}
          <Link
            href="/login"
            className="block rounded-lg border border-border py-3 text-center text-sm font-medium transition-all duration-200 hover:bg-muted hover:-translate-y-px"
          >
            返回登录
          </Link>
        </div>
      </div>
    </div>
  )
}
