'use client'

import { BookOpen, Brain, Library, Loader2, Search, Network, Sparkles, Upload, UserCog, X } from 'lucide-react'
import { useCallback, useState } from 'react'

import { DocumentDetail } from '@/components/document-detail'
import { MyDocumentsSection, ObsidianVaultSection } from '@/components/knowledge-library'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { LongTermMemoryPanel } from '@/components/settings/memory-management/long-term-memory-panel'
import { MemoryGraphPanel } from '@/components/settings/memory-management/memory-graph-panel'
import { PreferencePanel } from '@/components/settings/memory-management/preference-panel'
import { SessionMemoryPanel } from '@/components/settings/memory-management/session-memory-panel'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { uploadDocument } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore, type MemoryTab } from '@/stores/app-store'

const MEMORY_SUBTABS: { id: MemoryTab; label: string; icon: typeof Brain }[] = [
  { id: 'long-term', label: '长期记忆', icon: Brain },
  { id: 'graph', label: '图谱', icon: Network },
  { id: 'preferences', label: '用户偏好', icon: UserCog },
  { id: 'session', label: '会话摘要', icon: Sparkles },
]

const MEMORY_TAB_WIDTH = 6.5 // rem per tab, for sliding indicator

export function CognitionMainPanel() {
  const tab = useAppStore((s) => s.cognitionTab)
  const setTab = useAppStore((s) => s.setCognitionTab)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header with Tabs */}
      <div className="cognition-fade-up flex shrink-0 items-center border-b px-6 py-4">
        <Tabs value={tab} onValueChange={(v) => setTab(v as 'knowledge' | 'memory')}>
          <TabsList className="h-9">
            <TabsTrigger value="knowledge" className="gap-1.5 text-xs">
              <Library className="size-3.5" />
              知识库
            </TabsTrigger>
            <TabsTrigger value="memory" className="gap-1.5 text-xs">
              <Brain className="size-3.5" />
              记忆
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Content */}
      <div key={tab} className="tab-content-enter flex min-h-0 flex-1 flex-col">
        {tab === 'knowledge' ? <KnowledgeTabContent /> : <MemoryTabContent />}
      </div>
    </div>
  )
}

// ─── Knowledge Tab ───────────────────────────────────────────

function KnowledgeTabContent() {
  const [search, setSearch] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const selectedId = useAppStore((s) => s.selectedKnowledgeDocId)
  const setSelectedId = useAppStore((s) => s.setSelectedKnowledgeDocId)

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return
    setUploading(true)
    setUploadError(null)
    try {
      for (const file of files) await uploadDocument(file)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }, [])

  return (
    <div className="flex min-h-0 flex-1">
      {/* Left column: document list */}
      <div
        className={cn(
          'relative flex w-64 shrink-0 flex-col border-r transition-colors duration-200',
          dragOver && 'bg-primary/5',
        )}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => void handleDrop(e)}
      >
        {/* Drag overlay */}
        {dragOver && (
          <div className="pointer-events-none absolute inset-2 z-10 rounded-xl border-2 border-dashed border-primary/40 bg-primary/5" />
        )}

        {/* Search + upload */}
        <div className="cognition-fade-up shrink-0 px-3 pt-3 pb-2">
          <div className="flex items-center gap-1.5">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索文档..."
                className="w-full rounded-lg border bg-background py-1.5 pl-8 pr-7 text-xs outline-none transition-all duration-200 focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground transition hover:bg-accent hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-1 text-xs shadow-[var(--shadow-sm),var(--inset-hi)] transition-all duration-200 hover:shadow-[var(--shadow-md),var(--inset-hi)] active:scale-[0.97]"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="size-3.5" />
              上传
            </Button>
          </div>
          {uploading && (
            <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              上传中...
            </div>
          )}
          {uploadError && (
            <div className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
              {uploadError}
            </div>
          )}
        </div>

        {/* Document list */}
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 px-2 py-2">
            <ObsidianVaultSection onSelectFile={setSelectedId} search={search} />
            <div className="border-t pt-2">
              <MyDocumentsSection search={search} />
            </div>
          </div>
        </ScrollArea>
      </div>

      {/* Right column: document detail or empty state */}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {selectedId ? (
          <DocumentDetail documentId={selectedId} onBack={() => setSelectedId(null)} />
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center bg-background/85 backdrop-blur-2xl">
            {/* Ambient glow */}
            <div className="cognition-ambient pointer-events-none absolute size-48 rounded-full bg-primary/8 blur-3xl" />

            <div className="cognition-fade-up relative flex flex-col items-center gap-4 text-center">
              <div className="relative">
                <div className="flex size-16 items-center justify-center rounded-2xl border border-border/50 bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
                  <BookOpen className="size-7 text-muted-foreground/70 cognition-empty-float" />
                </div>
              </div>
              <div className="space-y-1">
                <p className="text-sm font-semibold text-foreground">知识库</p>
                <p className="text-xs text-muted-foreground">从左侧选择文档查看详情，或拖入文件上传</p>
              </div>
            </div>
          </div>
        )}
      </div>

      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => {}}
      />
    </div>
  )
}

// ─── Memory Tab ──────────────────────────────────────────────

function MemoryTabContent() {
  const subtab = useAppStore((s) => s.memoryTab)
  const setSubtab = useAppStore((s) => s.setMemoryTab)
  const activeIndex = MEMORY_SUBTABS.findIndex((t) => t.id === subtab)

  return (
    <>
      {/* Sliding indicator sub-tabs */}
      <div className="cognition-fade-up flex shrink-0 items-center border-b px-6 py-3">
        <div className="relative flex w-fit items-center rounded-lg bg-muted p-0.5">
          {/* Sliding indicator */}
          <span
            className="pointer-events-none absolute top-0.5 bottom-0.5 left-0.5 rounded-md bg-background shadow-[var(--shadow-sm),var(--inset-hi)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{
              width: `calc(${MEMORY_TAB_WIDTH}rem - 2px)`,
              transform: `translateX(${activeIndex * 100}%)`,
            }}
          />
          {MEMORY_SUBTABS.map((t) => {
            const Icon = t.icon
            const isActive = t.id === subtab
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setSubtab(t.id)}
                className={cn(
                  'relative z-10 inline-flex h-8 items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors duration-200',
                  isActive
                    ? 'text-foreground'
                    : 'text-muted-foreground hover:text-foreground/70',
                )}
                style={{ width: `${MEMORY_TAB_WIDTH}rem` }}
              >
                <Icon className={cn('size-3.5 transition-transform duration-300', isActive && 'scale-110')} />
                {t.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      {subtab === 'graph' ? (
        <div key="graph" className="tab-content-enter min-h-0 flex-1 px-4 pb-4 pt-2">
          <MemoryGraphPanel />
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div key={subtab} className="tab-content-enter px-6 py-6">
            {subtab === 'long-term' && <LongTermMemoryPanel />}
            {subtab === 'preferences' && <PreferencePanel />}
            {subtab === 'session' && <SessionMemoryPanel />}
          </div>
        </ScrollArea>
      )}
    </>
  )
}
