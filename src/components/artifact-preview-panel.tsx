'use client'

import { AlertCircle, Check, ChevronLeft, ChevronRight, Clock, Copy, Download, ExternalLink, Eye, FileCode, FileText, FolderGit2, FolderTree, GitCompare, History, Image as ImageIcon, Layers, Loader2, Maximize, Pencil, Presentation, RefreshCw, RotateCcw, Save, X, ZoomIn, ZoomOut } from 'lucide-react'
import dynamic from 'next/dynamic'
import { useTheme } from 'next-themes'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'

import { buildDiffStyles } from '@/components/diff-viewer-styles'
import { getFileIcon } from '@/components/file-explorer-panel'
import { Markdown } from '@/components/markdown'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { ArtifactRow } from '@/db/schema'
import { createArtifactVersion, fetchArtifactVersions, workspaceReadFile, workspaceWriteFile } from '@/lib/api'
import { artifactPreviewPath } from '@/lib/artifact-preview'
import { normalizeLang } from '@/lib/highlighter'
import { cn } from '@/lib/utils'
import { buildArtifactVersionDiff } from '@/shared/artifact-version-diff'
import { formatMermaidError } from '@/shared/mermaid-normalize'
import { normalizePptDeck, toEditablePptContent } from '@/shared/ppt-normalize'
import { detectBulletTone, resolvePptTheme } from '@/shared/ppt-theme'
import type { ArtifactContent, DiffHunk, PptBlock, PptColumnBlock, PptTheme, PptTone } from '@/shared/types'
import type { NormalizedPptSlide } from '@/shared/ppt-normalize'
import { useAppStore } from '@/stores/app-store'

// 编辑器仅在用户点「编辑」时懒加载（重型 client 库；CodeMirror 无 worker、离线 OK）
const ArtifactCodeEditor = dynamic(() => import('./artifact-code-editor'), {
  ssr: false,
  loading: () => (
    <div className="flex size-full items-center justify-center p-4 text-xs text-muted-foreground">
      编辑器加载中…
    </div>
  ),
})

type SaveVersionFn = (rawContent: unknown, title?: string) => Promise<void>

/**
 * ArtifactPreviewPanel — 右侧滑入的产物预览面板。
 *
 * 由 store.previewArtifactId 控制显隐。按 artifact.type 分发到不同 view。
 * 顶部支持多版本切换：从同一个 root 派生的所有 artifact 通过 /versions API 查回。
 * web_app / document 支持面板内编辑并「提交为新版本」（POST /versions → createArtifactVersion）。
 */
export function ArtifactPreviewPanel() {
  const id = useAppStore((s) => s.previewArtifactId)
  const artifact = useAppStore((s) => (id ? s.artifacts[id] : null))
  const upsertArtifact = useAppStore((s) => s.upsertArtifact)
  const close = useAppStore((s) => s.closeArtifactPreview)
  const openPreview = useAppStore((s) => s.openArtifactPreview)

  const [versions, setVersions] = useState<ArtifactRow[] | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [showCompare, setShowCompare] = useState(false)
  const [compareBaseId, setCompareBaseId] = useState<string | null>(null)
  const [compareTargetId, setCompareTargetId] = useState<string | null>(null)

  // 切到新 artifact 时拉它的版本链
  useEffect(() => {
    if (!id) return
    let cancelled = false
    setVersions(null)
    setShowCompare(false)
    setCompareBaseId(null)
    setCompareTargetId(null)
    setVersionsLoading(true)
    fetchArtifactVersions(id)
      .then((list) => {
        if (cancelled) return
        setVersions(list)
        // 把新发现的兄弟版本灌到 store，方便下次切换不重拉
        for (const v of list) upsertArtifact(v)
      })
      .catch((err) => {
        if (!cancelled) console.warn('[ArtifactPreviewPanel] versions fetch failed', err)
      })
      .finally(() => {
        if (!cancelled) setVersionsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id, upsertArtifact])

  const switchVersion = useCallback(
    (targetId: string) => {
      if (targetId !== id) openPreview(targetId)
    },
    [id, openPreview],
  )

  const openCompare = useCallback(() => {
    if (!id || !versions || versions.length < 2) return
    const currentIndex = versions.findIndex((v) => v.id === id)
    const targetIndex = currentIndex >= 0 ? currentIndex : versions.length - 1
    const baseIndex = targetIndex > 0 ? targetIndex - 1 : 0
    const fallbackTargetIndex = targetIndex === baseIndex ? 1 : targetIndex
    const target = versions[fallbackTargetIndex] ?? versions[versions.length - 1]
    const base = versions[baseIndex] ?? versions[0]
    setCompareBaseId(base.id)
    setCompareTargetId(target.id)
    setShowCompare(true)
  }, [id, versions])

  // 提交编辑后的内容为新版本，成功后切到新版本（版本条经 id effect 自动刷新）
  const handleSaveVersion = useCallback<SaveVersionFn>(
    async (rawContent, title) => {
      if (!id) return
      const row = await createArtifactVersion(id, { content: rawContent, title })
      upsertArtifact(row)
      openPreview(row.id)
    },
    [id, upsertArtifact, openPreview],
  )

  if (!id || !artifact) return null

  const versionCount = versions?.length ?? 0
  const hasMultiple = versionCount > 1

  return (
    <aside className="flex w-1/2 min-w-[420px] shrink-0 flex-col border-l bg-card max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full max-md:min-w-0">
      <header className="flex shrink-0 items-center justify-between border-b px-4 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <TypeIcon type={artifact.type} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium" title={artifact.title}>
              {artifact.title}
            </div>
            <div className="text-xs text-muted-foreground">
              {artifact.type} · v{artifact.version}
              {hasMultiple && ` / ${versionCount}`}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {hasMultiple && (
            <>
              <Button
                size="icon"
                variant={showCompare ? 'default' : 'ghost'}
                onClick={() => {
                  if (showCompare) setShowCompare(false)
                  else openCompare()
                }}
                title="对比版本"
              >
                <GitCompare className="size-4" />
              </Button>
              <Button
                size="icon"
                variant={showVersions ? 'default' : 'ghost'}
                onClick={() => setShowVersions((v) => !v)}
                title={`版本历史 (${versionCount} 个)`}
              >
                <History className="size-4" />
              </Button>
            </>
          )}
          {artifact.type === 'web_app' && (
            <>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => openPreviewInNewTab(artifact.id)}
                title="打开预览 URL"
              >
                <ExternalLink className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => copyPreviewUrl(artifact.id)}
                title="复制预览 URL"
              >
                <Copy className="size-4" />
              </Button>
            </>
          )}
          <a
            href={`/api/artifacts/${artifact.id}/export`}
            download
            title={`下载${
              artifact.type === 'web_app' || artifact.type === 'project'
                ? ' .zip'
                : artifact.type === 'document'
                  ? ' .md'
                  : artifact.type === 'diagram'
                    ? ' .mmd'
                    : artifact.type === 'ppt'
                      ? '可编辑 .pptx'
                      : ''
            }`}
            className="inline-flex size-8 items-center justify-center rounded-lg text-foreground/70 transition hover:bg-muted hover:text-foreground"
          >
            <Download className="size-4" />
          </a>
          <Button size="icon" variant="ghost" onClick={close} title="关闭预览">
            <X className="size-4" />
          </Button>
        </div>
      </header>

      {/* 版本切换条：展开时显示所有版本，点击切换 */}
      {showVersions && versions && versions.length > 0 && (
        <div className="shrink-0 border-b bg-muted/20 px-3 py-2">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            版本历史
          </div>
          <div className="flex flex-wrap gap-1">
            {versions.map((v) => {
              const isCurrent = v.id === id
              return (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => switchVersion(v.id)}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition',
                    isCurrent
                      ? 'border-primary bg-primary/10 text-foreground'
                      : 'border-transparent hover:border-foreground/20 hover:bg-accent',
                  )}
                  title={`v${v.version} · ${new Date(v.createdAt).toLocaleString('zh-CN')}`}
                >
                  <span className="font-mono">v{v.version}</span>
                  <span className="text-muted-foreground">·</span>
                  <Clock className="size-2.5" />
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {new Date(v.createdAt).toLocaleTimeString('zh-CN', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </button>
              )
            })}
          </div>
          {versionsLoading && (
            <div className="mt-1 text-[10px] text-muted-foreground">加载中…</div>
          )}
        </div>
      )}

      {showCompare && versions && compareBaseId && compareTargetId ? (
        <VersionCompareView
          versions={versions}
          baseId={compareBaseId}
          targetId={compareTargetId}
          onBaseChange={setCompareBaseId}
          onTargetChange={setCompareTargetId}
        />
      ) : (
        <ArtifactView artifact={artifact} onSaveVersion={handleSaveVersion} />
      )}
    </aside>
  )
}

