'use client'

import { AnalyticsMainPanel } from '@/components/analytics-main-panel'
import { ArtifactPreviewPanel } from '@/components/artifact-preview-panel'
import { ChatPanel } from '@/components/chat-panel'
import { FileExplorerPanel } from '@/components/file-explorer-panel'
import { GuideFloatingPanel } from '@/components/guide-floating-panel'
import { KnowledgeMainPanel } from '@/components/knowledge-library'
import { MemoryMainPanel } from '@/components/memory-library'
import { MessageHighlightLayer } from '@/components/message-highlight-layer'
import { SelectionPopover } from '@/components/selection-popover'
import { Sidebar } from '@/components/sidebar'
import { WorkspaceBackground } from '@/components/workspace-background'
import { useAppStore } from '@/stores/app-store'

export default function Home() {
  const sidebarMode = useAppStore((s) => s.sidebarMode)

  return (
    <div className="relative flex h-dvh overflow-hidden">
      <WorkspaceBackground />
      <Sidebar />
      {sidebarMode === 'memory' ? <MemoryMainPanel /> : sidebarMode === 'knowledge' ? <KnowledgeMainPanel /> : sidebarMode === 'analytics' ? <AnalyticsMainPanel /> : <ChatPanel />}
      <FileExplorerPanel />
      <ArtifactPreviewPanel />
      <SelectionPopover />
      <MessageHighlightLayer />
      <GuideFloatingPanel />
    </div>
  )
}
