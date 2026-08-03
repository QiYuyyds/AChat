'use client'

import { Crown, Loader2 } from 'lucide-react'
import { useState } from 'react'

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

export function LoginDialog() {
  const showLoginDialog = useAuthStore((s) => s.showLoginDialog)
  const closeLoginDialog = useAuthStore((s) => s.closeLoginDialog)
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

  function handleOpenChange(open: boolean) {
    if (!open) {
      setError(null)
      setPassword('')
      closeLoginDialog()
    }
  }

  function handleVipOpenChange(open: boolean) {
    setVipOpen(open)
    if (!open) {
      setVipPassword('')
      setVipError(null)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setSubmitting(false)
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
    } catch {
      setVipError('密码错误')
    } finally {
      setVipSubmitting(false)
    }
  }

  return (
    <>
      <Dialog open={showLoginDialog} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-[440px] p-8">
          {/* Logo + 标题 */}
          <div className="mb-6 flex flex-col items-center text-center">
            <AuthLogo size={48} className="mb-4" />
            <DialogTitle className="text-2xl font-bold tracking-tight">
              欢迎回来
            </DialogTitle>
            <p className="mt-1.5 text-sm text-muted-foreground">
              登录以继续你的协作
            </p>
          </div>

          {/* 表单 */}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
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
            <div className="flex flex-col gap-2">
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
              className="group relative mt-2 h-12 w-full overflow-hidden bg-gradient-to-b from-primary to-primary/90 text-sm font-medium tracking-wide transition-all duration-300 hover:shadow-lg hover:shadow-primary/30 hover:brightness-110 active:scale-[0.98]"
            >
              {submitting ? <Loader2 className="size-4 animate-spin" /> : '登录'}
            </Button>
          </form>

          {/* VIP 登录 */}
          {vipLoginEnabled && (
            <button
              type="button"
              onClick={() => handleVipOpenChange(true)}
              className="mt-2 flex w-full items-center justify-center gap-1.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <Crown className="size-3.5" />
              VIP 登录
            </button>
          )}
        </DialogContent>
      </Dialog>

      {/* VIP 登录子对话框 */}
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
    </>
  )
}