// ─── 调度 ──────────────────────────────────────────────
function ArtifactView({
  artifact,
  onSaveVersion,
}: {
  artifact: ArtifactRow
  onSaveVersion: SaveVersionFn
}) {
  const content = artifact.content as ArtifactContent

  // 用 data-selection-target 标记容器：SelectionPopover 会响应这里的文字选择
  const wrap = (children: React.ReactNode) => (
    <div
      data-selection-target="artifact"
      data-selection-label={`产物「${artifact.title}」 v${artifact.version}`}
      data-selection-artifact-id={artifact.id}
      className="contents"
    >
      {children}
    </div>
  )

  switch (content.type) {
    case 'web_app':
      return wrap(<WebAppView artifactId={artifact.id} content={content} onSaveVersion={onSaveVersion} />)
    case 'document':
      return wrap(<DocumentView content={content} onSaveVersion={onSaveVersion} />)
    case 'diagram':
      return wrap(<DiagramView content={content} onSaveVersion={onSaveVersion} />)
    case 'image':
      return <ImageView content={content} />
    case 'code_file':
      return wrap(
        <CodeFileView
          artifactId={artifact.id}
          conversationId={artifact.conversationId}
          content={content}
          onSaveVersion={onSaveVersion}
        />,
      )
    case 'project':
      return wrap(<ProjectView conversationId={artifact.conversationId} content={content} />)
    case 'diff':
      return wrap(<DiffArtifactView content={content} />)
    case 'ppt':
      return wrap(<SlideDeckView content={content} onSaveVersion={onSaveVersion} />)
    default:
      return <Empty>该类型暂不支持预览</Empty>
  }
}

// ─── web_app: iframe + 源码 + 编辑 ─────────────────────
function WebAppView({
  artifactId,
  content,
  onSaveVersion,
}: {
  artifactId: string
  content: Extract<ArtifactContent, { type: 'web_app' }>
  onSaveVersion: SaveVersionFn
}) {
  const [view, setView] = useState<'render' | 'edit'>('render')
  const [activeFile, setActiveFile] = useState<string>(content.entry)
  const [draftFiles, setDraftFiles] = useState<Record<string, string>>(content.files)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fileNames = Object.keys(content.files)

  // 切版本/产物（content 每行是新对象）时重置编辑态
  useEffect(() => {
    setDraftFiles(content.files)
    setActiveFile(content.entry)
    setView('render')
    setSaving(false)
    setError(null)
  }, [content])

  const dirty = useMemo(
    () => JSON.stringify(draftFiles) !== JSON.stringify(content.files),
    [draftFiles, content.files],
  )

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSaveVersion({ files: draftFiles, entry: content.entry })
      // 成功后面板切到新版本，本视图随 content 变化自动重置
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
      setSaving(false)
    }
  }

  const showFilePicker = view === 'edit' && fileNames.length > 1

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b px-2">
        <div className="flex">
          <ViewTab active={view === 'render'} onClick={() => setView('render')}>
            <Eye className="size-3.5" />
            预览
          </ViewTab>
          <ViewTab active={view === 'edit'} onClick={() => setView('edit')}>
            <Pencil className="size-3.5" />
            编辑
          </ViewTab>
        </div>
        {showFilePicker && (
          <select
            value={activeFile}
            onChange={(e) => setActiveFile(e.target.value)}
            className="rounded border bg-background px-2 py-0.5 text-xs"
          >
            {fileNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {view === 'render' && (
          <iframe
            key={artifactId}
            src={artifactPreviewPath(artifactId)}
            sandbox="allow-scripts"
            className="size-full border-0 bg-white"
            title="Artifact preview"
          />
        )}
        {view === 'edit' && (
          <ArtifactCodeEditor
            value={draftFiles[activeFile] ?? ''}
            onChange={(next) => setDraftFiles((d) => ({ ...d, [activeFile]: next }))}
            filename={activeFile}
            type="web_app"
          />
        )}
      </div>

      {view === 'edit' && (
        <EditFooter
          dirty={dirty}
          saving={saving}
          error={error}
          onSave={save}
          onReset={() => {
            setDraftFiles(content.files)
            setActiveFile(content.entry)
            setError(null)
          }}
        />
      )}
    </div>
  )
}

// ─── document: 预览 + 编辑 ─────────────────────────────
function DocumentView({
  content,
  onSaveVersion,
}: {
  content: Extract<ArtifactContent, { type: 'document' }>
  onSaveVersion: SaveVersionFn
}) {
  const [view, setView] = useState<'render' | 'edit'>('render')
  const [draft, setDraft] = useState(content.content)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDraft(content.content)
    setView('render')
    setSaving(false)
    setError(null)
  }, [content])

  const dirty = draft !== content.content

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSaveVersion({ content: draft })
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
      setSaving(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 border-b px-2">
        <ViewTab active={view === 'render'} onClick={() => setView('render')}>
          <Eye className="size-3.5" />
          预览
        </ViewTab>
        <ViewTab active={view === 'edit'} onClick={() => setView('edit')}>
          <Pencil className="size-3.5" />
          编辑
        </ViewTab>
      </div>
      <div className="min-h-0 flex-1">
        {view === 'render' ? (
          <ScrollArea className="size-full">
            <div className="mx-auto max-w-3xl px-6 py-6">
              <Markdown>{content.content}</Markdown>
            </div>
          </ScrollArea>
        ) : (
          <ArtifactCodeEditor value={draft} onChange={setDraft} filename="document.md" type="document" />
        )}
      </div>
      {view === 'edit' && (
        <EditFooter
          dirty={dirty}
          saving={saving}
          error={error}
          onSave={save}
          onReset={() => {
            setDraft(content.content)
            setError(null)
          }}
        />
      )}
    </div>
  )
}

