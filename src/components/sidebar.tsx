'use client'

import { Archive, ArchiveRestore, Cable, ChevronDown, ChevronRight, Database, Ellipsis, Gauge, Library, LogOut, MessagesSquare, Package, PanelLeftClose, PanelLeftOpen, Pencil, Pin, PinOff, Plus, Search, Trash2, Users, Wrench, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { AgentLibrary } from '@/components/agent-library'
import { ConversationAvatar } from '@/components/agent-avatar'
import { GlobalSearchTrigger } from '@/components/global-search-trigger'
import { ArtifactLibrary } from '@/components/artifact-library'
import { KnowledgeSidebarNav } from '@/components/knowledge-library'
import { MemorySidebarNav } from '@/components/memory-library'
import { McpServerLibrary } from '@/components/mcp-server-library'
import { SkillLibrary } from '@/components/skill-library'
import { NewConversationDialog } from '@/components/new-conversation-dialog'
import { ProfileButton } from '@/components/profile-dialog'
import { SettingsButton } from '@/components/settings-dialog'
import { ThemeToggle } from '@/components/theme-toggle'
import { UsageDashboard } from '@/components/usage-dashboard'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  deleteConversation as deleteConversationAPI,
  fetchAgents,
  fetchConversations,
  renameConversation as renameConversationAPI,
  toggleArchiveConversation as toggleArchiveConversationAPI,
  togglePinConversation as togglePinConversationAPI,
  updateConversationSummary,
} from '@/lib/api'
import { subscribeUiCommand } from '@/lib/ui-command-events'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { cn } from '@/lib/utils'
import type { AgentRow, ConversationRow } from '@/db/schema'
import { useAppStore, useConversationList, useUnreadCount } from '@/stores/app-store'
import type { SidebarMode } from '@/stores/app-store'
import { useAuthStore } from '@/stores/auth-store'

