'use client'

import { AlertTriangle, FilePenLine, FileStack, Files, Menu, MessagesSquare, MoreHorizontal, PanelRight, UploadCloud, UserRoundPlus, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { AddAgentDialog } from '@/components/add-agent-dialog'
import { AgentInfoPopover } from '@/components/agent-info-popover'
import { AskUserQuestionDialog } from '@/components/ask-user-question-dialog'
import { ArtifactLibrary } from '@/components/artifact-library'
import { CodeIntelligenceControl } from '@/components/code-intelligence-control'
import { ConversationOutline } from '@/components/conversation-outline'
import { FileLibraryDialog } from '@/components/file-library-dialog'
import { FileTab } from '@/components/file-tab'
import { PendingWriteDiffTab } from '@/components/pending-write-diff-tab'
import { PendingBashCommandsPanel } from '@/components/pending-bash-commands-panel'
import { PendingMcpCallsPanel } from '@/components/pending-mcp-call-card'
import { PendingWritesPanel } from '@/components/pending-writes-panel'
import { MergeConflictPanel } from '@/components/merge-conflict-panel'
import { diffTabPendingId, isDiffTabId } from '@/components/pending-writes-panel'
import { PinnedMessagesBar } from '@/components/pinned-messages-bar'
import { WorkspaceEnvHintCard } from '@/components/workspace-env-hint-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { MessageInput } from '@/components/message-input'
import { MessageList } from '@/components/message-list'
import { UsageBadge } from '@/components/usage-badge'
import type { AgentRow } from '@/db/schema'
import { useAttachmentUpload } from '@/hooks/use-attachment-upload'
import { fetchPendingDispatchPlans, fetchProfile } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  useActiveConversation,
  useActiveTab,
  useAppStore,
  useOpenFiles,
  usePendingWrites,
} from '@/stores/app-store'

