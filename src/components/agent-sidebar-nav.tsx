'use client'

import { Users } from 'lucide-react'

export function AgentSidebarNav() {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 px-3 pt-4 pb-3">
        <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10">
          <Users className="size-3.5 text-primary" />
        </div>
        <h2 className="text-sm font-semibold">联系人</h2>
      </div>

      {/* Description */}
      <div className="shrink-0 px-3 pb-3">
        <p className="text-[11px] leading-4 text-muted-foreground">
          管理内置与自建联系人，配置工具与能力
        </p>
      </div>

      {/* Empty state - content is in main panel */}
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-4 py-8 text-center">
        <div className="flex size-12 items-center justify-center rounded-xl bg-muted/60">
          <Users className="size-6 text-muted-foreground opacity-50" />
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          在主面板中查看和管理
        </p>
      </div>
    </div>
  )
}