export function Sidebar() {
  const mobileOpen = useAppStore((s) => s.mobileSidebarOpen)
  const setMobileSidebarOpen = useAppStore((s) => s.setMobileSidebarOpen)
  const conversations = useConversationList()
  const activeId = useAppStore((s) => s.activeConversationId)
  const setActive = useAppStore((s) => s.setActiveConversation)
  const setConversations = useAppStore((s) => s.setConversations)
  const setAgents = useAppStore((s) => s.setAgents)
  const agents = useAppStore((s) => s.agents)
  const removeConversation = useAppStore((s) => s.removeConversation)
  const upsertConversation = useAppStore((s) => s.upsertConversation)

  const mode = useAppStore((s) => s.sidebarMode)
  const setMode = useAppStore((s) => s.setSidebarMode)
  const logout = useAuthStore((s) => s.logout)
  const user = useAuthStore((s) => s.user)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [editingSummaryId, setEditingSummaryId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)

  const activeConversations = useMemo(
    () => conversations.filter((c) => !c.archived),
    [conversations],
  )
  const archivedConversations = useMemo(
    () => conversations.filter((c) => c.archived),
    [conversations],
  )

  const filteredConversations = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return activeConversations
    return activeConversations.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        (c.summary && c.summary.toLowerCase().includes(q)),
    )
  }, [activeConversations, search])

  const handleTogglePin = async (convId: string) => {
    try {
      const updated = await togglePinConversationAPI(convId)
      upsertConversation(updated)
    } catch (err) {
      console.error('[Sidebar] toggle pin failed', err)
    }
  }

  const handleToggleArchive = async (convId: string) => {
    try {
      const updated = await toggleArchiveConversationAPI(convId)
      upsertConversation(updated)
    } catch (err) {
      console.error('[Sidebar] toggle archive failed', err)
    }
  }

  const finishRename = async (convId: string, currentTitle: string, next: string) => {
    const trimmed = next.trim()
    setRenamingId(null)
    if (!trimmed || trimmed === currentTitle) return
    try {
      const updated = await renameConversationAPI(convId, trimmed)
      upsertConversation(updated)
    } catch (err) {
      console.error('[Sidebar] rename failed', err)
    }
  }

  const finishSummaryEdit = async (convId: string, next: string) => {
    const trimmed = next.trim() || null
    setEditingSummaryId(null)
    try {
      const updated = await updateConversationSummary(convId, trimmed)
      upsertConversation(updated)
    } catch (err) {
      console.error('[Sidebar] summary edit failed', err)
    }
  }

  useEffect(() => {
    fetchConversations().then(setConversations).catch(console.error)
    fetchAgents().then(setAgents).catch(console.error)
  }, [setConversations, setAgents])

  // Guide agent side-effect: refresh agents/conversations when guide does management ops
  useGuideSideEffectRefresh('agents', () => { fetchAgents().then(setAgents).catch(console.error) })
  useGuideSideEffectRefresh('conversations', () => { fetchConversations().then(setConversations).catch(console.error) })

  useEffect(() => {
    return subscribeUiCommand((command) => {
      if (command !== 'open-agents') return
      setCollapsed(false)
      setMode('agents')
      if (window.matchMedia('(max-width: 767px)').matches) {
        setMobileSidebarOpen(true)
      }
    })
  }, [setMobileSidebarOpen])

  const deleteTarget = deleteTargetId ? conversations.find((c) => c.id === deleteTargetId) : null

  const confirmDelete = async () => {
    if (!deleteTargetId) return
    setDeleting(true)
    try {
      await deleteConversationAPI(deleteTargetId)
      removeConversation(deleteTargetId)
      setDeleteTargetId(null)
    } catch (err) {
      console.error('[Sidebar] delete failed', err)
    } finally {
      setDeleting(false)
    }
  }

  const pickMode = (m: SidebarMode) => {
    // 移动端：点轨上图标弹出对应面板；再点当前项则收起。桌面端 mobileOpen 不影响布局，无副作用
    if (mobileOpen && mode === m) {
      setMobileSidebarOpen(false)
      return
    }
    setMode(m)
    setMobileSidebarOpen(true)
  }

  return (
    <>
      {/* 移动端遮罩 —— 内容面板弹出时点击关闭（图标轨常驻，不被遮罩盖住） */}
      {mobileOpen && (
        <div
          className="fixed inset-0 left-14 z-30 bg-foreground/20 md:hidden"
          onClick={() => setMobileSidebarOpen(false)}
        />
      )}
      {/* 外层容器：桌面端 flex 常驻；移动端解散为 contents，让图标轨与内容列各自定位 */}
      <div className="flex shrink-0 overflow-hidden border-r bg-card max-md:contents">
        {/* 图标轨：桌面端 flex 内联；移动端固定左侧常驻（不再随抽屉整体隐藏） */}
        <nav className="flex w-14 shrink-0 flex-col items-center gap-1 border-r bg-card px-1 py-2 max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40">
          <RailButton mode={mode} self="conversations" onClick={() => pickMode('conversations')} icon={<MessagesSquare className="size-5" />} label="对话" />
          <RailButton mode={mode} self="artifacts" onClick={() => pickMode('artifacts')} icon={<Package className="size-5" />} label="产物库" />
          <RailButton mode={mode} self="agents" onClick={() => pickMode('agents')} icon={<Users className="size-5" />} label="Agents" />
          <span className="my-1 h-px w-6 shrink-0 bg-border" aria-hidden="true" />
          <RailButton mode={mode} self="analytics" onClick={() => pickMode('analytics')} icon={<Gauge className="size-5" />} label="分析" />
          <RailButton mode={mode} self="knowledge" onClick={() => pickMode('knowledge')} icon={<Library className="size-5" />} label="知识库" />
          <RailButton mode={mode} self="skills" onClick={() => pickMode('skills')} icon={<Wrench className="size-5" />} label="技能" />
          <RailButton mode={mode} self="mcp" onClick={() => pickMode('mcp')} icon={<Cable className="size-5" />} label="MCP" />
          <RailButton mode={mode} self="memory" onClick={() => pickMode('memory')} icon={<Database className="size-5" />} label="记忆管理" />
          <div className="mt-auto flex flex-col items-center gap-1">
            <ProfileButton />
            <SettingsButton />
            <ThemeToggle />
            <Button
              size="icon"
              variant="ghost"
              className="group"
              onClick={() => void logout()}
              title={user ? `退出登录 (${user.email})` : '退出登录'}
              aria-label="退出登录"
            >
              <LogOut className="size-5 motion-safe:transition-transform motion-safe:duration-200 motion-safe:group-hover:-translate-x-0.5 motion-safe:group-active:scale-90" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="group max-md:hidden"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              title={collapsed ? '展开' : '收起'}
            >
              {/* hover 时向「即将移动的方向」轻推：收起态推右（要展开），展开态推左（要收起）*/}
              <span
                className={cn(
                  'inline-flex motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-active:scale-90',
                  collapsed
                    ? 'motion-safe:group-hover:translate-x-0.5'
                    : 'motion-safe:group-hover:-translate-x-0.5',
                )}
              >
                {collapsed ? <PanelLeftOpen className="size-5" /> : <PanelLeftClose className="size-5" />}
              </span>
            </Button>
          </div>
        </nav>

        {/* 上下文栏：AChat 标题 + 按 mode 分发的内容 */}
        {!collapsed && (
          <div
            className={cn(
              'flex w-60 shrink-0 flex-col overflow-hidden bg-card',
              'max-md:fixed max-md:inset-y-0 max-md:left-14 max-md:z-40 max-md:w-[min(20rem,calc(100vw-3.5rem))] max-md:border-r max-md:shadow-xl max-md:transition-transform max-md:duration-200',
              mobileOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-[calc(100%+3.5rem)]',
            )}
          >
            {/* AChat 标题（移动端省略，节省竖向空间；图标轨已提供导航） */}
            <div className="flex shrink-0 items-center border-b px-4 py-3 max-md:hidden">
              <div className="min-w-0">
                <h1 className="truncate text-base font-semibold">AChat</h1>
              </div>
            </div>

            {mode === 'conversations' ? (
              <>
                {/* New conversation button */}
                <div className="shrink-0 px-3 pt-3">
                  <Button
                    className="w-full justify-start gap-2"
                    variant="outline"
                    onClick={() => setDialogOpen(true)}
                  >
                    <Plus className="size-4" />
                    新建对话
                  </Button>
                </div>

                {/* Search box */}
                {activeConversations.length > 0 && (
                  <div className="shrink-0 flex items-center gap-2 px-3 pt-2 pb-2">
                    <div className="relative flex-1">
                      <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                      <input
                        type="text"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="搜索会话…"
                        className="w-full rounded-md border bg-background py-1.5 pl-7 pr-7 text-xs outline-none transition focus:border-foreground/30"
                      />
                      {search && (
                        <button
                          type="button"
                          onClick={() => setSearch('')}
                          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                          title="清除"
                        >
                          <X className="size-3" />
                        </button>
                      )}
                    </div>
                    <GlobalSearchTrigger />
                  </div>
                )}

                {/* Conversation list */}
                <ScrollArea className="min-h-0 flex-1">
                  <div className="space-y-1 p-2">
                    {filteredConversations.length === 0 ? (
                      <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                        {search.trim() ? `没有匹配「${search.trim()}」的会话` : '没有会话'}
                      </div>
                    ) : (
                      filteredConversations.map((c) => {
                        const convAgents = c.agentIds.map((id) => agents[id]).filter(Boolean)
                        return (
                          <ConversationItem
                            key={c.id}
                            conversation={c}
                            agents={convAgents}
                            isActive={activeId === c.id}
                            isRenaming={renamingId === c.id}
                            isEditingSummary={editingSummaryId === c.id}
                            onActivate={() => setActive(c.id)}
                            onTogglePin={() => void handleTogglePin(c.id)}
                            onToggleArchive={() => void handleToggleArchive(c.id)}
                            onStartRename={() => setRenamingId(c.id)}
                            onFinishRename={(next) => void finishRename(c.id, c.title, next)}
                            onStartEditSummary={() => setEditingSummaryId(c.id)}
                            onFinishEditSummary={(next) => void finishSummaryEdit(c.id, next)}
                            onRequestDelete={() => setDeleteTargetId(c.id)}
                          />
                        )
                      })
                    )}
                  </div>

                  {/* 已归档区：可折叠，展开后每项可取消归档 */}
                  {archivedConversations.length > 0 && (
                    <div className="border-t p-2">
                      <button
                        type="button"
                        onClick={() => setShowArchived((v) => !v)}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground"
                      >
                        {showArchived ? (
                          <ChevronDown className="size-3.5" />
                        ) : (
                          <ChevronRight className="size-3.5" />
                        )}
                        <Archive className="size-3.5" />
                        <span>已归档</span>
                        <span className="ml-auto tabular-nums">{archivedConversations.length}</span>
                      </button>
                      {showArchived && (
                        <div className="mt-1 space-y-1">
                          {archivedConversations.map((c) => {
                            const convAgents = c.agentIds.map((id) => agents[id]).filter(Boolean)
                            return (
                              <ConversationItem
                                key={c.id}
                                conversation={c}
                                agents={convAgents}
                                isActive={activeId === c.id}
                                isRenaming={renamingId === c.id}
                                isArchived
                                isEditingSummary={editingSummaryId === c.id}
                                onActivate={() => setActive(c.id)}
                                onTogglePin={() => void handleTogglePin(c.id)}
                                onToggleArchive={() => void handleToggleArchive(c.id)}
                                onStartRename={() => setRenamingId(c.id)}
                                onFinishRename={(next) => void finishRename(c.id, c.title, next)}
                                onStartEditSummary={() => setEditingSummaryId(c.id)}
                                onFinishEditSummary={(next) => void finishSummaryEdit(c.id, next)}
                                onRequestDelete={() => setDeleteTargetId(c.id)}
                              />
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </ScrollArea>
              </>
            ) : mode === 'artifacts' ? (
              <ArtifactLibrary />
            ) : mode === 'agents' ? (
              <AgentLibrary />
) : mode === 'knowledge' ? (
              <KnowledgeSidebarNav />
) : mode === 'skills' ? (
              <SkillLibrary />
            ) : mode === 'mcp' ? (
              <McpServerLibrary />
            ) : mode === 'memory' ? (
              <MemorySidebarNav />
            ) : (
              <UsageDashboard />
            )}
          </div>
        )}
      </div>

      <NewConversationDialog open={dialogOpen} onOpenChange={setDialogOpen} />

      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除会话</DialogTitle>
            <DialogDescription>
              确定要删除「{deleteTarget?.title}」吗？该会话的所有消息、产物和工作区都会一并清除，无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTargetId(null)}>
              取消
            </Button>
            <Button
              variant="default"
              className="bg-destructive hover:bg-destructive/90"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {deleting ? '删除中...' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function ConversationItem({
  conversation,
  agents,
  isActive,
  isRenaming,
  isArchived = false,
  isEditingSummary = false,
  onActivate,
  onTogglePin,
  onToggleArchive,
  onStartRename,
  onFinishRename,
  onStartEditSummary,
  onFinishEditSummary,
  onRequestDelete,
}: {
  conversation: ConversationRow
  agents: AgentRow[]
  isActive: boolean
  isRenaming: boolean
  isArchived?: boolean
  isEditingSummary?: boolean
  onActivate: () => void
  onTogglePin: () => void
  onToggleArchive: () => void
  onStartRename: () => void
  onFinishRename: (next: string) => void | Promise<void>
  onStartEditSummary: () => void
  onFinishEditSummary: (next: string) => void | Promise<void>
  onRequestDelete: () => void
}) {
  const isPinned = !!conversation.pinnedAt
  const unread = useUnreadCount(conversation.id)
  return (
    <div
      className={cn(
        'group flex w-full items-center gap-3 rounded-md px-2 py-2.5 transition hover:bg-accent',
        isActive && 'border-l-2 border-primary bg-transparent',
        isPinned && 'bg-warning/10',
      )}
    >
      <button
        type="button"
        onClick={onActivate}
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
        disabled={isRenaming}
      >
        <div className="relative">
          <ConversationAvatar
            agents={agents}
            isGroup={conversation.mode === 'group'}
            size="lg"
          />
          {unread > 0 && !isActive && (
            <span className="absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium leading-none text-white">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          {isRenaming ? (
            <RenameInput
              key={conversation.id}
              initial={conversation.title}
              onCommit={(next) => onFinishRename(next)}
              onCancel={() => onFinishRename(conversation.title)}
            />
          ) : (
            <div className="flex items-center gap-1">
              {isPinned && <Pin className="size-3 shrink-0 fill-warning text-warning" />}
              <div className="truncate text-sm font-medium">{conversation.title}</div>
            </div>
          )}
          {isEditingSummary ? (
            <RenameInput
              key={`summary-${conversation.id}`}
              initial={conversation.summary ?? ''}
              onCommit={(next) => onFinishEditSummary(next)}
              onCancel={() => onFinishEditSummary(conversation.summary ?? '')}
            />
          ) : conversation.summary ? (
            <div className="group/summary flex items-center gap-1">
              <div className="line-clamp-2 text-xs leading-tight text-muted-foreground">
                {conversation.summary}
              </div>
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation()
                  onStartEditSummary()
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    e.stopPropagation()
                    onStartEditSummary()
                  }
                }}
                title="编辑摘要"
                className="shrink-0 cursor-pointer rounded p-0.5 opacity-0 transition group-hover/summary:opacity-100 max-md:opacity-100 hover:bg-accent hover:text-foreground"
              >
                <Pencil className="size-3" />
              </span>
            </div>
          ) : null}
        </div>
      </button>
      {!isRenaming && !isEditingSummary && (
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            title="更多操作"
            className="shrink-0 cursor-pointer rounded p-0.5 opacity-0 transition group-hover:opacity-100 max-md:opacity-100 hover:bg-accent hover:text-foreground"
          >
            <Ellipsis className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-36">
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation()
                onTogglePin()
              }}
            >
              {isPinned ? (
                <>
                  <PinOff className="size-4" />
                  <span>取消置顶</span>
                </>
              ) : (
                <>
                  <Pin className="size-4" />
                  <span>置顶</span>
                </>
              )}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation()
                onToggleArchive()
              }}
            >
              {isArchived ? (
                <>
                  <ArchiveRestore className="size-4" />
                  <span>取消归档</span>
                </>
              ) : (
                <>
                  <Archive className="size-4" />
                  <span>归档</span>
                </>
              )}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation()
                onStartRename()
              }}
            >
              <Pencil className="size-4" />
              <span>重命名</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={(e) => {
                e.stopPropagation()
                onRequestDelete()
              }}
            >
              <Trash2 className="size-4" />
              <span>删除会话</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

function RenameInput({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string
  onCommit: (next: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  return (
    <input
      ref={ref}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onBlur={() => onCommit(draft)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          onCommit(draft)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          onCancel()
        }
      }}
      maxLength={100}
      className="w-full rounded border border-primary/40 bg-background px-1.5 py-0.5 text-sm font-medium outline-none ring-2 ring-primary/30"
    />
  )
}

function RailButton({
  mode,
  self,
  onClick,
  icon,
  label,
}: {
  mode: SidebarMode
  self: SidebarMode
  onClick: () => void
  icon: React.ReactNode
  label: string
}) {
  const active = mode === self
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        'group relative flex size-10 items-center justify-center rounded-md transition',
        active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      {/* active 锚定：2px 主色左色条 */}
      {active && (
        <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-primary" />
      )}
      <span className="inline-flex motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover:scale-110 motion-safe:group-active:scale-90">
        {icon}
      </span>
    </button>
  )
}
