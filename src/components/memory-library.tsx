'use client'

import { Brain } from 'lucide-react'

import { LongTermMemoryPanel } from '@/components/settings/memory-management/long-term-memory-panel'
import { PreferencePanel } from '@/components/settings/memory-management/preference-panel'
import { SessionMemoryPanel } from '@/components/settings/memory-management/session-memory-panel'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useAppStore, type MemoryTab } from '@/stores/app-store'

const TABS: { id: MemoryTab; label: string; desc: string }[] = [
  { id: 'long-term', label: '长期记忆', desc: 'Agent 在对话中积累的事实、技能与项目知识' },
  { id: 'preferences', label: '用户偏好', desc: '跨会话持久化的用户设置与喜好' },
  { id: 'session', label: '会话摘要', desc: '各会话的压缩摘要，用于跨 run 上下文恢复' },
]

/** 侧边栏导航：仅展示子 Tab 切换 + 当前 Tab 描述 */
export function MemorySidebarNav() {
  const tab = useAppStore((s) => s.memoryTab)
  const setTab = useAppStore((s) => s.setMemoryTab)
  const activeTab = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-2 px-3 pt-3 pb-2">
        <Brain className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">记忆管理</h2>
      </div>

      <div className="flex shrink-0 flex-col gap-0.5 px-3 pb-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              'rounded-md px-2.5 py-1.5 text-left text-xs font-medium transition',
              tab === t.id
                ? 'bg-accent text-foreground'
                : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="shrink-0 border-t px-3 py-2">
        <p className="text-[11px] leading-4 text-muted-foreground">{activeTab.desc}</p>
      </div>
    </div>
  )
}

/** 主区域内容：根据当前 Tab 渲染完整表格 */
export function MemoryMainPanel() {
  const tab = useAppStore((s) => s.memoryTab)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-2 border-b px-6 py-3">
        <Brain className="size-5 text-muted-foreground" />
        <h2 className="text-base font-semibold">
          {TABS.find((t) => t.id === tab)?.label ?? '记忆管理'}
        </h2>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="p-6">
          {tab === 'long-term' && <LongTermMemoryPanel />}
          {tab === 'preferences' && <PreferencePanel />}
          {tab === 'session' && <SessionMemoryPanel />}
        </div>
      </ScrollArea>
    </div>
  )
}
