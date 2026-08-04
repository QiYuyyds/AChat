'use client'

import { BarChart3, Cpu } from 'lucide-react'

import { AnalyticsMainPanel } from '@/components/analytics-main-panel'
import { ModelConfigTab } from '@/components/model-config-tab'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

type ResourcesTab = 'models' | 'analytics'

const TABS: { value: ResourcesTab; label: string; icon: typeof Cpu }[] = [
  { value: 'models', label: '模型配置', icon: Cpu },
  { value: 'analytics', label: '用量分析', icon: BarChart3 },
]

export function ResourcesMainPanel() {
  const tab = useAppStore((s) => s.resourcesTab)
  const setTab = useAppStore((s) => s.setResourcesTab)

  const activeIndex = TABS.findIndex((t) => t.value === tab)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Segmented tab switcher */}
      <header className="agent-fade-up relative shrink-0 border-b border-border px-6 py-3">
        <div className="relative flex w-fit items-center rounded-lg bg-muted p-0.5">
          {/* Sliding indicator */}
          <span
            className="pointer-events-none absolute top-0.5 bottom-0.5 left-0.5 w-[calc(50%-2px)] rounded-md bg-background shadow-[var(--shadow-sm),var(--inset-hi)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{ transform: `translateX(${activeIndex * 100}%)` }}
          />

          {TABS.map((t) => {
            const Icon = t.icon
            const isActive = t.value === tab
            return (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setTab(t.value)}
                className={cn(
                  'relative z-10 inline-flex h-8 w-[7.5rem] items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors duration-200',
                  isActive
                    ? 'text-foreground'
                    : 'text-muted-foreground hover:text-foreground/70',
                )}
              >
                <Icon
                  className={cn(
                    'size-3.5 transition-transform duration-300',
                    isActive && 'scale-110',
                  )}
                />
                {t.label}
              </button>
            )
          })}
        </div>
      </header>

      {/* Tab content */}
      <div key={tab} className="tab-content-enter min-h-0 flex-1">
        {tab === 'models' ? (
          <ScrollArea className="min-h-0 h-full">
            <div className="px-6 py-6">
              <ModelConfigTab />
            </div>
          </ScrollArea>
        ) : (
          <AnalyticsMainPanel hideHeader />
        )}
      </div>
    </div>
  )
}
