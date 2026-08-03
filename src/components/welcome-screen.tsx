'use client'

import { FileCode, Network, Workflow } from 'lucide-react'

import { AuthLogo } from '@/components/AuthLogo'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth-store'

export function WelcomeScreen() {
  const openLoginDialog = useAuthStore((s) => s.openLoginDialog)

  return (
    <div className="relative flex flex-1 items-center justify-center overflow-hidden">
      <div className="relative z-10 flex max-w-md flex-col items-center gap-8 px-6 text-center">
        {/* Logo */}
        <AuthLogo size={64} className="mb-2" />

        {/* Title */}
        <div className="space-y-3">
          <h1 className="text-4xl font-bold leading-[1.05] tracking-tight md:text-5xl">
            多个 Agent，
            <br />
            一个群聊
          </h1>
          <p className="text-[15px] leading-relaxed text-muted-foreground">
            Orchestrator 自动拆任务、并行调度、聚合产物。你只管发需求。
          </p>
        </div>

        {/* CTA */}
        <Button
          type="button"
          onClick={openLoginDialog}
          className="group relative h-12 overflow-hidden bg-gradient-to-b from-primary to-primary/90 px-8 text-sm font-medium tracking-wide transition-all duration-300 hover:shadow-lg hover:shadow-primary/30 hover:brightness-110 hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
        >
          <span className="pointer-events-none absolute inset-0 overflow-hidden">
            <span className="absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-foreground/15 to-transparent" />
          </span>
          立即登录
        </Button>

        {/* Feature highlights */}
        <div className="flex items-center justify-center gap-4 text-[11px] text-muted-foreground">
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
  )
}
