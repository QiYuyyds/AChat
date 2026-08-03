'use client'

import { Cable, ChevronDown, Plug, Puzzle, Search, Sparkles, Trash2, Upload, Wrench } from 'lucide-react'
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
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'

import { McpDetailDialog } from '@/components/mcp-detail-dialog'
import { McpServerEditDialog } from '@/components/mcp-server-edit-dialog'
import { SkillDetailDialog } from '@/components/skill-detail-dialog'

export function ExtensionMainPanel() {
  const [activeTab, setActiveTab] = useState<'extensions' | 'skills'>('extensions')

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

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header with Tabs and Create Button */}
      <div className="flex shrink-0 items-center justify-between border-b px-6 py-4">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'extensions' | 'skills')}>
          <TabsList className="h-9">
            <TabsTrigger value="extensions" className="gap-1.5 text-xs">
              <Plug className="size-3.5" />
              扩展
            </TabsTrigger>
            <TabsTrigger value="skills" className="gap-1.5 text-xs">
              <Wrench className="size-3.5" />
              技能
            </TabsTrigger>
          </TabsList>
        </Tabs>

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

// Extensions Tab Content
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
  return (
    <div className="space-y-6">
      {/* Title and Description */}
      <div>
        <h2 className="text-xl font-semibold">扩展</h2>
        <p className="mt-1 text-sm text-muted-foreground">管理外部工具连接，为 Agent 提供可调用能力</p>
      </div>

      {/* Search */}
      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索扩展..."
          className="pl-10"
        />
      </div>

      {/* Section Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-muted-foreground">
          已安装 <span className="ml-1 text-xs">({servers.length})</span>
        </h3>
      </div>

      {/* Grid */}
      {loading && servers.length === 0 ? (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm">加载中...</span>
        </div>
      ) : servers.length === 0 ? (
        <EmptyState icon={Plug} title="还没有 MCP Server" description="点击右上角「添加」连接外部工具" />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {servers.map((server) => (
            <ExtensionCard
              key={server.id}
              type="mcp"
              title={server.name}
              subtitle={`${server.transport} · ${server.enabled ? '已启用' : '已禁用'}`}
              icon={Cable}
              status={server.enabled ? 'active' : 'inactive'}
              onClick={() => onSelect(server)}
              onEdit={() => onEdit(server)}
              onDelete={() => onDelete(server.id)}
              onTest={() => onTest(server)}
              testing={testingIds.has(server.id)}
              testResult={testResults[server.id]}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// Skills Tab Content
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
    <div className="space-y-6">
      {/* Title and Description */}
      <div>
        <h2 className="text-xl font-semibold">技能</h2>
        <p className="mt-1 text-sm text-muted-foreground">给 Agent 的指令增强和能力说明</p>
      </div>

      {/* Search */}
      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索技能..."
          className="pl-10"
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
          'rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors',
          dragOver
            ? 'border-primary bg-primary/5'
            : 'border-border/50 hover:border-border',
          uploading && 'opacity-50 pointer-events-none',
        )}
      >
        {uploading ? (
          <div className="flex items-center justify-center gap-2 text-muted-foreground">
            <div className="size-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="text-sm">上传中...</span>
          </div>
        ) : (
          <>
            <Upload className="mx-auto size-8 text-muted-foreground opacity-50" />
            <p className="mt-2 text-sm text-muted-foreground">拖放 SKILL.md 文件或文件夹到此处</p>
            <p className="mt-1 text-xs text-muted-foreground">支持单个文件、多个文件或整个文件夹</p>
          </>
        )}
      </div>

      {/* Section Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-muted-foreground">
          已安装 <span className="ml-1 text-xs">({skills.length})</span>
        </h3>
      </div>

      {/* Grid */}
      {loading && skills.length === 0 ? (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm">加载中...</span>
        </div>
      ) : skills.length === 0 ? (
        <EmptyState icon={Sparkles} title="还没有技能" description="拖放 SKILL.md 文件或点击右上角「上传」添加" />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {skills.map((skill) => (
            <ExtensionCard
              key={skill.slug}
              type="skill"
              title={skill.name}
              subtitle={skill.slug}
              description={skill.description}
              icon={Sparkles}
              onClick={() => onSelect(skill)}
              onDelete={() => onDelete(skill.slug)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// Extension Card Component
function ExtensionCard({
  type,
  title,
  subtitle,
  description,
  icon: Icon,
  status,
  onClick,
  onEdit,
  onDelete,
  onTest,
  testing,
  testResult,
}: {
  type: 'mcp' | 'skill'
  title: string
  subtitle: string
  description?: string
  icon: React.ComponentType<{ className?: string }>
  status?: 'active' | 'inactive'
  onClick: () => void
  onEdit?: () => void
  onDelete?: () => void
  onTest?: () => void
  testing?: boolean
  testResult?: McpTestResult
}) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-xl border bg-card p-3 sm:p-4 transition-all',
        'hover:border-primary/50 hover:shadow-sm',
        status === 'active' && 'border-l-4 border-l-success',
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-2 sm:gap-3">
        <div
          className={cn(
            'flex size-8 sm:size-10 shrink-0 items-center justify-center rounded-lg',
            type === 'mcp' ? 'bg-blue-500/10 text-blue-500' : 'bg-amber-500/10 text-amber-500',
          )}
        >
          <Icon className="size-4 sm:size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-xs sm:text-sm font-medium">{title}</h4>
          <p className="truncate text-[10px] sm:text-xs text-muted-foreground">{subtitle}</p>
        </div>
      </div>

      {/* Description */}
      {description && (
        <p className="mt-2 sm:mt-3 line-clamp-2 text-[10px] sm:text-xs text-muted-foreground">{description}</p>
      )}

      {/* Test result preview for MCP */}
      {type === 'mcp' && testResult && (
        <div
          className={cn(
            'mt-2 sm:mt-3 rounded-md border px-2 py-1 text-[9px] sm:text-[10px]',
            testResult.ok
              ? 'border-success/30 bg-success/5 text-success'
              : 'border-destructive/30 bg-destructive/5 text-destructive',
          )}
        >
          {testResult.ok ? `✓ ${testResult.tools.length} 个工具` : '✗ 连接失败'}
        </div>
      )}

      {/* Hover actions - always visible on mobile, hover on desktop */}
      <div className="absolute right-1.5 sm:right-2 top-1.5 sm:top-2 flex gap-0.5 sm:gap-1 opacity-100 sm:opacity-0 transition-opacity group-hover:opacity-100">
        {onTest && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onTest()
            }}
            disabled={testing}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="测试连接"
          >
            {testing ? (
              <div className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : (
              <Plug className="size-3.5" />
            )}
          </button>
        )}
        {onEdit && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onEdit()
            }}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="编辑"
          >
            <Puzzle className="size-3.5" />
          </button>
        )}
        {onDelete && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive"
            title="删除"
          >
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

// Empty State Component
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
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
        <Icon className="size-8 text-muted-foreground opacity-50" />
      </div>
      <h3 className="mt-4 text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

// Utility
function cn(...classes: (string | boolean | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

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