// ─── diagram: Mermaid 预览 + 源码编辑 ───────────────────
function DiagramView({
  content,
  onSaveVersion,
}: {
  content: Extract<ArtifactContent, { type: 'diagram' }>
  onSaveVersion: SaveVersionFn
}) {
  const [view, setView] = useState<'render' | 'edit'>('render')
  const [draft, setDraft] = useState(content.source)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDraft(content.source)
    setView('render')
    setSaving(false)
    setError(null)
  }, [content])

  const dirty = draft !== content.source

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSaveVersion({ syntax: 'mermaid', source: draft, theme: content.theme })
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败（请检查 Mermaid 源码）')
      setSaving(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 border-b px-2">
        <ViewTab active={view === 'render'} onClick={() => setView('render')}>
          <Eye className="size-3.5" />
          预览
        </ViewTab>
        <ViewTab active={view === 'edit'} onClick={() => setView('edit')}>
          <Pencil className="size-3.5" />
          编辑源码
        </ViewTab>
      </div>
      <div className="min-h-0 flex-1">
        {view === 'render' ? (
          <MermaidPreview content={content} />
        ) : (
          <ArtifactCodeEditor value={draft} onChange={setDraft} filename="diagram.mmd" type="diagram" />
        )}
      </div>
      {view === 'edit' && (
        <EditFooter
          dirty={dirty}
          saving={saving}
          error={error}
          onSave={save}
          onReset={() => {
            setDraft(content.source)
            setError(null)
          }}
        />
      )}
    </div>
  )
}

function MermaidPreview({ content }: { content: Extract<ArtifactContent, { type: 'diagram' }> }) {
  const { resolvedTheme } = useTheme()
  const [svg, setSvg] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [zoom, setZoom] = useState(1.25)

  useEffect(() => {
    setZoom(1.25)
  }, [content.source])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setSvg('')

    const render = async () => {
      try {
        const { default: mermaid } = await import('mermaid')
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: content.theme ?? (resolvedTheme === 'dark' ? 'dark' : 'default'),
        })
        const renderId = `agenthub-mermaid-${Date.now()}-${Math.random().toString(36).slice(2)}`
        const result = await mermaid.render(renderId, content.source)
        if (!cancelled) setSvg(result.svg)
      } catch (err) {
        if (!cancelled) setError(formatMermaidError(err instanceof Error ? err.message : String(err)))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void render()
    return () => {
      cancelled = true
    }
  }, [content, resolvedTheme])

  if (loading) {
    return (
      <div className="flex size-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        渲染图表中…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex size-full flex-col items-center justify-center gap-3 p-6 text-center">
        <AlertCircle className="size-6 text-red-500" />
        <div className="text-sm font-medium">Mermaid 渲染失败</div>
        <pre className="max-w-full overflow-auto rounded-md bg-muted px-3 py-2 text-left font-mono text-xs text-muted-foreground">
          {error}
        </pre>
      </div>
    )
  }

  const diagramWidth = Math.round(920 * zoom)

  return (
    <div className="relative size-full bg-zinc-50 dark:bg-zinc-950">
      <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-md border bg-background/95 p-1 shadow-sm backdrop-blur">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => setZoom((v) => Math.max(0.5, Number((v - 0.25).toFixed(2))))}
          disabled={zoom <= 0.5}
          title="缩小"
        >
          <ZoomOut className="size-3.5" />
        </Button>
        <button
          type="button"
          className="min-w-12 rounded px-1.5 py-1 text-center font-mono text-[11px] text-muted-foreground transition hover:bg-accent hover:text-foreground"
          onClick={() => setZoom(1.25)}
          title="重置缩放"
        >
          {Math.round(zoom * 100)}%
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => setZoom((v) => Math.min(3, Number((v + 0.25).toFixed(2))))}
          disabled={zoom >= 3}
          title="放大"
        >
          <ZoomIn className="size-3.5" />
        </Button>
      </div>
      <div className="size-full overflow-auto">
        <div
          className="grid min-h-full p-12 [place-items:safe_center]"
          style={{ minWidth: `${diagramWidth + 96}px` }}
        >
          <div
            className="overflow-auto rounded-md bg-white p-4 shadow-sm dark:bg-zinc-900 [&_svg]:h-auto [&_svg]:w-full [&_svg]:max-w-none"
            style={{
              width: `${diagramWidth}px`,
              minWidth: `${diagramWidth}px`,
            }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      </div>
    </div>
  )
}

