'use client'

import { Crown, FileCode, Loader2, Network, Workflow } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { AuthBackground } from '@/components/auth-background'
import { AuthBrandPanel } from '@/components/auth-brand-panel'
import { AuthLogo } from '@/components/AuthLogo'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

const inputClass =
  'h-11 px-3.5 bg-muted/40 border-border text-foreground placeholder:text-muted-foreground focus-visible:border-ring/50 focus-visible:ring-2 focus-visible:ring-ring/20 transition-all duration-200'

export default function LoginPage() {
  const router = useRouter()
  const login = useAuthStore((s) => s.login)
  const vipLogin = useAuthStore((s) => s.vipLogin)
  const vipLoginEnabled = useAuthStore((s) => s.config.vipLoginEnabled)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [vipOpen, setVipOpen] = useState(false)
  const [vipPassword, setVipPassword] = useState('')
  const [vipError, setVipError] = useState<string | null>(null)
  const [vipSubmitting, setVipSubmitting] = useState(false)

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

  function handleVipOpenChange(open: boolean) {
    setVipOpen(open)
    if (!open) {
      setVipPassword('')
      setVipError(null)
    }
  }

  async function handleVipSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (vipSubmitting) return

    setVipError(null)
    setVipSubmitting(true)
    try {
      await vipLogin(vipPassword)
      handleVipOpenChange(false)
      router.replace('/')
    } catch {
      setVipError('密码错误')
    } finally {
      setVipSubmitting(false)
    }
  }

  return (
    <div className="flex h-dvh">
      <AuthBrandPanel />

      <div className="relative flex w-full items-center justify-center overflow-hidden p-6 lg:w-2/5">
        <AuthBackground variant="form" />

        <div className="auth-fade-up relative z-10 w-full max-w-[440px] rounded-3xl border border-border bg-card/50 p-8 backdrop-blur-xl">
          {/* Logo + 标题 */}
          <div className="mb-8 flex flex-col items-center text-center">
            <AuthLogo size={56} className="mb-5" />
            <h1 className="auth-form-title text-3xl font-bold tracking-tight md:text-4xl">
              欢迎回来
            </h1>
            <p className="mt-2 text-[15px] text-muted-foreground">
              登录以继续你的协作
            </p>
          </div>

          {/* 表单 */}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
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
                autoFocus
                className={inputClass}
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
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                className={inputClass}
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
              className="auth-btn-shimmer auth-fade-up-delay-3 group relative mt-2 h-12 w-full overflow-hidden bg-gradient-to-b from-primary to-primary/90 text-sm font-medium tracking-wide transition-all duration-300 hover:shadow-lg hover:shadow-primary/30 hover:brightness-110 hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
            >
              <span className="pointer-events-none absolute inset-0 overflow-hidden">
                <span className="absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-foreground/15 to-transparent" />
              </span>
              {submitting ? <Loader2 className="size-4 animate-spin" /> : '登录'}
            </Button>
          </form>

          {/* 分隔线 */}
          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">还没有账户</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          {/* 注册链接 */}
          <Link
            href="/register"
            className="block rounded-lg border border-border py-3 text-center text-sm font-medium text-foreground transition-all duration-200 hover:bg-muted hover:-translate-y-0.5"
          >
            注册新账户
          </Link>

          {/* VIP 登录 */}
          {vipLoginEnabled && (
            <button
              type="button"
              onClick={() => handleVipOpenChange(true)}
              className="mt-4 flex w-full items-center justify-center gap-1.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <Crown className="size-3.5" />
              VIP 登录
            </button>
          )}

          {/* 信任信号 */}
          <div className="mt-6 flex items-center justify-center gap-4 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Workflow className="size-3" />
              并行调度
            </span>
            <span className="size-1 rounded-full bg-border" />
            <span className="flex items-center gap-1.5">
              <FileCode className="size-3" />
              产物预览
            </span>
            <span className="size-1 rounded-full bg-border" />
            <span className="flex items-center gap-1.5">
              <Network className="size-3" />
              知识图谱
            </span>
          </div>
        </div>
      </div>

      <Dialog open={vipOpen} onOpenChange={handleVipOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>VIP 登录</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleVipSubmit}>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="vip-password" className="text-sm font-medium">
                密码
              </label>
              <Input
                id="vip-password"
                type="password"
                placeholder="请输入密码"
                value={vipPassword}
                onChange={(e) => setVipPassword(e.target.value)}
                required
                disabled={vipSubmitting}
                autoComplete="current-password"
                autoFocus
              />
            </div>
            {vipError && (
              <p
                className="mt-3 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
                role="alert"
              >
                {vipError}
              </p>
            )}
            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="outline"
                disabled={vipSubmitting}
                onClick={() => handleVipOpenChange(false)}
              >
                取消
              </Button>
              <Button type="submit" disabled={vipSubmitting || !vipPassword}>
                {vipSubmitting ? <Loader2 className="size-4 animate-spin" /> : '登录'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