export function ChatPanel() {
  const conv = useActiveConversation()
  const agents = useAppStore((s) => s.agents)
  const streamConnected = useAppStore((s) => s.streamConnected)
  const fileExplorerOpen = useAppStore((s) => s.fileExplorerOpen)
  const previewArtifactId = useAppStore((s) => s.previewArtifactId)
  const setFileExplorerOpen = useAppStore((s) => s.setFileExplorerOpen)
  const setMobileSidebarOpen = useAppStore((s) => s.setMobileSidebarOpen)
  const closeFile = useAppStore((s) => s.closeFile)
  const setActiveTab = useAppStore((s) => s.setActiveTab)
  const [profileName, setProfileName] = useState<string | null>(null)
  const setPendingDispatchPlansForConversation = useAppStore(
    (s) => s.setPendingDispatchPlansForConversation,
  )
  const [addOpen, setAddOpen] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const dragCounter = useRef(0)
  const { handleFiles, uploading } = useAttachmentUpload(conv?.id ?? '')

  // 获取个人信息中的姓名用于欢迎页问候
  useEffect(() => {
    fetchProfile()
      .then((p) => setProfileName(p.name))
      .catch(() => setProfileName(null))
  }, [])

  const openFiles = useOpenFiles(conv?.id ?? '')
  const activeTab = useActiveTab(conv?.id ?? '')
  const pendingWrites = usePendingWrites(conv?.id ?? null)
  const pendingById = useMemo(
    () => new Map(pendingWrites.map((p) => [p.id, p])),
    [pendingWrites],
  )

  // Pending 被 resolve（其他客户端 / SSE 移除）后，关闭对应的 diff tab —— 即使该 tab 当前在后台
  useEffect(() => {
    if (!conv) return
    for (const tabId of openFiles) {
      if (isDiffTabId(tabId) && !pendingById.has(diffTabPendingId(tabId))) {
        closeFile(conv.id, tabId)
      }
    }
  }, [conv, openFiles, pendingById, closeFile])

  useEffect(() => {
    if (!conv) return
    let cancelled = false
    fetchPendingDispatchPlans(conv.id)
      .then((list) => {
        if (!cancelled) setPendingDispatchPlansForConversation(conv.id, list)
      })
      .catch((err) => {
        console.warn('[ChatPanel] fetch pending dispatch plans failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conv, setPendingDispatchPlansForConversation])

  const handleDragEnter = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
    dragCounter.current++
    setDragOver(true)
  }

  const handleDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current--
    if (dragCounter.current <= 0) {
      dragCounter.current = 0
      setDragOver(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setDragOver(false)
    if (!conv) return
    const files = e.dataTransfer.files
    if (files && files.length > 0) void handleFiles(files)
  }

  if (!conv) {
    return (
      <main
        className="flex min-w-0 flex-1 items-center justify-center bg-background/80 backdrop-blur-2xl"
      >
        <div className="flex max-w-md flex-col items-center gap-6 px-6 text-center">
          <div className="flex size-20 items-center justify-center rounded-3xl bg-muted/60 shadow-[var(--shadow-sm)]">
            <MessagesSquare className="size-9 text-muted-foreground" />
          </div>
          <div className="space-y-3">
            <h2 className="text-2xl font-semibold">你好{profileName ? `，${profileName}` : ''}</h2>
            <p className="text-base leading-7 text-muted-foreground">
              从左侧选择会话继续聊天，或点击「+ 新建对话」召集 Agent 团队。
            </p>
            <p className="text-sm leading-6 text-muted-foreground/70">
              随时找右下角的「小A」帮你管理 Agent、知识库与记忆
            </p>
          </div>
        </div>
      </main>
    )
  }

  const participantAgents = conv.agentIds.map((id) => agents[id]).filter(Boolean)

  return (
    <main
      className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-background/60 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary/40 bg-card/90 px-8 py-6 shadow-lg">
            <UploadCloud className="size-8 text-primary/60" />
            <div className="text-center">
              <div className="text-sm font-medium">拖拽文件到此处上传</div>
              <div className="text-xs text-muted-foreground">将添加到当前会话的附件</div>
            </div>
          </div>
        </div>
      )}
      <header className="flex shrink-0 items-center gap-3 overflow-hidden border-b px-3 py-2">
        {/* 移动端 hamburger 按钮：打开侧边栏抽屉 */}
        <Button
          size="icon-sm"
          variant="ghost"
          className="shrink-0 md:hidden"
          onClick={() => setMobileSidebarOpen(true)}
          title="打开侧边栏"
          aria-label="打开侧边栏"
        >
          <Menu className="size-4" />
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
          <ParticipantStack agents={participantAgents} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="min-w-0 truncate text-sm font-medium">{conv.title}</span>
              {conv.workspaceMode === 'local' && conv.workspaceBoundPath && (
                <span
                  title={`本地工作目录：${conv.workspaceBoundPath}`}
                  className="inline-flex shrink-0 items-center gap-1 rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
                >
                  <AlertTriangle className="size-2.5" />
                  本地
                </span>
              )}
            </div>
            {conv.summary && (
              <div className="truncate text-xs text-muted-foreground">
                {conv.summary}
              </div>
            )}
          </div>
        </div>
        <div className="hidden min-w-0 max-w-[65%] shrink-0 items-center gap-1 overflow-x-auto overscroll-contain md:flex [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {/* 右侧面板切换（文件树 / 产物预览，互斥）。点同一个再关掉。 */}
          <Button
            size="icon-sm"
            variant={fileExplorerOpen ? 'default' : 'ghost'}
            onClick={() => setFileExplorerOpen(!fileExplorerOpen)}
            title={fileExplorerOpen ? '关闭文件树' : '打开文件树'}
            aria-label={fileExplorerOpen ? '关闭文件树' : '打开文件树'}
          >
            <PanelRight className="size-4 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover/button:scale-110 motion-safe:group-active/button:scale-90" />
          </Button>
          {conv.workspaceMode === 'local' && (
            <CodeIntelligenceControl conversationId={conv.id} />
          )}
          <Button
            size="icon-sm"
            variant={artifactsOpen || previewArtifactId ? 'default' : 'ghost'}
            onClick={() => setArtifactsOpen(true)}
            title="本会话产物库"
            aria-label="本会话产物库"
          >
            <FileStack className="size-4 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover/button:scale-110 motion-safe:group-active/button:scale-90" />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setFilesOpen(true)}
            title="会话文件库"
            aria-label="会话文件库"
          >
            <Files className="size-4 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover/button:scale-110 motion-safe:group-active/button:scale-90" />
          </Button>
          <ConversationOutline conversationId={conv.id} />
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setAddOpen(true)}
            title="添加 Agent"
            aria-label="添加 Agent"
          >
            <UserRoundPlus className="size-4 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover/button:scale-110 motion-safe:group-active/button:scale-90" />
          </Button>
          <UsageBadge conversationId={conv.id} />
          <Badge variant={streamConnected ? 'default' : 'outline'} className="gap-1 px-1.5 text-[11px]">
            <span
              className={`size-1.5 rounded-full ${streamConnected ? 'bg-success' : 'bg-muted-foreground'}`}
            />
            {streamConnected ? '已连接' : '断开'}
          </Badge>
        </div>
        {/* 移动端：连接点 + ⋯ 更多（收纳文件树 / 产物库 / 文件库 / 加 Agent，避免 header 溢出） */}
        <div className="flex shrink-0 items-center gap-1.5 md:hidden">
          <span
            className={`size-2 rounded-full ${streamConnected ? 'bg-success' : 'bg-muted-foreground'}`}
            title={streamConnected ? '已连接' : '断开'}
          />
          <DropdownMenu>
            <DropdownMenuTrigger
              title="更多"
              aria-label="更多操作"
              className="group inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              <MoreHorizontal className="size-4 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover:scale-110 motion-safe:group-active:scale-90" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuItem onClick={() => setFileExplorerOpen(!fileExplorerOpen)}>
                <PanelRight className="size-4" />
                文件树
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setArtifactsOpen(true)}>
                <FileStack className="size-4" />
                本会话产物库
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setFilesOpen(true)}>
                <Files className="size-4" />
                会话文件库
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setAddOpen(true)}>
                <UserRoundPlus className="size-4" />
                添加 Agent
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Tab bar：仅在有打开的文件 / diff 时显示（避免单 chat tab 时浪费空间） */}
      {openFiles.length > 0 && (
        <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b bg-card/50 px-2 py-1 text-xs font-mono">
          <TabButton
            label="对话"
            active={activeTab === 'chat'}
            onClick={() => setActiveTab(conv.id, 'chat')}
          />
          {openFiles.map((tabId) => {
            if (isDiffTabId(tabId)) {
              const pw = pendingById.get(diffTabPendingId(tabId))
              const name = pw ? pw.path.split('/').pop() ?? pw.path : '已处理'
              return (
                <TabButton
                  key={tabId}
                  label={`diff: ${name}`}
                  tooltip={pw?.path}
                  icon={<FilePenLine className="size-3 text-primary" />}
                  active={activeTab === tabId}
                  onClick={() => setActiveTab(conv.id, tabId)}
                  onClose={() => closeFile(conv.id, tabId)}
                  highlight
                />
              )
            }
            return (
              <TabButton
                key={tabId}
                label={tabId.split('/').pop() ?? tabId}
                tooltip={tabId}
                active={activeTab === tabId}
                onClick={() => setActiveTab(conv.id, tabId)}
                onClose={() => closeFile(conv.id, tabId)}
              />
            )
          })}
        </div>
      )}

      {/* 主体：chat / file tab / pending diff tab */}
      {activeTab === 'chat' || !openFiles.includes(activeTab) ? (
        <>
          <PinnedMessagesBar conversationId={conv.id} />
          <WorkspaceEnvHintCard conversationId={conv.id} />
          <MessageList conversationId={conv.id} />
          <PendingBashCommandsPanel conversationId={conv.id} />
          <PendingMcpCallsPanel conversationId={conv.id} />
          <PendingWritesPanel conversationId={conv.id} />
          <MergeConflictPanel conversationId={conv.id} />
          <MessageInput conversationId={conv.id} handleFiles={handleFiles} uploading={uploading} />
        </>
      ) : isDiffTabId(activeTab) ? (
        <PendingWriteDiffTab conversationId={conv.id} pendingId={diffTabPendingId(activeTab)} />
      ) : (
        <FileTab conversationId={conv.id} relPath={activeTab} />
      )}

      <AddAgentDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        conversationId={conv.id}
        existingAgentIds={conv.agentIds}
      />

      <FileLibraryDialog
        open={filesOpen}
        onOpenChange={setFilesOpen}
        conversationId={conv.id}
      />

      <Dialog open={artifactsOpen} onOpenChange={setArtifactsOpen}>
        <DialogContent className="grid max-h-[min(680px,calc(100dvh-2rem))] max-w-md grid-rows-[auto_minmax(0,1fr)] overflow-hidden p-0">
          <DialogHeader className="border-b px-4 py-3">
            <DialogTitle className="flex items-center gap-2 text-sm">
              <FileStack className="size-4 text-muted-foreground" />
              会话产物
            </DialogTitle>
            <DialogDescription className="truncate text-xs" title={conv.title}>
              {conv.title}
            </DialogDescription>
          </DialogHeader>
          <ArtifactLibrary conversationId={conv.id} showConversationTitle={false} />
        </DialogContent>
      </Dialog>

      <AskUserQuestionDialog conversationId={conv.id} />
    </main>
  )
}

