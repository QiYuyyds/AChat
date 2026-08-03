'use client'

import { BarChart3, Cpu } from 'lucide-react'

import { AnalyticsMainPanel } from '@/components/analytics-main-panel'
import { ModelConfigTab } from '@/components/model-config-tab'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAppStore } from '@/stores/app-store'

export function ResourcesMainPanel() {
  const tab = useAppStore((s) => s.resourcesTab)
  const setTab = useAppStore((s) => s.setResourcesTab)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Top-level tab switcher — always visible */}
      <div className="flex shrink-0 items-center border-b px-6 py-4">
        <Tabs value={tab} onValueChange={(v) => setTab(v as 'models' | 'analytics')}>
          <TabsList className="h-9">
            <TabsTrigger value="models" className="gap-1.5 text-xs">
              <Cpu className="size-3.5" />
              模型配置
            </TabsTrigger>
            <TabsTrigger value="analytics" className="gap-1.5 text-xs">
              <BarChart3 className="size-3.5" />
              用量分析
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Tab content */}
      {tab === 'models' ? (
        <ScrollArea className="min-h-0 flex-1">
          <div className="px-6 py-6">
            <ModelConfigTab />
          </div>
        </ScrollArea>
      ) : (
        <AnalyticsMainPanel hideHeader />
      )}
    </div>
  )
}
