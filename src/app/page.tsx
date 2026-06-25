import { ArtifactPreviewPanel } from '@/components/artifact-preview-panel'
import { ChatPanel } from '@/components/chat-panel'
import { FileExplorerPanel } from '@/components/file-explorer-panel'
import { MessageHighlightLayer } from '@/components/message-highlight-layer'
import { SelectionPopover } from '@/components/selection-popover'
import { Sidebar } from '@/components/sidebar'

export default function Home() {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <ChatPanel />
      <FileExplorerPanel />
      <ArtifactPreviewPanel />
      <SelectionPopover />
      <MessageHighlightLayer />
    </div>
  )
}