// ─── ppt: 幻灯片分页预览 + JSON 编辑 ────────────────────
function SlideDeckView({
  content,
  onSaveVersion,
}: {
  content: Extract<ArtifactContent, { type: 'ppt' }>
  onSaveVersion: SaveVersionFn
}) {
  const serialized = useMemo(() => JSON.stringify(toEditablePptContent(content), null, 2), [content])
  const deck = useMemo(() => normalizePptDeck(content), [content])
  const [view, setView] = useState<'render' | 'edit'>('render')
  const [idx, setIdx] = useState(0)
  const [draft, setDraft] = useState(serialized)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setIdx(0)
    setDraft(serialized)
    setView('render')
    setSaving(false)
    setError(null)
  }, [serialized])

  const total = deck.slides.length
  const safeIdx = Math.min(idx, Math.max(0, total - 1))
  const current = deck.slides[safeIdx]
  const dirty = draft !== serialized

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSaveVersion(JSON.parse(draft))
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败（请检查 JSON 格式）')
      setSaving(false)
    }
  }

  const enterFullscreen = () => {
    const el = containerRef.current
    if (el?.requestFullscreen) el.requestFullscreen().catch(() => {})
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center border-b px-2">
        <ViewTab active={view === 'render'} onClick={() => setView('render')}>
          <Eye className="size-3.5" />
          预览
        </ViewTab>
        <ViewTab active={view === 'edit'} onClick={() => setView('edit')}>
          <Pencil className="size-3.5" />
          编辑 JSON
        </ViewTab>
        {view === 'render' && total > 0 && (
          <div className="ml-auto flex items-center gap-1 pr-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
              disabled={safeIdx <= 0}
              title="上一页"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="min-w-12 text-center text-xs tabular-nums text-muted-foreground">
              {safeIdx + 1} / {total}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setIdx((i) => Math.min(total - 1, i + 1))}
              disabled={safeIdx >= total - 1}
              title="下一页"
            >
              <ChevronRight className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={enterFullscreen}
              title="全屏"
            >
              <Maximize className="size-4" />
            </Button>
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1">
        {view === 'render' ? (
          <div ref={containerRef} className="size-full bg-zinc-100 dark:bg-zinc-900">
            {current ? (
              <SlideView slide={current} theme={deck.theme} />
            ) : (
              <Empty>没有幻灯片</Empty>
            )}
          </div>
        ) : (
          <ArtifactCodeEditor value={draft} onChange={setDraft} filename="slides.json" type="ppt" />
        )}
      </div>
      {view === 'edit' && (
        <EditFooter
          dirty={dirty}
          saving={saving}
          error={error}
          onSave={save}
          onReset={() => {
            setDraft(serialized)
            setError(null)
          }}
        />
      )}
    </div>
  )
}

