'use client'

import { BookOpen, Loader2, User } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  fetchAppSettings,
  fetchProfile,
  updateAppSettings,
  updateProfile,
  uploadAvatar,
  type ProfileUpdateBody,
} from '@/lib/api'
import { API_BASE_URL } from '@/lib/config'
import { useAuthStore } from '@/stores/auth-store'

interface ProfileForm {
  name: string
  location: string
  hometown: string
  preferences: string
  bio: string
  obsidianVaultPath: string
}

export function ProfileDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [avatarUploading, setAvatarUploading] = useState(false)
  const [form, setForm] = useState<ProfileForm>({
    name: '',
    location: '',
    hometown: '',
    preferences: '',
    bio: '',
    obsidianVaultPath: '',
  })
  const [initial, setInitial] = useState<ProfileForm | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const authUser = useAuthStore((s) => s.user)
  const updateAvatarInStore = useAuthStore((s) => s.updateAvatar)

  useEffect(() => {
    if (!open) return
    let cancelled = false

    void Promise.resolve()
      .then(() => {
        if (!cancelled) setLoading(true)
        return Promise.all([fetchProfile(), fetchAppSettings()])
      })
      .then(([p, settings]) => {
        if (cancelled) return
        const f: ProfileForm = {
          name: p.name ?? '',
          location: p.location ?? '',
          hometown: p.hometown ?? '',
          preferences: p.preferences ?? '',
          bio: p.bio ?? '',
          obsidianVaultPath: (settings as Record<string, unknown>).obsidianVaultPath as string ?? '',
        }
        setForm(f)
        setInitial(f)
      })
      .catch((err) => console.error('[ProfileDialog] load failed', err))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [open])

  const handleAvatarSelect = async (file: File) => {
    if (avatarUploading) return
    setAvatarUploading(true)
    try {
      const result = await uploadAvatar(file)
      updateAvatarInStore(result.avatarUrl)
    } catch (err) {
      console.error('[ProfileDialog] avatar upload failed', err)
    } finally {
      setAvatarUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleSave = async () => {
    if (busy || !initial) return
    setBusy(true)
    try {
      const patch: Record<string, string | null> = {}
      const profileKeys: Array<keyof ProfileForm> = ['name', 'location', 'hometown', 'preferences', 'bio']
      for (const key of profileKeys) {
        const current = form[key].trim()
        const original = initial[key].trim()
        if (current !== original) {
          patch[key] = current || null
        }
      }
      if (Object.keys(patch).length > 0) {
        const updated = await updateProfile(patch)
        setForm((f) => ({
          ...f,
          name: updated.name ?? '',
          location: updated.location ?? '',
          hometown: updated.hometown ?? '',
          preferences: updated.preferences ?? '',
          bio: updated.bio ?? '',
        }))
      }

      const currentVault = form.obsidianVaultPath.trim()
      const originalVault = initial.obsidianVaultPath.trim()
      if (currentVault !== originalVault) {
        const settingsResult = await updateAppSettings({
          obsidianVaultPath: currentVault || null,
        })
        const newVaultPath = (settingsResult as Record<string, unknown>).obsidianVaultPath as string ?? ''
        setForm((f) => ({ ...f, obsidianVaultPath: newVaultPath }))
      }

      setInitial({
        ...form,
        obsidianVaultPath: form.obsidianVaultPath,
      })
      onOpenChange(false)
    } catch (err) {
      console.error('[ProfileDialog] save failed', err)
    } finally {
      setBusy(false)
    }
  }

  const avatarSrc = authUser?.avatarUrl
    ? `${API_BASE_URL}${authUser.avatarUrl}`
    : undefined

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] max-w-xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>个人信息</DialogTitle>
          <DialogDescription className="sr-only">
            编辑个人资料与头像
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 overflow-y-auto pr-1">
          {/* ─── Avatar section — premium card with ambient glow ─── */}
          <section className="agent-fade-up relative overflow-hidden rounded-lg border border-border/40 bg-muted/20 p-4">
            <div className="agent-ambient pointer-events-none absolute -right-8 -top-8 size-28 rounded-full bg-primary/[0.05] blur-2xl" />
            <div className="relative flex items-center gap-4">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={avatarUploading}
                className="group relative size-20 shrink-0 rounded-full transition-all duration-500 hover:ring-2 hover:ring-primary hover:ring-offset-2 hover:ring-offset-background disabled:opacity-50"
                title="点击上传头像"
              >
                <div className="pointer-events-none absolute inset-0 rounded-full bg-primary/10 opacity-0 blur-md transition-opacity duration-500 group-hover:opacity-100" />
                <Avatar className="size-20" size="lg">
                  {avatarSrc && <AvatarImage src={avatarSrc} alt="头像" />}
                  <AvatarFallback className="bg-primary text-xl text-primary-foreground">
                    {avatarUploading ? (
                      <Loader2 className="size-6 animate-spin" />
                    ) : (
                      <User className="size-7" />
                    )}
                  </AvatarFallback>
                </Avatar>
              </button>
              <div className="flex flex-col gap-0.5">
                <p className="text-sm font-semibold tracking-tight">个人头像</p>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  支持 PNG / JPEG / WebP / GIF，最大 2MB
                </p>
              </div>
            </div>
          </section>

          {loading ? (
            <div className="agent-fade-up-delay-1 flex h-24 items-center justify-center">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            /* ─── Form fields — gapless bento grid ─── */
            <div className="agent-fade-up-delay-1 mt-3 grid grid-flow-row-dense grid-cols-2 gap-3">
              {/* 姓名 + 所在地 */}
              <div className="grid gap-1.5">
                <label className="text-xs font-medium tracking-tight">姓名</label>
                <Input
                  value={form.name}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, name: e.target.value }))
                  }
                  placeholder="你的名字"
                  className="transition-shadow duration-300 focus-visible:ring-primary/40"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium tracking-tight">所在地</label>
                <Input
                  value={form.location}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, location: e.target.value }))
                  }
                  placeholder="如：北京"
                  className="transition-shadow duration-300 focus-visible:ring-primary/40"
                />
              </div>

              {/* 家乡 + 喜好 */}
              <div className="grid gap-1.5">
                <label className="text-xs font-medium tracking-tight">家乡</label>
                <Input
                  value={form.hometown}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, hometown: e.target.value }))
                  }
                  placeholder="如：成都"
                  className="transition-shadow duration-300 focus-visible:ring-primary/40"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium tracking-tight">喜好</label>
                <Input
                  value={form.preferences}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, preferences: e.target.value }))
                  }
                  placeholder="如：编程、音乐"
                  className="transition-shadow duration-300 focus-visible:ring-primary/40"
                />
              </div>

              {/* 简介 — full width */}
              <div className="col-span-2 grid gap-1.5">
                <label className="text-xs font-medium tracking-tight">简介</label>
                <Textarea
                  value={form.bio}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, bio: e.target.value }))
                  }
                  placeholder="介绍一下自己…"
                  rows={3}
                  className="transition-shadow duration-300 focus-visible:ring-primary/40"
                />
              </div>

              {/* ─── Obsidian Vault — premium card with ambient gradient ─── */}
              <div className="agent-fade-up-delay-2 col-span-2 relative overflow-hidden rounded-lg border border-border/40 bg-card p-3">
                <div className="pointer-events-none absolute -right-6 -top-6 size-20 rounded-full bg-chart-3/[0.05] opacity-60 blur-2xl" />
                <div className="relative">
                  <div className="flex items-center gap-1.5">
                    <BookOpen className="size-3.5 text-primary/60" />
                    <label className="text-xs font-semibold tracking-tight">Obsidian Vault 路径</label>
                  </div>
                  <Input
                    value={form.obsidianVaultPath}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, obsidianVaultPath: e.target.value }))
                    }
                    placeholder="C:\Users\用户\Obsidian\MyVault"
                    className="mt-2 transition-shadow duration-300 focus-visible:ring-primary/40"
                  />
                  <p className="mt-1.5 text-[10px] leading-4 text-muted-foreground">
                    填写本地 Vault 目录路径，配置后可在知识库页面点击「同步」导入笔记。留空则禁用同步。
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void handleAvatarSelect(file)
          }}
        />

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            取消
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={busy || loading}
          >
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 个人信息 button 入口，挂在 Sidebar 底部图标轨。 */
export function ProfileButton() {
  const [open, setOpen] = useState(false)
  const user = useAuthStore((s) => s.user)
  const avatarSrc = user?.avatarUrl
    ? `${API_BASE_URL}${user.avatarUrl}`
    : undefined

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={user ? `个人信息 (${user.email})` : '个人信息'}
        aria-label="个人信息"
        className="group relative flex size-10 items-center justify-center rounded-lg text-muted-foreground transition-all duration-300 hover:bg-accent hover:text-foreground"
      >
        <Avatar className="size-7 transition-transform duration-500 group-hover:scale-105">
          {avatarSrc && <AvatarImage src={avatarSrc} alt={user?.name ?? 'avatar'} />}
          <AvatarFallback className="bg-primary/10 text-xs text-primary">
            {user?.name?.charAt(0).toUpperCase() ?? (
              <User className="size-5" />
            )}
          </AvatarFallback>
        </Avatar>
      </button>
      <ProfileDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
