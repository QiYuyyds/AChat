'use client'

import { Loader2, User } from 'lucide-react'
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
  fetchProfile,
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
        return fetchProfile()
      })
      .then((p) => {
        if (cancelled) return
        const f: ProfileForm = {
          name: p.name ?? '',
          location: p.location ?? '',
          hometown: p.hometown ?? '',
          preferences: p.preferences ?? '',
          bio: p.bio ?? '',
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
      const patch: ProfileUpdateBody = {}
      ;(Object.keys(form) as Array<keyof ProfileForm>).forEach((key) => {
        const current = form[key].trim()
        const original = initial[key].trim()
        if (current !== original) {
          patch[key] = current || null
        }
      })
      if (Object.keys(patch).length > 0) {
        const updated = await updateProfile(patch)
        const f: ProfileForm = {
          name: updated.name ?? '',
          location: updated.location ?? '',
          hometown: updated.hometown ?? '',
          preferences: updated.preferences ?? '',
          bio: updated.bio ?? '',
        }
        setForm(f)
        setInitial(f)
      }
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
          <section className="flex flex-col gap-4 rounded-lg border bg-muted/30 p-4">
            <div className="flex items-center gap-4">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={avatarUploading}
                className="group relative size-16 shrink-0 rounded-full transition hover:ring-2 hover:ring-primary hover:ring-offset-2 disabled:opacity-50"
                title="点击上传头像"
              >
                <Avatar className="size-16" size="lg">
                  {avatarSrc && <AvatarImage src={avatarSrc} alt="头像" />}
                  <AvatarFallback className="bg-primary text-lg text-primary-foreground">
                    {avatarUploading ? (
                      <Loader2 className="size-5 animate-spin" />
                    ) : (
                      <User className="size-6" />
                    )}
                  </AvatarFallback>
                </Avatar>
              </button>
              <div className="flex flex-col gap-0.5">
                <p className="text-sm font-medium">头像</p>
                <p className="text-[11px] text-muted-foreground">
                  支持 PNG / JPEG / WebP / GIF，最大 2MB
                </p>
              </div>
            </div>

            {loading ? (
              <div className="flex h-20 items-center justify-center">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="grid gap-3">
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">姓名</label>
                  <Input
                    value={form.name}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, name: e.target.value }))
                    }
                    placeholder="你的名字"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">所在地</label>
                  <Input
                    value={form.location}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, location: e.target.value }))
                    }
                    placeholder="如：北京"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">家乡</label>
                  <Input
                    value={form.hometown}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, hometown: e.target.value }))
                    }
                    placeholder="如：成都"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">喜好</label>
                  <Input
                    value={form.preferences}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, preferences: e.target.value }))
                    }
                    placeholder="如：编程、音乐"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">简介</label>
                  <Textarea
                    value={form.bio}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, bio: e.target.value }))
                    }
                    placeholder="介绍一下自己…"
                    rows={3}
                  />
                </div>
              </div>
            )}
          </section>
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
        className="relative flex size-10 items-center justify-center rounded-md text-muted-foreground transition hover:bg-accent hover:text-foreground"
      >
        <Avatar className="size-7">
          {avatarSrc && <AvatarImage src={avatarSrc} alt={user?.name ?? 'avatar'} />}
          <AvatarFallback className="bg-primary/10 text-xs text-primary">
            {user?.name?.charAt(0).toUpperCase() ?? (
              <User className="size-4" />
            )}
          </AvatarFallback>
        </Avatar>
      </button>
      <ProfileDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