function ParticipantStack({ agents }: { agents: AgentRow[] }) {
  const visibleAgents = agents.slice(0, 3)
  const hiddenAgents = agents.slice(3)
  const title = agents.map((agent) => agent.name).join(' / ')

  return (
    <div className="flex shrink-0 -space-x-2 overflow-hidden pr-1" title={title}>
      {visibleAgents.map((agent) => (
        <AgentInfoPopover
          key={agent.id}
          agent={agent}
          size="sm"
          avatarClassName="border-2 border-background"
        />
      ))}
      {hiddenAgents.length > 0 && (
        <div
          className="flex size-7 shrink-0 items-center justify-center rounded-full border-2 border-background bg-muted text-[11px] font-semibold text-muted-foreground"
          title={hiddenAgents.map((agent) => agent.name).join(' / ')}
        >
          +{hiddenAgents.length}
        </div>
      )}
    </div>
  )
}

function TabButton({
  label,
  tooltip,
  icon,
  active,
  highlight,
  onClick,
  onClose,
}: {
  label: string
  tooltip?: string
  icon?: React.ReactNode
  active: boolean
  highlight?: boolean
  onClick: () => void
  onClose?: () => void
}) {
  return (
    <div
      title={tooltip}
      className={cn(
        'group flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1 transition',
        active
          ? highlight
            ? 'border-transparent text-primary shadow-[inset_0_-2px_0_0_var(--color-primary)]'
            : 'border-transparent text-primary shadow-[inset_0_-2px_0_0_var(--color-primary)]'
          : 'border-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      {icon}
      <button type="button" onClick={onClick} className="max-w-[180px] truncate">
        {label}
      </button>
      {onClose && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onClose()
          }}
          className="rounded p-0.5 opacity-50 transition hover:bg-accent hover:opacity-100"
          title="关闭"
          aria-label="关闭"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}
