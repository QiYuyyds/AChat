'use client'

import {
  Cable,
  ChevronDown,
  Globe,
  Plug,
  Puzzle,
  Search,
  Sparkles,
  Terminal,
  Trash2,
  Upload,
  Wrench,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  deleteMcpServer,
  deleteSkill,
  fetchMcpServers,
  listSkills,
  testMcpServer,
  updateMcpServer,
  uploadSkill,
  type McpServerResponse,
  type McpTestResult,
  type SkillSummary,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'

import { McpDetailDialog } from '@/components/mcp-detail-dialog'
import { McpServerEditDialog } from '@/components/mcp-server-edit-dialog'
import { SkillDetailDialog } from '@/components/skill-detail-dialog'

// ─── Tab Config ──────────────────────────────────────────────────

type ExtensionTab = 'extensions' | 'skills'

const TABS: { value: ExtensionTab; label: string; icon: typeof Plug }[] = [
  { value: 'extensions', label: '扩展', icon: Plug },
  { value: 'skills', label: '技能', icon: Wrench },
]

// ─── Transport Meta ──────────────────────────────────────────────

interface TransportMeta {
  label: string
  icon: typeof Terminal
  iconBg: string
  iconText: string
}

const TRANSPORT_META: Record<string, TransportMeta> = {
  stdio: {
    label: 'stdio',
    icon: Terminal,
    iconBg: 'bg-violet-500/10',
    iconText: 'text-violet-500',
  },
  sse: {
    label: 'SSE',
    icon: Globe,
    iconBg: 'bg-cyan-500/10',
    iconText: 'text-cyan-500',
  },
  streamable_http: {
    label: 'HTTP',
    icon: Globe,
    iconBg: 'bg-cyan-500/10',
    iconText: 'text-cyan-500',
  },
}

function getTransportMeta(transport: string): TransportMeta {
  return TRANSPORT_META[transport] ?? {
    label: transport,
    icon: Cable,
    iconBg: 'bg-blue-500/10',
    iconText: 'text-blue-500',
  }
}

// ─── Main Component ──────────────────────────────────────────────

export function ExtensionMainPanel() {
  const [activeTab, setActiveTab] = useState<ExtensionTab>('extensions')

  // Data states
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [mcpServers, setMcpServers] = useState<McpServerResponse[]>([])
  const [loading, setLoading] = useState(true)

  // Search
  const [searchQuery, setSearchQuery] = useState('')

  // Dialog states
  const [selectedSkill, setSelectedSkill] = useState<SkillSummary | null>(null)
  const [selectedMcp, setSelectedMcp] = useState<McpServerResponse | null>(null)
  const [mcpEditOpen, setMcpEditOpen] = useState(false)
  const [editingMcp, setEditingMcp] = useState<McpServerResponse | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Test results
  const [testResults, setTestResults] = useState<Record<string, McpTestResult | undefined>>({})
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set())

  // Refresh data
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [skillsData, mcpData] = await Promise.all([listSkills(), fetchMcpServers()])
      setSkills(skillsData)
      setMcpServers(mcpData)
    } catch (err) {
      console.error('[ExtensionMainPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useGuideSideEffectRefresh('skills', () => { void refresh() })
  useGuideSideEffectRefresh('mcp', () => { void refresh() })

  // Filtered data
  const filteredSkills = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.slug.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    )
  }, [skills, searchQuery])

  const filteredMcpServers = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return mcpServers
    return mcpServers.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.transport.toLowerCase().includes(q) ||
        (s.command ?? '').toLowerCase().includes(q) ||
        (s.url ?? '').toLowerCase().includes(q),
    )
  }, [mcpServers, searchQuery])

  // Handlers
  const handleDeleteSkill = async (slug: string) => {
    try {
      await deleteSkill(slug)
      setSkills((prev) => prev.filter((s) => s.slug !== slug))
      if (selectedSkill?.slug === slug) setSelectedSkill(null)
    } catch (err) {
      console.error('[ExtensionMainPanel] delete skill failed', err)
    }
  }

  const handleDeleteMcp = async (id: string) => {
    try {
      await deleteMcpServer(id)
      setMcpServers((prev) => prev.filter((s) => s.id !== id))
      if (selectedMcp?.id === id) setSelectedMcp(null)
    } catch (err) {
      console.error('[ExtensionMainPanel] delete mcp failed', err)
    }
  }

  const handleTestMcp = async (server: McpServerResponse) => {
    setTestingIds((prev) => new Set(prev).add(server.id))
    setTestResults((prev) => ({ ...prev, [server.id]: undefined }))
    try {
      const result = await testMcpServer(server.id)
      setTestResults((prev) => ({ ...prev, [server.id]: result }))
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [server.id]: { ok: false, tools: [], error: err instanceof Error ? err.message : String(err) },
      }))
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev)
        next.delete(server.id)
        return next
      })
    }
  }

  const handleToggleEnabled = async (server: McpServerResponse) => {
    try {
      const updated = await updateMcpServer(server.id, { enabled: !server.enabled })
      setMcpServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
      if (selectedMcp?.id === server.id) {
        setSelectedMcp(updated)
      }
    } catch (err) {
      console.error('[ExtensionMainPanel] toggle enabled failed', err)
    }
  }

  // Skill upload handlers
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  const handleUploadSkill = useCallback(
    async (files: File[], paths: string[]) => {
      if (files.length === 0) return
      setUploading(true)
      try {
        await uploadSkill(files, paths)
        await refresh()
      } catch (err) {
        console.error('[ExtensionMainPanel] upload skill failed', err)
      } finally {
        setUploading(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [refresh],
  )

  const handleFileSelect = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const files = Array.from(fileList)
      void handleUploadSkill(files, files.map((f) => f.webkitRelativePath || f.name))
    },
    [handleUploadSkill],
  )

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const items = Array.from(e.dataTransfer.items)
        .map((it) => it.webkitGetAsEntry?.())
        .filter((x): x is FileSystemEntry => Boolean(x))
      if (items.length === 0) {
        handleFileSelect(e.dataTransfer.files)
        return
      }
      const collected: { file: File; path: string }[] = []
      for (const entry of items) await collectEntry(entry, '', collected)
      void handleUploadSkill(collected.map((c) => c.file), collected.map((c) => c.path))
    },
    [handleFileSelect, handleUploadSkill],
  )

  const openCreateMcp = () => {
    setEditingMcp(null)
    setMcpEditOpen(true)
  }

  const openEditMcp = (server: McpServerResponse) => {
    setEditingMcp(server)
    setMcpEditOpen(true)
  }

  const handleMcpEditClose = (open: boolean) => {
    setMcpEditOpen(open)
    if (!open) {
      setEditingMcp(null)
      void refresh()
    }
  }

  const activeIndex = TABS.findIndex((t) => t.value === activeTab)
  const totalCount = activeTab === 'extensions' ? filteredMcpServers.length : filteredSkills.length

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Ambient glow */}
      <div className="pointer-events-none absolute -top-20 right-0 size-64 rounded-full bg-primary/5 blur-3xl extension-ambient" />

      {/* Header with segmented tabs and action button */}
      <header className="extension-fade-up relative shrink-0 border-b border-border px-6 py-4">
        <div className="flex items-center justify-between">
          {/* Segmented tab switcher */}
          <div className="relative flex w-fit items-center rounded-lg bg-muted p-0.5">
            {/* Sliding indicator */}
            <span
              className="pointer-events-none absolute top-0.5 bottom-0.5 left-0.5 w-[calc(50%-2px)] rounded-md bg-background shadow-[var(--shadow-sm),var(--inset-hi)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={{ transform: `translateX(${activeIndex * 100}%)` }}
            />
            {TABS.map((t) => {
              const Icon = t.icon
              const isActive = t.value === activeTab
              return (
                <button
                  key={t.value}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActiveTab(t.value)}
                  className={cn(
                    'relative z-10 inline-flex h-8 w-[6.5rem] items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors duration-200',
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

          {/* Count + Action */}
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">
              {loading ? '加载中…' : `${totalCount} 项`}
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button size="sm" className="gap-1.5 text-xs" />}>
                {activeTab === 'extensions' ? <Cable className="size-3.5" /> : <Upload className="size-3.5" />}
                {activeTab === 'extensions' ? '添加' : '上传'}
                <ChevronDown className="size-3" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {activeTab === 'extensions' ? (
                  <DropdownMenuItem onClick={openCreateMcp}>添加 MCP Server</DropdownMenuItem>
                ) : (
                  <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>上传 SKILL.md</DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-6">
          {activeTab === 'extensions' ? (
            <ExtensionsTabContent
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              servers={filteredMcpServers}
              loading={loading}
              onSelect={setSelectedMcp}
              onEdit={openEditMcp}
              onDelete={handleDeleteMcp}
              onTest={handleTestMcp}
              onToggleEnabled={handleToggleEnabled}
              testResults={testResults}
              testingIds={testingIds}
            />
          ) : (
            <SkillsTabContent
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              skills={filteredSkills}
              loading={loading}
              onSelect={setSelectedSkill}
              onDelete={handleDeleteSkill}
              dragOver={dragOver}
              setDragOver={setDragOver}
              onDrop={handleDrop}
              uploading={uploading}
            />
          )}
        </div>
      </ScrollArea>

      {/* Hidden file input for skill upload */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        // @ts-expect-error webkitdirectory is a non-standard attribute
        webkitdirectory=""
        className="hidden"
        onChange={(e) => handleFileSelect(e.target.files)}
      />

      {/* Detail Dialogs */}
      {selectedMcp && (
        <McpDetailDialog
          server={selectedMcp}
          open={!!selectedMcp}
          onOpenChange={(open: boolean) => !open && setSelectedMcp(null)}
          testResult={testResults[selectedMcp.id]}
          onTest={() => handleTestMcp(selectedMcp)}
          onEdit={() => openEditMcp(selectedMcp)}
          onDelete={() => handleDeleteMcp(selectedMcp.id)}
          onToggleEnabled={() => handleToggleEnabled(selectedMcp)}
        />
      )}

      {selectedSkill && (
        <SkillDetailDialog
          skill={selectedSkill}
          open={!!selectedSkill}
          onOpenChange={(open: boolean) => !open && setSelectedSkill(null)}
          onDelete={() => handleDeleteSkill(selectedSkill.slug)}
        />
      )}

      {/* MCP Edit Dialog */}
      <McpServerEditDialog open={mcpEditOpen} onOpenChange={handleMcpEditClose} server={editingMcp} />
    </div>
  )
}

// ─── Extensions Tab Content ──────────────────────────────────────

function ExtensionsTabContent({
  searchQuery,
  setSearchQuery,
  servers,
  loading,
  onSelect,
  onEdit,
  onDelete,
  onTest,
  onToggleEnabled,
  testResults,
  testingIds,
}: {
  searchQuery: string
  setSearchQuery: (q: string) => void
  servers: McpServerResponse[]
  loading: boolean
  onSelect: (s: McpServerResponse) => void
  onEdit: (s: McpServerResponse) => void
  onDelete: (id: string) => void
  onTest: (s: McpServerResponse) => void
  onToggleEnabled: (s: McpServerResponse) => void
  testResults: Record<string, McpTestResult | undefined>
  testingIds: Set<string>
}) {
  const enabledCount = servers.filter((s) => s.enabled).length

  return (
    <div className="space-y-5">
      {/* Title row */}
      <div className="extension-fade-up flex items-end justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">MCP Server</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            管理外部工具连接，为 Agent 提供可调用能力
          </p>
        </div>
        {servers.length > 0 && (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-full bg-success" />
              {enabledCount} 启用
            </span>
            <span className="text-border">/</span>
            <span>{servers.length} 总计</span>
          </div>
        )}
      </div>

      {/* Search */}
      <div className="extension-fade-up extension-fade-up-delay-1 relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索名称、传输方式或命令…"
          className="h-9 pl-10 transition-shadow focus-visible:ring-primary/20"
        />
      </div>

      {/* Grid */}
      {loading && servers.length === 0 ? (
        <LoadingState />
      ) : servers.length === 0 ? (
        <EmptyState
          icon={Plug}
          title="还没有 MCP Server"
          description="点击右上角「添加」连接外部工具"
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {servers.map((server, idx) => {
            const animDelay = Math.min(idx, 7)
            const animClass = `extension-fade-up${animDelay > 0 ? `-delay-${animDelay}` : ''}`
            return (
              <McpServerCard
                key={server.id}
                server={server}
                animClass={animClass}
                onClick={() => onSelect(server)}
                onEdit={() => onEdit(server)}
                onDelete={() => onDelete(server.id)}
                onTest={() => onTest(server)}
                onToggleEnabled={() => onToggleEnabled(server)}
                testing={testingIds.has(server.id)}
                testResult={testResults[server.id]}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Skills Tab Content ──────────────────────────────────────────

function SkillsTabContent({
  searchQuery,
  setSearchQuery,
  skills,
  loading,
  onSelect,
  onDelete,
  dragOver,
  setDragOver,
  onDrop,
  uploading,
}: {
  searchQuery: string
  setSearchQuery: (q: string) => void
  skills: SkillSummary[]
  loading: boolean
  onSelect: (s: SkillSummary) => void
  onDelete: (slug: string) => void
  dragOver: boolean
  setDragOver: (v: boolean) => void
  onDrop: (e: React.DragEvent) => void
  uploading: boolean
}) {
  return (
    <div className="space-y-5">
      {/* Title row */}
      <div className="extension-fade-up">
        <h2 className="text-xl font-semibold tracking-tight">技能</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          给 Agent 的指令增强和能力说明
        </p>
      </div>

      {/* Search */}
      <div className="extension-fade-up extension-fade-up-delay-1 relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索名称、标识或描述…"
          className="h-9 pl-10 transition-shadow focus-visible:ring-primary/20"
        />
      </div>

      {/* Drop Zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          'extension-fade-up extension-fade-up-delay-2 relative overflow-hidden rounded-xl border-2 border-dashed px-6 py-6 text-center transition-all duration-300',
          dragOver
            ? 'border-primary bg-primary/5 scale-[1.01]'
            : 'border-border/50 hover:border-border hover:bg-muted/30',
          uploading && 'pointer-events-none opacity-50',
        )}
      >
        {/* Decorative gradient on drag */}
        {dragOver && (
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-primary/5" />
        )}
        <div className="relative">
          {uploading ? (
            <div className="flex items-center justify-center gap-2 text-muted-foreground">
              <div className="size-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="text-sm">上传中…</span>
            </div>
          ) : (
            <>
              <div
                className={cn(
                  'mx-auto flex size-10 items-center justify-center rounded-xl transition-colors',
                  dragOver ? 'bg-primary/10 text-primary' : 'bg-muted/60 text-muted-foreground',
                )}
              >
                <Upload className="size-5" />
              </div>
              <p className="mt-2 text-sm font-medium text-foreground">
                拖放 SKILL.md 文件或文件夹到此处
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                支持单个文件、多个文件或整个文件夹
              </p>
            </>
          )}
        </div>
      </div>

      {/* Grid */}
      {loading && skills.length === 0 ? (
        <LoadingState />
      ) : skills.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="还没有技能"
          description="拖放 SKILL.md 文件或点击右上角「上传」添加"
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {skills.map((skill, idx) => {
            const animDelay = Math.min(idx, 7)
            const animClass = `extension-fade-up${animDelay > 0 ? `-delay-${animDelay}` : ''}`
            return (
              <SkillCard
                key={skill.slug}
                skill={skill}
                animClass={animClass}
                onClick={() => onSelect(skill)}
                onDelete={() => onDelete(skill.slug)}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── MCP Server Card ─────────────────────────────────────────────

function McpServerCard({
  server,
  animClass,
  onClick,
  onEdit,
  onDelete,
  onTest,
  onToggleEnabled,
  testing,
  testResult,
}: {
  server: McpServerResponse
  animClass: string
  onClick: () => void
  onEdit: () => void
  onDelete: () => void
  onTest: () => void
  onToggleEnabled: () => void
  testing: boolean
  testResult?: McpTestResult
}) {
  const transportMeta = getTransportMeta(server.transport)
  const TransportIcon = transportMeta.icon

  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-xl border bg-card p-4 transition-all duration-300',
        'hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md',
        animClass,
      )}
    >
      {/* Status indicator bar */}
      <div
        className={cn(
          'absolute left-0 top-4 bottom-4 w-0.5 rounded-r-full transition-colors',
          server.enabled ? 'bg-success' : 'bg-transparent',
        )}
      />

      {/* Header */}
      <div className="flex items-start gap-3 pl-1.5">
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-lg transition-transform duration-300 group-hover:scale-110',
            transportMeta.iconBg,
          )}
        >
          <TransportIcon className={cn('size-4.5', transportMeta.iconText)} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h4 className="truncate text-sm font-medium" title={server.name}>
              {server.name}
            </h4>
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <span
              className={cn(
                'inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-medium',
                transportMeta.iconBg,
                transportMeta.iconText,
              )}
            >
              {transportMeta.label}
            </span>
            <span className="text-border">·</span>
            <span className={cn('inline-flex items-center gap-0.5', server.enabled ? 'text-success' : 'text-muted-foreground')}>
              <span className={cn('size-1.5 rounded-full', server.enabled ? 'bg-success' : 'bg-muted-foreground/40')} />
              {server.enabled ? '已启用' : '已禁用'}
            </span>
            {server.trust === 'always' && (
              <>
                <span className="text-border">·</span>
                <span className="inline-flex items-center gap-0.5 text-warning">
                  <Zap className="size-2.5" />
                  始终信任
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Connection info */}
      <div className="mt-2.5 pl-1.5">
        <code className="block truncate font-mono text-[10px] text-muted-foreground">
          {server.transport === 'stdio'
            ? `${server.command ?? ''} ${(server.args ?? []).join(' ')}`
            : server.url ?? ''}
        </code>
      </div>

      {/* Test result preview */}
      {testResult && (
        <div
          className={cn(
            'mt-2.5 ml-1.5 rounded-md border px-2 py-1 text-[10px] font-medium',
            testResult.ok
              ? 'border-success/30 bg-success/5 text-success'
              : 'border-destructive/30 bg-destructive/5 text-destructive',
          )}
        >
          {testResult.ok ? `${testResult.tools.length} 个工具可用` : '连接失败'}
        </div>
      )}

      {/* Hover actions */}
      <div className="absolute right-2 top-2 z-10 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          onClick={(e) => {
            e.stopPropagation()
            onToggleEnabled()
          }}
          className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground"
          title={server.enabled ? '禁用' : '启用'}
        >
          <span
            className={cn(
              'relative inline-flex h-3.5 w-6 items-center rounded-full transition-colors',
              server.enabled ? 'bg-primary' : 'bg-muted-foreground/30',
            )}
          >
            <span
              className={cn(
                'inline-block size-3 rounded-full bg-white transition-transform',
                server.enabled ? 'translate-x-3' : 'translate-x-0.5',
              )}
            />
          </span>
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onTest()
          }}
          disabled={testing}
          className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground"
          title="测试连接"
        >
          {testing ? (
            <div className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          ) : (
            <Plug className="size-3.5" />
          )}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
          className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground"
          title="编辑"
        >
          <Puzzle className="size-3.5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
          title="删除"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  )
}

// ─── Skill Card ──────────────────────────────────────────────────

function SkillCard({
  skill,
  animClass,
  onClick,
  onDelete,
}: {
  skill: SkillSummary
  animClass: string
  onClick: () => void
  onDelete: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-xl border bg-card p-4 transition-all duration-300',
        'hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md',
        animClass,
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-amber-500 transition-transform duration-300 group-hover:scale-110">
          <Sparkles className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-sm font-medium" title={skill.name}>
            {skill.name}
          </h4>
          <code className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
            {skill.slug}
          </code>
        </div>
      </div>

      {/* Description */}
      {skill.description && (
        <p className="mt-2.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
          {skill.description}
        </p>
      )}

      {/* Trigger keywords */}
      {skill.triggerKeywords && skill.triggerKeywords.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1">
          {skill.triggerKeywords.slice(0, 3).map((kw) => (
            <span
              key={kw}
              className="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-600 dark:text-amber-400"
            >
              {kw}
            </span>
          ))}
          {skill.triggerKeywords.length > 3 && (
            <span className="text-[9px] text-muted-foreground">
              +{skill.triggerKeywords.length - 3}
            </span>
          )}
        </div>
      )}

      {/* Hover actions */}
      <div className="absolute right-2 top-2 z-10 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          className="rounded p-1 text-muted-foreground transition hover:bg-accent hover:text-destructive"
          title="删除"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  )
}

// ─── Loading State ───────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col rounded-xl border bg-card p-4"
          style={{ animationDelay: `${i * 60}ms` }}
        >
          <div className="flex items-start gap-3">
            <div className="size-9 shrink-0 animate-pulse rounded-lg bg-muted" />
            <div className="flex-1 space-y-1.5">
              <div className="h-3.5 w-2/3 animate-pulse rounded bg-muted" />
              <div className="h-2.5 w-1/2 animate-pulse rounded bg-muted" />
            </div>
          </div>
          <div className="mt-3 h-2.5 w-full animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  )
}

// ─── Empty State ─────────────────────────────────────────────────

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
}) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="extension-empty-float flex size-16 items-center justify-center rounded-2xl bg-muted/60">
        <Icon className="size-8 text-muted-foreground opacity-50" />
      </div>
      <h3 className="mt-4 text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

// ─── Utility ─────────────────────────────────────────────────────

/** Recursively collect files from a dropped FileSystemEntry, preserving relative paths. */
async function collectEntry(
  entry: FileSystemEntry,
  prefix: string,
  out: { file: File; path: string }[],
): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    )
    out.push({ file, path: prefix + entry.name })
    return
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    )
    if (batch.length === 0) break
    for (const child of batch) await collectEntry(child, `${prefix}${entry.name}/`, out)
  }
}
