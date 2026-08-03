'use client'

import { ArrowLeft, BookOpen, Brain, Library, Loader2, Plus, Search, Upload, X } from 'lucide-react'
import { useCallback, useState } from 'react'

import { DocumentDetail } from '@/components/document-detail'
import { MyDocumentsSection, ObsidianVaultSection } from '@/components/knowledge-library'
import { UploadDocumentDialog } from '@/components/upload-document-dialog'
import { LongTermMemoryPanel } from '@/components/settings/memory-management/long-term-memory-panel'
import { PreferencePanel } from '@/components/settings/memory-management/preference-panel'
import { SessionMemoryPanel } from '@/components/settings/memory-management/session-memory-panel'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { uploadDocument } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAppStore, type MemoryTab } from '@/stores/app-store'

const MEMORY_SUBTABS: { id: MemoryTab; label: string }[] = [
  { id: 'long-term', label: '长期记忆' },
  { id: 'preferences', label: '用户偏好' },
  { id: 'session', label: '会话摘要' },
]

export function CognitionMainPanel() {
  const tab = useAppStore((s) => s.cognitionTab)
  const setTab = useAppStore((s) => s.setCognitionTab)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background/85 backdrop-blur-2xl">
      {/* Header with Tabs */}
      <div className="flex shrink-0 items-center border-b px-6 py-4">
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
      {tab === 'knowledge' ? <KnowledgeTabContent /> : <MemoryTabContent />}
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

  // Detail view when a document is selected
  if (selectedId) {
    return (
      <>
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5">
          <Button
            size="sm"
            variant="ghost"
            className="gap-1.5 text-xs"
            onClick={() => setSelectedId(null)}
          >
            <ArrowLeft className="size-3.5" />
            返回
          </Button>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <DocumentDetail documentId={selectedId} onBack={() => setSelectedId(null)} />
        </ScrollArea>
      </>
    )
  }

  // List view
  return (
    <>
      {/* Search + upload */}
      <div className="shrink-0 px-6 pt-4 pb-3">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文档..."
              className="w-full rounded-md border bg-background py-1.5 pl-8 pr-7 text-xs outline-none transition focus:border-primary/40"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1 text-xs"
            onClick={() => setUploadOpen(true)}
          >
            <Upload className="size-3.5" />
            上传
          </Button>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => void handleDrop(e)}
          className={cn(
            'mt-2 flex items-center justify-center gap-1.5 rounded-md border border-dashed px-3 py-2 text-center text-[10px] transition',
            dragOver
              ? 'border-primary bg-primary/5 text-primary'
              : 'text-muted-foreground hover:border-border/60',
          )}
        >
          {uploading ? (
            <><Loader2 className="size-3 animate-spin" /> 上传中...</>
          ) : (
            <><Plus className="size-3" /> 拖入文档上传</>
          )}
        </div>
        {uploadError && (
          <div className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
            {uploadError}
          </div>
        )}
      </div>

      {/* Obsidian Vault + My Documents */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 px-6 pb-6">
          <ObsidianVaultSection onSelectFile={setSelectedId} />
          <div className="border-t pt-2">
            <MyDocumentsSection />
          </div>
        </div>
      </ScrollArea>

      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => {}}
      />
    </>
  )
}

// ─── Memory Tab ──────────────────────────────────────────────

function MemoryTabContent() {
  const subtab = useAppStore((s) => s.memoryTab)
  const setSubtab = useAppStore((s) => s.setMemoryTab)

  const activeSubtab = MEMORY_SUBTABS.find((t) => t.id === subtab) ?? MEMORY_SUBTABS[0]

  return (
    <>
      {/* Sub-tabs */}
      <div className="flex shrink-0 items-center gap-2 border-b px-6 py-3">
        <Tabs value={subtab} onValueChange={(v) => setSubtab(v as MemoryTab)}>
          <TabsList className="h-8">
            {MEMORY_SUBTABS.map((t) => (
              <TabsTrigger key={t.id} value={t.id} className="text-xs">
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Content */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-6">
          {subtab === 'long-term' && <LongTermMemoryPanel />}
          {subtab === 'preferences' && <PreferencePanel />}
          {subtab === 'session' && <SessionMemoryPanel />}
        </div>
      </ScrollArea>
    </>
  )
}
