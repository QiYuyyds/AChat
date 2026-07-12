'use client'

import { ArtifactPreviewPanel } from '@/components/artifact-preview-panel'
import { ChatPanel } from '@/components/chat-panel'
import { FileExplorerPanel } from '@/components/file-explorer-panel'
import { MemoryMainPanel } from '@/components/memory-library'
import { MessageHighlightLayer } from '@/components/message-highlight-layer'
import { SelectionPopover } from '@/components/selection-popover'
import { Sidebar } from '@/components/sidebar'
import { useAppStore } from '@/stores/app-store'

export default function Home() {
  const sidebarMode = useAppStore((s) => s.sidebarMode)

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <Sidebar />
      {sidebarMode === 'memory' ? <MemoryMainPanel /> : <ChatPanel />}
      <FileExplorerPanel />
      <ArtifactPreviewPanel />
      <SelectionPopover />
      <MessageHighlightLayer />
    </div>
  )
}
