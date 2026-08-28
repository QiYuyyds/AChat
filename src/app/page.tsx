'use client'

import { ArtifactPreviewPanel } from '@/components/artifact-preview-panel'
import { AgentMainPanel } from '@/components/agent-main-panel'
import { ArtifactMainPanel } from '@/components/artifact-main-panel'
import { ChatPanel } from '@/components/chat-panel'
import { CognitionMainPanel } from '@/components/cognition-main-panel'
import { ExtensionMainPanel } from '@/components/extension-main-panel'
import { FileExplorerPanel } from '@/components/file-explorer-panel'
import { GuideFloatingPanel } from '@/components/guide-floating-panel'
import { LoginDialog } from '@/components/login-dialog'
import { MessageHighlightLayer } from '@/components/message-highlight-layer'
import { ResourcesMainPanel } from '@/components/resources-main-panel'
import { SelectionPopover } from '@/components/selection-popover'
import { SessionNoteSidePanel } from '@/components/session-note-side-panel'
import { Sidebar } from '@/components/sidebar'
import { TaskBoardView } from '@/components/task-board-view'
import { TaskDetailPanel } from '@/components/task-detail-panel'
import { WelcomeScreen } from '@/components/welcome-screen'
import { WorkspaceBackground } from '@/components/workspace-background'
import { useAppStore } from '@/stores/app-store'
import { useAuthStore } from '@/stores/auth-store'

export default function Home() {
  const sidebarMode = useAppStore((s) => s.sidebarMode)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  return (
    <div className="relative flex h-dvh overflow-hidden">
      <WorkspaceBackground />
      <Sidebar />
      {!isAuthenticated ? (
        <WelcomeScreen />
      ) : sidebarMode === 'agents' ? (
        <AgentMainPanel />
      ) : sidebarMode === 'artifacts' ? (
        <ArtifactMainPanel />
      ) : sidebarMode === 'cognition' ? (
        <CognitionMainPanel />
      ) : sidebarMode === 'extensions' ? (
        <ExtensionMainPanel />
      ) : sidebarMode === 'resources' ? (
        <ResourcesMainPanel />
      ) : sidebarMode === 'tasks' ? (
        <TaskBoardView />
      ) : (
        <ChatPanel />
      )}
      <FileExplorerPanel />
      <SessionNoteSidePanel />
      <ArtifactPreviewPanel />
      <TaskDetailPanel />
      <SelectionPopover />
      <MessageHighlightLayer />
      {isAuthenticated && <GuideFloatingPanel />}
      <LoginDialog />
    </div>
  )
}