function SlideView({
  slide,
  theme,
}: {
  slide: NormalizedPptSlide
  theme?: PptTheme
}) {
  const t = resolvePptTheme(theme)
  const centered = slide.layout === 'title' || slide.layout === 'section'
  const heading = slide.title
  const hx = (c: string) => `#${c}`

  return (
    <div
      className="flex size-full items-center justify-center p-6"
      style={{ background: hx(t.background) }}
    >
      <div
        className="aspect-video w-full max-w-3xl overflow-hidden rounded-lg shadow-md"
        style={{
          border: `1px solid ${hx(t.divider)}`,
          fontFamily: `${t.fontBody}, system-ui, sans-serif`,
        }}
      >
        {centered ? (
          <div
            className="flex size-full flex-col items-center justify-center gap-5 overflow-hidden p-12 text-center"
            style={{ background: hx(t.primary) }}
          >
            {heading && (
              <h2
                className="line-clamp-3 max-w-full break-words text-3xl font-bold leading-tight [overflow-wrap:anywhere]"
                style={{ color: '#FFFFFF', fontFamily: `${t.fontHeading}, system-ui, sans-serif` }}
              >
                {heading}
              </h2>
            )}
            {slide.subtitle && (
              <div className="line-clamp-2 max-w-2xl break-words text-base [overflow-wrap:anywhere]" style={{ color: 'rgba(255,255,255,0.82)' }}>
                {slide.subtitle}
              </div>
            )}
            <SlideBlocks blocks={slide.blocks} theme={t} layout={slide.layout} centered />
          </div>
        ) : (
          <div className="flex size-full flex-col" style={{ background: hx(t.surface) }}>
            <div className="h-1.5 shrink-0" style={{ background: hx(t.primary) }} />
            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden p-10">
              {(heading || slide.subtitle) && (
                <div className="min-w-0 border-b pb-3" style={{ borderColor: hx(t.divider) }}>
                  {heading && (
                    <h2
                      className="line-clamp-2 break-words text-2xl font-bold leading-tight [overflow-wrap:anywhere]"
                      style={{
                        color: hx(t.primary),
                        fontFamily: `${t.fontHeading}, system-ui, sans-serif`,
                      }}
                    >
                      {heading}
                    </h2>
                  )}
                  {slide.subtitle && (
                    <div className="mt-1 line-clamp-1 break-words text-sm [overflow-wrap:anywhere]" style={{ color: hx(t.textMuted) }}>
                      {slide.subtitle}
                    </div>
                  )}
                </div>
              )}
              <SlideBlocks blocks={slide.blocks} theme={t} layout={slide.layout} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

type ResolvedPptTheme = ReturnType<typeof resolvePptTheme>

function SlideBlocks({
  blocks,
  theme,
  layout,
  centered = false,
}: {
  blocks: PptBlock[]
  theme: ResolvedPptTheme
  layout: NormalizedPptSlide['layout']
  centered?: boolean
}) {
  if (blocks.length === 0) return null

  const metricOnly = layout === 'metrics' && blocks.every((block) => block.type === 'metric')
  if (metricOnly) {
    return (
      <div className="grid min-h-0 w-full grid-cols-2 gap-3 overflow-hidden">
        {blocks.map((block, index) =>
          block.type === 'metric' ? <MetricBlock key={index} block={block} theme={theme} /> : null,
        )}
      </div>
    )
  }

  return (
    <div className={cn('min-h-0 min-w-0 space-y-2 overflow-hidden', centered && 'max-w-2xl text-left')}>
      {blocks.map((block, index) => (
        <SlideBlock key={index} block={block} theme={theme} centered={centered} />
      ))}
    </div>
  )
}

function SlideBlock({
  block,
  theme,
  centered,
}: {
  block: PptBlock
  theme: ResolvedPptTheme
  centered?: boolean
}) {
  const hx = (c: string) => `#${c}`
  switch (block.type) {
    case 'heading':
      return (
        <div
          className={cn(
            'line-clamp-2 break-words font-semibold leading-tight [overflow-wrap:anywhere]',
            block.level === 1 ? 'text-xl' : 'text-base',
          )}
          style={{ color: centered ? '#FFFFFF' : hx(theme.primary), fontFamily: `${theme.fontHeading}, system-ui, sans-serif` }}
        >
          {block.text}
        </div>
      )
    case 'paragraph':
      return (
        <p
          className="line-clamp-4 break-words text-sm leading-relaxed [overflow-wrap:anywhere]"
          style={{ color: centered ? 'rgba(255,255,255,0.86)' : hx(theme.textBody) }}
        >
          {block.text}
        </p>
      )
    case 'bullets':
      return <BulletsBlock items={block.items} ordered={block.ordered} theme={theme} centered={centered} />
    case 'metric':
      return <MetricBlock block={block} theme={theme} />
    case 'quote':
      return <QuoteBlock block={block} theme={theme} centered={centered} />
    case 'timeline':
      return <TimelineBlock block={block} theme={theme} />
    case 'columns':
      return <ColumnsBlock block={block} theme={theme} />
    case 'callout':
      return <CalloutBlock block={block} theme={theme} centered={centered} />
    case 'divider':
      return <div className="h-px w-full" style={{ background: centered ? 'rgba(255,255,255,0.28)' : hx(theme.divider) }} />
    case 'spacer':
      return <div className={block.size === 'lg' ? 'h-5' : block.size === 'sm' ? 'h-1' : 'h-3'} />
  }
}

function BulletsBlock({
  items,
  ordered,
  theme,
  centered,
}: {
  items: string[]
  ordered?: boolean
  theme: ResolvedPptTheme
  centered?: boolean
}) {
  const hx = (c: string) => `#${c}`
  const Tag = ordered ? 'ol' : 'ul'
  return (
    <Tag className="min-w-0 space-y-1.5 overflow-hidden">
      {items.slice(0, 7).map((text, index) => {
        const tone = detectBulletTone(text)
        const color = tone === 'positive' ? hx(theme.accentPositive) : tone === 'negative' ? hx(theme.accentNegative) : hx(theme.primary)
        const icon = ordered ? `${index + 1}.` : tone === 'positive' ? '▲' : tone === 'negative' ? '▼' : '▪'
        return (
          <li
            key={`${text}-${index}`}
            className="flex min-w-0 items-start gap-2 rounded-md px-3 py-1.5 text-sm leading-relaxed"
            style={{
              background: centered ? 'rgba(255,255,255,0.10)' : hx(theme.background),
              border: centered ? '1px solid rgba(255,255,255,0.16)' : `1px solid ${hx(theme.divider)}`,
              color: centered ? 'rgba(255,255,255,0.88)' : hx(theme.textBody),
            }}
          >
            <span className="mt-0.5 shrink-0 select-none text-xs font-semibold" style={{ color: centered ? '#FFFFFF' : color }}>
              {icon}
            </span>
            <span className="line-clamp-2 min-w-0 break-words [overflow-wrap:anywhere]">{text}</span>
          </li>
        )
      })}
    </Tag>
  )
}

function MetricBlock({
  block,
  theme,
}: {
  block: Extract<PptBlock, { type: 'metric' }>
  theme: ResolvedPptTheme
}) {
  const hx = (c: string) => `#${c}`
  const color = hx(toneColor(block.tone, theme))
  return (
    <div
      className="min-w-0 rounded-lg border px-3 py-2"
      style={{ background: hx(theme.background), borderColor: hx(theme.divider) }}
    >
      <div className="line-clamp-1 text-[11px] font-medium uppercase tracking-wide" style={{ color: hx(theme.textMuted) }}>
        {block.label}
      </div>
      <div className="mt-1 line-clamp-1 break-words text-2xl font-bold [overflow-wrap:anywhere]" style={{ color }}>
        {block.value}
      </div>
      {block.change && (
        <div className="mt-0.5 line-clamp-1 break-words text-xs [overflow-wrap:anywhere]" style={{ color }}>
          {block.change}
        </div>
      )}
    </div>
  )
}

function QuoteBlock({
  block,
  theme,
  centered,
}: {
  block: Extract<PptBlock, { type: 'quote' }>
  theme: ResolvedPptTheme
  centered?: boolean
}) {
  const hx = (c: string) => `#${c}`
  return (
    <blockquote
      className="min-w-0 rounded-lg border-l-4 px-4 py-3"
      style={{
        background: centered ? 'rgba(255,255,255,0.10)' : hx(theme.background),
        borderColor: centered ? '#FFFFFF' : hx(theme.primary),
      }}
    >
      <div className="line-clamp-4 break-words text-base font-medium leading-relaxed [overflow-wrap:anywhere]" style={{ color: centered ? '#FFFFFF' : hx(theme.textBody) }}>
        “{block.text}”
      </div>
      {block.attribution && (
        <div className="mt-2 line-clamp-1 text-xs" style={{ color: centered ? 'rgba(255,255,255,0.72)' : hx(theme.textMuted) }}>
          {block.attribution}
        </div>
      )}
    </blockquote>
  )
}

function TimelineBlock({
  block,
  theme,
}: {
  block: Extract<PptBlock, { type: 'timeline' }>
  theme: ResolvedPptTheme
}) {
  const hx = (c: string) => `#${c}`
  return (
    <div className="grid min-w-0 grid-cols-2 gap-2 overflow-hidden">
      {block.items.slice(0, 6).map((item, index) => (
        <div key={`${item.label}-${index}`} className="min-w-0 rounded-md border px-3 py-2" style={{ background: hx(theme.background), borderColor: hx(theme.divider) }}>
          <div className="line-clamp-1 text-[11px] font-semibold" style={{ color: hx(theme.primary) }}>{item.label}</div>
          {item.title && <div className="line-clamp-1 text-sm font-medium" style={{ color: hx(theme.textBody) }}>{item.title}</div>}
          {item.text && <div className="mt-0.5 line-clamp-2 break-words text-xs [overflow-wrap:anywhere]" style={{ color: hx(theme.textMuted) }}>{item.text}</div>}
        </div>
      ))}
    </div>
  )
}

function ColumnsBlock({
  block,
  theme,
}: {
  block: Extract<PptBlock, { type: 'columns' }>
  theme: ResolvedPptTheme
}) {
  const hx = (c: string) => `#${c}`
  return (
    <div
      className="grid min-w-0 gap-2 overflow-hidden"
      style={{ gridTemplateColumns: `repeat(${Math.max(1, block.columns.length)}, minmax(0, 1fr))` }}
    >
      {block.columns.map((column, index) => (
        <div key={index} className="min-w-0 rounded-lg border p-3" style={{ background: hx(theme.background), borderColor: hx(theme.divider) }}>
          {column.title && <div className="mb-2 line-clamp-1 text-sm font-semibold" style={{ color: hx(theme.primary) }}>{column.title}</div>}
          <div className="space-y-1.5 overflow-hidden">
            {(column.blocks ?? []).slice(0, 4).map((child, childIndex) => (
              <ColumnBlock key={childIndex} block={child} theme={theme} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function ColumnBlock({ block, theme }: { block: PptColumnBlock; theme: ResolvedPptTheme }) {
  const hx = (c: string) => `#${c}`
  if (block.type === 'paragraph') {
    return <div className="line-clamp-3 break-words text-xs leading-relaxed [overflow-wrap:anywhere]" style={{ color: hx(theme.textBody) }}>{block.text}</div>
  }
  if (block.type === 'bullets') {
    return <BulletsBlock items={block.items.slice(0, 4)} ordered={block.ordered} theme={theme} />
  }
  if (block.type === 'metric') {
    return <MetricBlock block={block} theme={theme} />
  }
  return <CalloutBlock block={block} theme={theme} />
}

function CalloutBlock({
  block,
  theme,
  centered,
}: {
  block: Extract<PptBlock, { type: 'callout' }>
  theme: ResolvedPptTheme
  centered?: boolean
}) {
  const hx = (c: string) => `#${c}`
  const color = hx(toneColor(block.tone, theme))
  return (
    <div
      className="min-w-0 rounded-lg border px-3 py-2"
      style={{
        background: centered ? 'rgba(255,255,255,0.10)' : hx(theme.background),
        borderColor: centered ? 'rgba(255,255,255,0.18)' : color,
      }}
    >
      {block.title && <div className="line-clamp-1 text-xs font-semibold" style={{ color: centered ? '#FFFFFF' : color }}>{block.title}</div>}
      <div className="line-clamp-3 break-words text-sm leading-relaxed [overflow-wrap:anywhere]" style={{ color: centered ? 'rgba(255,255,255,0.88)' : hx(theme.textBody) }}>
        {block.text}
      </div>
    </div>
  )
}

function toneColor(tone: PptTone | undefined, theme: ResolvedPptTheme): string {
  if (tone === 'positive') return theme.accentPositive
  if (tone === 'negative' || tone === 'warning') return theme.accentNegative
  return theme.primary
}

// ─── image ─────────────────────────────────────────────
function ImageView({ content }: { content: Extract<ArtifactContent, { type: 'image' }> }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-zinc-100 p-4 dark:bg-zinc-900">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={content.url}
        alt={content.alt}
        className="max-h-full max-w-full object-contain"
      />
    </div>
  )
}

// ─── code_file（workspace 文件引用）──────────────────────
function CodeFileView({
  artifactId,
  conversationId,
  content,
  onSaveVersion,
}: {
  artifactId: string
  conversationId: string
  content: Extract<ArtifactContent, { type: 'code_file' }>
  onSaveVersion: SaveVersionFn
}) {
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [fileContent, setFileContent] = useState('')
  const [draft, setDraft] = useState('')
  const [size, setSize] = useState(content.sizeBytes)
  const [truncated, setTruncated] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await workspaceReadFile(conversationId, content.workspacePath)
      setFileContent(result.content)
      setDraft(result.content)
      setSize(result.size)
      setTruncated(result.truncated)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
      setSaving(false)
    }
  }, [conversationId, content.workspacePath])

  useEffect(() => {
    void reload()
  }, [artifactId, reload])

  const language = normalizeLang(content.language || guessLanguage(content.workspacePath))
  const dirty = draft !== fileContent

  const save = async () => {
    if (!dirty || saving || truncated) return
    setSaving(true)
    setError(null)
    try {
      await workspaceWriteFile(conversationId, content.workspacePath, draft)
      const bytes = new TextEncoder().encode(draft)
      await onSaveVersion({
        workspacePath: content.workspacePath,
        language,
        sizeBytes: bytes.byteLength,
        checksum: await sha256(bytes),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(draft)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // 剪贴板不可用时静默忽略
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        加载 {content.workspacePath}...
      </div>
    )
  }

  if (error && !fileContent) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <AlertCircle className="size-6 text-red-500" />
        <div className="text-sm font-medium">无法打开 workspace 文件</div>
        <div className="font-mono text-xs text-muted-foreground">{error}</div>
        <Button size="sm" variant="outline" onClick={() => void reload()}>
          <RefreshCw className="mr-1 size-3.5" />
          重试
        </Button>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
        <code className="min-w-0 flex-1 truncate font-mono">{content.workspacePath}</code>
        <span className="font-mono text-[10px]">{language}</span>
        <span className="font-mono text-[10px]">{(size / 1024).toFixed(1)} KB</span>
        {truncated && (
          <span className="font-mono text-[10px] text-amber-600" title="文件已截断，不可保存">
            已截断
          </span>
        )}
        <button
          type="button"
          onClick={() => void handleCopy()}
          title="复制代码"
          className="rounded p-1 transition-colors hover:text-foreground"
        >
          {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
        </button>
        <button
          type="button"
          onClick={() => void reload()}
          title="重新加载"
          className="rounded p-1 transition-colors hover:text-foreground"
        >
          <RefreshCw className="size-3.5" />
        </button>
      </div>

      <div className="min-h-0 flex-1">
        <ArtifactCodeEditor
          value={draft}
          onChange={setDraft}
          filename={content.workspacePath}
          type="code_file"
          readOnly={truncated}
        />
      </div>

      <EditFooter
        dirty={dirty && !truncated}
        saving={saving}
        error={error}
        onSave={save}
        onReset={() => {
          setDraft(fileContent)
          setError(null)
        }}
      />
    </div>
  )
}

type ProjectArtifactContent = Extract<ArtifactContent, { type: 'project' }>
type ProjectFileEntry = ProjectArtifactContent['files'][number]

interface ProjectTreeNode {
  name: string
  path: string
  children: ProjectTreeNode[]
  file?: ProjectFileEntry
}

function ProjectView({
  conversationId,
  content,
}: {
  conversationId: string
  content: ProjectArtifactContent
}) {
  const [activePath, setActivePath] = useState<string | null>(content.files[0]?.path ?? null)
  const [fileContent, setFileContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [truncated, setTruncated] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const tree = useMemo(() => buildProjectTree(content.files), [content.files])
  const activeFile = useMemo(
    () => content.files.find((file) => file.path === activePath) ?? null,
    [activePath, content.files],
  )
  const totalBytes = useMemo(
    () => content.files.reduce((sum, file) => sum + file.sizeBytes, 0),
    [content.files],
  )

  useEffect(() => {
    const firstPath = content.files[0]?.path ?? null
    setActivePath((current) =>
      current && content.files.some((file) => file.path === current) ? current : firstPath,
    )
  }, [content.files])

  useEffect(() => {
    if (!activePath) {
      setFileContent('')
      setTruncated(false)
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)
    workspaceReadFile(conversationId, activePath)
      .then((result) => {
        if (cancelled) return
        setFileContent(result.content)
        setTruncated(result.truncated)
      })
      .catch((err) => {
        if (cancelled) return
        setFileContent('')
        setTruncated(false)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [activePath, conversationId])

  if (content.files.length === 0) {
    return <Empty>该项目产物没有记录到 workspace 写入文件</Empty>
  }

  const activeName = activePath?.split('/').pop() ?? null
  const iconSpec = activeName ? getFileIcon(activeName) : null
  const ActiveIcon = iconSpec?.Icon

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="flex w-72 shrink-0 flex-col border-r bg-muted/10">
        <div className="shrink-0 border-b px-3 py-2">
          <div className="flex items-center gap-2 text-xs font-medium">
            <FolderGit2 className="size-4 text-muted-foreground" />
            <span>项目文件</span>
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            {content.files.length} 个文件 · {formatBytes(totalBytes)}
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            {tree.children.map((node) => (
              <ProjectTreeNodeRow
                key={node.path || node.name}
                node={node}
                depth={0}
                activePath={activePath}
                onSelect={setActivePath}
              />
            ))}
          </div>
        </ScrollArea>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b px-3 text-xs">
          {ActiveIcon ? (
            <ActiveIcon className={cn('size-4 shrink-0', iconSpec?.cls)} />
          ) : (
            <FileCode className="size-4 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate font-mono">{activePath}</span>
          {activeFile && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {formatBytes(activeFile.sizeBytes)}
            </span>
          )}
          {truncated && (
            <span className="shrink-0 rounded bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
              已截断
            </span>
          )}
        </div>

        <div className="min-h-0 flex-1">
          {loading ? (
            <div className="flex size-full items-center justify-center text-xs text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" />
              读取文件中
            </div>
          ) : error ? (
            <div className="flex size-full items-center justify-center p-6 text-center text-sm text-destructive">
              <div>
                <AlertCircle className="mx-auto mb-2 size-5" />
                <div>{error}</div>
              </div>
            </div>
          ) : activePath ? (
            <ArtifactCodeEditor
              value={fileContent}
              onChange={setFileContent}
              filename={activePath}
              type="project"
              readOnly
            />
          ) : (
            <Empty>请选择一个文件</Empty>
          )}
        </div>
      </section>
    </div>
  )
}

function ProjectTreeNodeRow({
  node,
  depth,
  activePath,
  onSelect,
}: {
  node: ProjectTreeNode
  depth: number
  activePath: string | null
  onSelect: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(depth < 2)
  const isFile = Boolean(node.file)
  const iconSpec = isFile ? getFileIcon(node.name) : { Icon: FolderTree, cls: 'text-amber-500' }
  const Icon = iconSpec.Icon
  const selected = isFile && node.file?.path === activePath

  return (
    <div>
      <button
        type="button"
        className={cn(
          'flex h-7 w-full items-center gap-1.5 rounded px-1.5 text-left text-xs transition',
          selected ? 'bg-primary/10 text-primary' : 'text-foreground/80 hover:bg-muted',
        )}
        style={{ paddingLeft: 6 + depth * 12 }}
        onClick={() => {
          if (isFile && node.file) onSelect(node.file.path)
          else setExpanded((value) => !value)
        }}
        title={node.path}
      >
        {isFile ? (
          <span className="size-3.5 shrink-0" />
        ) : expanded ? (
          <ChevronRight className="size-3.5 shrink-0 rotate-90 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <Icon className={cn('size-3.5 shrink-0', iconSpec.cls)} />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        {node.file && (
          <span className="shrink-0 text-[10px] text-muted-foreground">
            {formatBytes(node.file.sizeBytes)}
          </span>
        )}
      </button>
      {!isFile && expanded && (
        <div>
          {node.children.map((child) => (
            <ProjectTreeNodeRow
              key={child.path || child.name}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function buildProjectTree(files: readonly ProjectFileEntry[]): ProjectTreeNode {
  const root: ProjectTreeNode = { name: '', path: '', children: [] }
  for (const file of files) {
    const parts = file.path.split('/').filter(Boolean)
    let cursor = root
    for (let index = 0; index < parts.length; index++) {
      const name = parts[index]
      const nodePath = parts.slice(0, index + 1).join('/')
      const leaf = index === parts.length - 1
      let next = cursor.children.find((child) => child.name === name)
      if (!next) {
        next = { name, path: nodePath, children: [] }
        cursor.children.push(next)
      }
      if (leaf) next.file = file
      cursor = next
    }
  }
  sortProjectTree(root)
  return root
}

function sortProjectTree(node: ProjectTreeNode): void {
  node.children.sort((a, b) => {
    const aFile = Boolean(a.file)
    const bFile = Boolean(b.file)
    if (aFile !== bFile) return aFile ? 1 : -1
    return a.name.localeCompare(b.name)
  })
  for (const child of node.children) sortProjectTree(child)
}

// ─── version compare ──────────────────────────────────
function VersionCompareView({
  versions,
  baseId,
  targetId,
  onBaseChange,
  onTargetChange,
}: {
  versions: ArtifactRow[]
  baseId: string
  targetId: string
  onBaseChange: (id: string) => void
  onTargetChange: (id: string) => void
}) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const diffStyles = useMemo(() => buildDiffStyles(isDark), [isDark])
  const base = versions.find((v) => v.id === baseId) ?? versions[0]
  const target = versions.find((v) => v.id === targetId) ?? versions[versions.length - 1]
  const diff = useMemo(() => {
    if (!base || !target) return null
    return buildArtifactVersionDiff(base.content as ArtifactContent, target.content as ArtifactContent)
  }, [base, target])

  if (!base || !target || !diff) {
    return <Empty>无法加载版本对比</Empty>
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/20 px-3 py-2 text-xs">
        <GitCompare className="size-3.5 shrink-0 text-muted-foreground" />
        <label className="flex min-w-0 items-center gap-1">
          <span className="shrink-0 text-muted-foreground">基准</span>
          <VersionSelect versions={versions} value={base.id} onChange={onBaseChange} />
        </label>
        <span className="shrink-0 text-muted-foreground">→</span>
        <label className="flex min-w-0 items-center gap-1">
          <span className="shrink-0 text-muted-foreground">目标</span>
          <VersionSelect versions={versions} value={target.id} onChange={onTargetChange} />
        </label>
      </div>

      {diff.status === 'unsupported' ? (
        <div className="flex min-h-0 flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">
          {diff.reason}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto bg-background pending-diff-body">
          {diff.sections.map((section) => (
            <section key={section.key} className="border-b last:border-b-0">
              <div className="flex items-center justify-between gap-3 border-b bg-muted/20 px-4 py-2 text-xs">
                <code className="min-w-0 truncate font-mono">{section.title}</code>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                  v{base.version} → v{target.version}
                </span>
              </div>
              <ReactDiffViewer
                oldValue={section.oldText}
                newValue={section.newText}
                splitView={true}
                useDarkTheme={isDark}
                compareMethod={DiffMethod.WORDS_WITH_SPACE}
                leftTitle={`v${base.version}`}
                rightTitle={`v${target.version}`}
                styles={diffStyles}
              />
            </section>
          ))}
        </div>
      )}
    </div>
  )
}

function VersionSelect({
  versions,
  value,
  onChange,
}: {
  versions: ArtifactRow[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="max-w-36 rounded border bg-background px-2 py-1 text-xs"
    >
      {versions.map((version) => (
        <option key={version.id} value={version.id}>
          v{version.version} · {version.title}
        </option>
      ))}
    </select>
  )
}

// ─── diff ───────────────────────────────────────────────
function DiffArtifactView({ content }: { content: Extract<ArtifactContent, { type: 'diff' }> }) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const diffStyles = useMemo(() => buildDiffStyles(isDark), [isDark])
  const pair = useMemo(() => diffHunksToTextPair(content.hunks), [content.hunks])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs">
        <GitCompare className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
          历史 diff · 只读
        </span>
        <span className="text-muted-foreground">目标产物</span>
        <code className="min-w-0 flex-1 truncate font-mono">{content.targetArtifactId}</code>
        <span
          className={cn(
            'rounded border px-1.5 py-0.5 text-[10px]',
            content.applied
              ? 'border-success/30 bg-success/10 text-success'
              : 'border-warning/30 bg-warning/10 text-warning',
          )}
        >
          {content.applied ? '已应用' : '未应用'}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-background pending-diff-body">
        <ReactDiffViewer
          oldValue={pair.oldText}
          newValue={pair.newText}
          splitView={true}
          useDarkTheme={isDark}
          compareMethod={DiffMethod.WORDS_WITH_SPACE}
          leftTitle="修改前"
          rightTitle="修改后"
          styles={diffStyles}
        />
      </div>
    </div>
  )
}

// ─── 共享小组件 ───────────────────────────────────────
function EditFooter({
  dirty,
  saving,
  error,
  onSave,
  onReset,
}: {
  dirty: boolean
  saving: boolean
  error: string | null
  onSave: () => void
  onReset: () => void
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-t px-3 py-2">
      <Button size="sm" disabled={!dirty || saving} onClick={onSave}>
        <Save className="size-3.5" />
        {saving ? '提交中…' : '提交为新版本'}
      </Button>
      <Button size="sm" variant="ghost" disabled={!dirty || saving} onClick={onReset}>
        <RotateCcw className="size-3.5" />
        重置
      </Button>
      {error ? (
        <span className="truncate text-xs text-red-600 dark:text-red-400">{error}</span>
      ) : (
        <span className="text-xs text-muted-foreground">
          {dirty ? '已修改 · 提交将创建新版本' : '编辑后提交为新版本'}
        </span>
      )}
    </div>
  )
}

function ViewTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs transition',
        active
          ? 'border-primary text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">
      <div className="flex flex-col items-center gap-2">
        <ChevronRight className="size-5" />
        <div>{children}</div>
      </div>
    </div>
  )
}

function TypeIcon({ type }: { type: string }) {
  if (type === 'image') return <ImageIcon className="size-4 text-muted-foreground" />
  if (type === 'document') return <FileText className="size-4 text-muted-foreground" />
  if (type === 'code_file') return <FileCode className="size-4 text-muted-foreground" />
  if (type === 'diff') return <GitCompare className="size-4 text-muted-foreground" />
  if (type === 'ppt') return <Presentation className="size-4 text-muted-foreground" />
  if (type === 'project') return <FolderGit2 className="size-4 text-muted-foreground" />
  return <Layers className="size-4 text-muted-foreground" />
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`
}

function openPreviewInNewTab(artifactId: string): void {
  window.open(artifactPreviewPath(artifactId), '_blank', 'noopener,noreferrer')
}

function copyPreviewUrl(artifactId: string): void {
  const url = new URL(artifactPreviewPath(artifactId), window.location.origin).toString()
  navigator.clipboard?.writeText(url).catch(() => {})
}

function diffHunksToTextPair(hunks: DiffHunk[]): { oldText: string; newText: string } {
  const oldLines: string[] = []
  const newLines: string[] = []

  for (const hunk of hunks) {
    for (const line of hunk.lines) {
      if (line.startsWith('\\ No newline') || isUnifiedDiffMetadataLine(line)) continue
      if (line.startsWith('+')) {
        newLines.push(line.slice(1))
      } else if (line.startsWith('-')) {
        oldLines.push(line.slice(1))
      } else if (line.startsWith(' ')) {
        const text = line.slice(1)
        oldLines.push(text)
        newLines.push(text)
      } else {
        oldLines.push(line)
        newLines.push(line)
      }
    }
  }

  return { oldText: oldLines.join('\n'), newText: newLines.join('\n') }
}

function isUnifiedDiffMetadataLine(line: string): boolean {
  return /^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@/.test(line)
}

function guessLanguage(filePath: string): string {
  return normalizeLang(filePath.split('.').pop()?.toLowerCase() ?? '')
}

async function sha256(bytes: Uint8Array): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    return `size:${bytes.byteLength}`
  }
  const buffer = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(buffer).set(bytes)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}
