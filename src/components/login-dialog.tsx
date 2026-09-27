'use client'

import { Crown, Loader2, LogIn, UserPlus } from 'lucide-react'
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

function mapRegisterError(err: unknown): string {
  const status = (err as { status?: number } | null)?.status
  if (status === 409) return '该邮箱已被注册，试试直接登录'
  if (status === 403) return '当前部署已关闭注册'
  return err instanceof Error ? err.message : '注册失败'
}

export function LoginDialog() {
  const showLoginDialog = useAuthStore((s) => s.showLoginDialog)
  const closeLoginDialog = useAuthStore((s) => s.closeLoginDialog)
  const login = useAuthStore((s) => s.login)
  const register = useAuthStore((s) => s.register)
  const vipLogin = useAuthStore((s) => s.vipLogin)
  const vipLoginEnabled = useAuthStore((s) => s.config.vipLoginEnabled)
  const allowRegistration = useAuthStore((s) => s.config.allowRegistration)

  const [mode, setMode] = useState<'login' | 'register'>('login')
  // 配置取不到时 allowRegistration 为 false：即使 mode 残留也强制渲染登录表单
  const isRegister = allowRegistration && mode === 'register'
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [vipOpen, setVipOpen] = useState(false)
  const [vipPassword, setVipPassword] = useState('')
  const [vipError, setVipError] = useState<string | null>(null)
  const [vipSubmitting, setVipSubmitting] = useState(false)

  function handleOpenChange(open: boolean) {
    if (!open) {
      setMode('login')
      setError(null)
      setPassword('')
      closeLoginDialog()
    }
  }

  function switchMode(next: 'login' | 'register') {
    setMode(next)
    setError(null)
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
    if (isRegister && password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    setSubmitting(true)
    try {
      if (isRegister) {
        // register 不会自己关对话框（store 不碰 showLoginDialog），成功后显式关闭
        await register(email, name, password)
        closeLoginDialog()
      } else {
        await login(email, password)
      }
    } catch (err) {
      setError(
        isRegister
          ? mapRegisterError(err)
          : err instanceof Error
            ? err.message
            : 'Login failed',
      )
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
              {isRegister ? '创建账户' : '欢迎回来'}
            </DialogTitle>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {isRegister ? '加入 AChat，开启多 Agent 协作' : '登录以继续你的协作'}
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
            {isRegister && (
              <div className="flex flex-col gap-2">
                <label
                  htmlFor="nickname"
                  className="text-xs font-medium tracking-wide text-muted-foreground"
                >
                  昵称
                </label>
                <Input
                  id="nickname"
                  type="text"
                  placeholder="你的名字"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  autoComplete="nickname"
                  className={inputClass}
                />
              </div>
            )}
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
                placeholder={isRegister ? '至少 8 个字符' : '••••••••'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={isRegister ? 8 : undefined}
                autoComplete={isRegister ? 'new-password' : 'current-password'}
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
              {submitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : isRegister ? (
                '注册'
              ) : (
                '登录'
              )}
            </Button>
          </form>

          {/* 注册 / 登录模式切换 */}
          {allowRegistration && (
            <button
              type="button"
              onClick={() => switchMode(isRegister ? 'login' : 'register')}
              className="mt-2 flex w-full items-center justify-center gap-1.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              {isRegister ? (
                <LogIn className="size-3.5" />
              ) : (
                <UserPlus className="size-3.5" />
              )}
              {isRegister ? '已有账户？返回登录' : '没有账户？创建一个'}
            </button>
          )}

          {/* VIP 登录 */}
          {vipLoginEnabled && (
            <button
              type="button"
              onClick={() => handleVipOpenChange(true)}
              className="flex w-full items-center justify-center gap-1.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
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
