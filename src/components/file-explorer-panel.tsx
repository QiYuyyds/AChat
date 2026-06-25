'use client'

import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  Database,
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileCog,
  FileImage,
  FileJson,
  FileKey,
  FileLock,
  FileSpreadsheet,
  FileTerminal,
  FileText,
  FileType,
  FileVideo,
  Folder,
  FolderOpen,
  Loader2,
  Package,
  Palette,
  RefreshCw,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { workspaceListDir } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useActiveConversation, useAppStore } from '@/stores/app-store'

interface DirEntry {
  name: string
  isDirectory: boolean
  size?: number
}

interface DirNode {
  relPath: string
  loaded: boolean
  expanded: boolean
  entries?: DirEntry[]
  error?: string
}

/**
 * 右侧文件浏览器面板。
 *
 * - 与 ArtifactPreviewPanel 互斥（store.fileExplorerOpen / previewArtifactId 同时只能一个有效）
 * - 列出当前 conversation workspace 的文件树
 * - 点击文件夹展开 / 收起；点击文件打开为中间 tab（store.openFile）
 */
export function FileExplorerPanel() {
  const conv = useActiveConversation()
  const open = useAppStore((s) => s.fileExplorerOpen)
  const setFileExplorerOpen = useAppStore((s) => s.setFileExplorerOpen)
  const openFile = useAppStore((s) => s.openFile)

  // 树状态：以 relPath 为 key 记录该节点是否展开 + 已加载条目
  const [nodes, setNodes] = useState<Record<string, DirNode>>({})
  const [loadingRoot, setLoadingRoot] = useState(false)

  const loadDir = useCallback(
    async (relPath: string) => {
      if (!conv) return
      setNodes((prev) => ({
        ...prev,
        [relPath]: {
          ...(prev[relPath] ?? { relPath }),
          relPath,
          expanded: true,
          loaded: false,
        },
      }))
      try {
        const result = await workspaceListDir(conv.id, relPath)
        setNodes((prev) => ({
          ...prev,
          [relPath]: {
            relPath,
            loaded: true,
            expanded: true,
            entries: result.entries,
          },
        }))
      } catch (err) {
        setNodes((prev) => ({
          ...prev,
          [relPath]: {
            relPath,
            loaded: true,
            expanded: true,
            error: err instanceof Error ? err.message : String(err),
          },
        }))
      }
    },
    [conv],
  )

  const toggleDir = (relPath: string) => {
    const cur = nodes[relPath]
    if (cur?.expanded) {
      // 已展开 → 收起（三种情况互斥，否则收起会被下面重新展开覆盖）
      setNodes((prev) => ({ ...prev, [relPath]: { ...prev[relPath], expanded: false } }))
    } else if (!cur || !cur.loaded) {
      void loadDir(relPath)
    } else {
      setNodes((prev) => ({ ...prev, [relPath]: { ...prev[relPath], expanded: true } }))
    }
  }

  const refresh = useCallback(() => {
    if (!conv) return
    setNodes({})
    setLoadingRoot(true)
    void loadDir('').finally(() => setLoadingRoot(false))
  }, [conv, loadDir])

  // 切换会话时清空 + 重新加载根；首次打开时同样加载
  useEffect(() => {
    if (!open || !conv) return
    setNodes({})
    setLoadingRoot(true)
    void loadDir('').finally(() => setLoadingRoot(false))
  }, [open, conv, loadDir])

  if (!open || !conv) return null

  return (
    <aside className="flex w-80 min-w-[260px] shrink-0 flex-col border-l bg-card max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full max-md:min-w-0">
      <header className="flex shrink-0 items-center justify-between border-b px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <Folder className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate text-sm font-medium">文件</span>
          <span className="truncate font-mono text-[10px] text-muted-foreground" title={conv.workspaceBoundPath ?? ''}>
            {conv.workspaceMode === 'local'
              ? (conv.workspaceBoundPath ?? '').split(/[\\/]/).pop()
              : '沙箱'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button size="icon" variant="ghost" onClick={refresh} title="刷新" disabled={loadingRoot}>
            <RefreshCw className={cn('size-4', loadingRoot && 'animate-spin')} />
          </Button>
          <Button size="icon" variant="ghost" onClick={() => setFileExplorerOpen(false)} title="关闭">
            <X className="size-4" />
          </Button>
        </div>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="py-1">
          <DirTreeNode
            relPath=""
            indent={0}
            nodes={nodes}
            onToggleDir={toggleDir}
            onOpenFile={(p) => openFile(conv.id, p)}
          />
        </div>
      </ScrollArea>
    </aside>
  )
}

function DirTreeNode({
  relPath,
  indent,
  nodes,
  onToggleDir,
  onOpenFile,
}: {
  relPath: string
  indent: number
  nodes: Record<string, DirNode>
  onToggleDir: (path: string) => void
  onOpenFile: (path: string) => void
}) {
  const node = nodes[relPath]
  if (!node) return null

  return (
    <>
      {/* root 自身不渲染行，从子条目开始 */}
      {node.expanded && (
        <>
          {!node.loaded && (
            <div
              className="flex items-center gap-1.5 px-3 py-1 text-xs text-muted-foreground"
              style={{ paddingLeft: 12 + indent * 14 }}
            >
              <Loader2 className="size-3 animate-spin" />
              加载中...
            </div>
          )}
          {node.error && (
            <div
              className="px-3 py-1 text-xs text-red-600"
              style={{ paddingLeft: 12 + indent * 14 }}
            >
              {node.error}
            </div>
          )}
          {node.entries?.length === 0 && (
            <div
              className="px-3 py-1 text-xs text-muted-foreground"
              style={{ paddingLeft: 12 + indent * 14 }}
            >
              (空)
            </div>
          )}
          {node.entries?.map((e) => {
            const childPath = relPath === '' ? e.name : `${relPath}/${e.name}`
            const childNode = nodes[childPath]
            if (e.isDirectory) {
              const expanded = !!childNode?.expanded
              return (
                <div key={childPath}>
                  <button
                    type="button"
                    onClick={() => onToggleDir(childPath)}
                    className="flex w-full items-center gap-1 px-3 py-1 text-left text-xs hover:bg-accent"
                    style={{ paddingLeft: 12 + indent * 14 }}
                  >
                    {expanded ? (
                      <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
                    )}
                    {expanded ? (
                      <FolderOpen className="size-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
                    ) : (
                      <Folder className="size-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
                    )}
                    <span className="truncate">{e.name}</span>
                  </button>
                  {expanded && (
                    <DirTreeNode
                      relPath={childPath}
                      indent={indent + 1}
                      nodes={nodes}
                      onToggleDir={onToggleDir}
                      onOpenFile={onOpenFile}
                    />
                  )}
                </div>
              )
            }
            const { Icon: FileIcon, cls } = getFileIcon(e.name)
            return (
              <button
                key={childPath}
                type="button"
                onClick={() => onOpenFile(childPath)}
                className="flex w-full items-center gap-1 px-3 py-1 text-left text-xs hover:bg-accent"
                style={{ paddingLeft: 12 + indent * 14 + 14 }}
              >
                <FileIcon className={cn('size-3.5 shrink-0', cls)} />
                <span className="truncate">{e.name}</span>
              </button>
            )
          })}
        </>
      )}
    </>
  )
}

type FileIconSpec = { Icon: LucideIcon; cls: string }

// 扩展名（小写，含 .gitignore 这类点文件取到的 "gitignore"）→ 图标 + 颜色，VS Code 风格
const EXT_ICON: Record<string, FileIconSpec> = {
  // TypeScript / JavaScript
  ts: { Icon: FileCode, cls: 'text-blue-500' },
  tsx: { Icon: FileCode, cls: 'text-blue-500' },
  mts: { Icon: FileCode, cls: 'text-blue-500' },
  cts: { Icon: FileCode, cls: 'text-blue-500' },
  js: { Icon: FileCode, cls: 'text-yellow-500' },
  jsx: { Icon: FileCode, cls: 'text-yellow-500' },
  mjs: { Icon: FileCode, cls: 'text-yellow-500' },
  cjs: { Icon: FileCode, cls: 'text-yellow-500' },
  // 数据 / 配置
  json: { Icon: FileJson, cls: 'text-amber-500' },
  jsonc: { Icon: FileJson, cls: 'text-amber-500' },
  json5: { Icon: FileJson, cls: 'text-amber-500' },
  yaml: { Icon: FileCog, cls: 'text-muted-foreground' },
  yml: { Icon: FileCog, cls: 'text-muted-foreground' },
  toml: { Icon: FileCog, cls: 'text-muted-foreground' },
  ini: { Icon: FileCog, cls: 'text-muted-foreground' },
  conf: { Icon: FileCog, cls: 'text-muted-foreground' },
  cfg: { Icon: FileCog, cls: 'text-muted-foreground' },
  env: { Icon: FileKey, cls: 'text-amber-500' },
  editorconfig: { Icon: FileCog, cls: 'text-muted-foreground' },
  prettierrc: { Icon: FileCog, cls: 'text-muted-foreground' },
  eslintrc: { Icon: FileCog, cls: 'text-muted-foreground' },
  npmrc: { Icon: FileCog, cls: 'text-muted-foreground' },
  gitignore: { Icon: FileCode, cls: 'text-orange-500' },
  gitattributes: { Icon: FileCode, cls: 'text-orange-500' },
  // 文档
  md: { Icon: FileText, cls: 'text-sky-400' },
  mdx: { Icon: FileText, cls: 'text-sky-400' },
  markdown: { Icon: FileText, cls: 'text-sky-400' },
  txt: { Icon: FileText, cls: 'text-muted-foreground' },
  log: { Icon: FileText, cls: 'text-muted-foreground' },
  pdf: { Icon: FileText, cls: 'text-red-500' },
  doc: { Icon: FileText, cls: 'text-blue-600' },
  docx: { Icon: FileText, cls: 'text-blue-600' },
  rtf: { Icon: FileText, cls: 'text-muted-foreground' },
  // 标记 / 样式
  html: { Icon: FileCode, cls: 'text-orange-500' },
  htm: { Icon: FileCode, cls: 'text-orange-500' },
  xml: { Icon: FileCode, cls: 'text-orange-400' },
  css: { Icon: Palette, cls: 'text-sky-500' },
  scss: { Icon: Palette, cls: 'text-pink-500' },
  sass: { Icon: Palette, cls: 'text-pink-500' },
  less: { Icon: Palette, cls: 'text-blue-500' },
  vue: { Icon: FileCode, cls: 'text-emerald-500' },
  svelte: { Icon: FileCode, cls: 'text-orange-600' },
  astro: { Icon: FileCode, cls: 'text-orange-500' },
  // 编程语言
  py: { Icon: FileCode, cls: 'text-sky-500' },
  rb: { Icon: FileCode, cls: 'text-red-500' },
  go: { Icon: FileCode, cls: 'text-cyan-500' },
  rs: { Icon: FileCode, cls: 'text-orange-600' },
  java: { Icon: FileCode, cls: 'text-red-600' },
  kt: { Icon: FileCode, cls: 'text-purple-500' },
  c: { Icon: FileCode, cls: 'text-blue-600' },
  h: { Icon: FileCode, cls: 'text-blue-600' },
  cpp: { Icon: FileCode, cls: 'text-blue-600' },
  cc: { Icon: FileCode, cls: 'text-blue-600' },
  hpp: { Icon: FileCode, cls: 'text-blue-600' },
  cs: { Icon: FileCode, cls: 'text-violet-500' },
  php: { Icon: FileCode, cls: 'text-indigo-500' },
  swift: { Icon: FileCode, cls: 'text-orange-500' },
  sql: { Icon: Database, cls: 'text-sky-600' },
  // Shell
  sh: { Icon: FileTerminal, cls: 'text-green-500' },
  bash: { Icon: FileTerminal, cls: 'text-green-500' },
  zsh: { Icon: FileTerminal, cls: 'text-green-500' },
  fish: { Icon: FileTerminal, cls: 'text-green-500' },
  ps1: { Icon: FileTerminal, cls: 'text-blue-400' },
  // 图片
  png: { Icon: FileImage, cls: 'text-purple-500' },
  jpg: { Icon: FileImage, cls: 'text-purple-500' },
  jpeg: { Icon: FileImage, cls: 'text-purple-500' },
  gif: { Icon: FileImage, cls: 'text-purple-500' },
  webp: { Icon: FileImage, cls: 'text-purple-500' },
  bmp: { Icon: FileImage, cls: 'text-purple-500' },
  ico: { Icon: FileImage, cls: 'text-purple-500' },
  avif: { Icon: FileImage, cls: 'text-purple-500' },
  svg: { Icon: FileImage, cls: 'text-pink-500' },
  // 音视频
  mp4: { Icon: FileVideo, cls: 'text-rose-500' },
  mov: { Icon: FileVideo, cls: 'text-rose-500' },
  avi: { Icon: FileVideo, cls: 'text-rose-500' },
  mkv: { Icon: FileVideo, cls: 'text-rose-500' },
  webm: { Icon: FileVideo, cls: 'text-rose-500' },
  mp3: { Icon: FileAudio, cls: 'text-amber-600' },
  wav: { Icon: FileAudio, cls: 'text-amber-600' },
  flac: { Icon: FileAudio, cls: 'text-amber-600' },
  ogg: { Icon: FileAudio, cls: 'text-amber-600' },
  m4a: { Icon: FileAudio, cls: 'text-amber-600' },
  // 压缩包
  zip: { Icon: FileArchive, cls: 'text-yellow-600' },
  tar: { Icon: FileArchive, cls: 'text-yellow-600' },
  gz: { Icon: FileArchive, cls: 'text-yellow-600' },
  tgz: { Icon: FileArchive, cls: 'text-yellow-600' },
  rar: { Icon: FileArchive, cls: 'text-yellow-600' },
  '7z': { Icon: FileArchive, cls: 'text-yellow-600' },
  bz2: { Icon: FileArchive, cls: 'text-yellow-600' },
  xz: { Icon: FileArchive, cls: 'text-yellow-600' },
  // 表格
  csv: { Icon: FileSpreadsheet, cls: 'text-green-600' },
  tsv: { Icon: FileSpreadsheet, cls: 'text-green-600' },
  xls: { Icon: FileSpreadsheet, cls: 'text-green-600' },
  xlsx: { Icon: FileSpreadsheet, cls: 'text-green-600' },
  // 字体
  ttf: { Icon: FileType, cls: 'text-pink-400' },
  otf: { Icon: FileType, cls: 'text-pink-400' },
  woff: { Icon: FileType, cls: 'text-pink-400' },
  woff2: { Icon: FileType, cls: 'text-pink-400' },
  // 证书 / 密钥
  pem: { Icon: FileKey, cls: 'text-amber-500' },
  key: { Icon: FileKey, cls: 'text-amber-500' },
  crt: { Icon: FileKey, cls: 'text-amber-500' },
  cert: { Icon: FileKey, cls: 'text-amber-500' },
  // 锁文件扩展名
  lock: { Icon: FileLock, cls: 'text-muted-foreground' },
}

// 完整文件名（小写）→ 图标，优先级高于扩展名
const NAME_ICON: Record<string, FileIconSpec> = {
  'package.json': { Icon: Package, cls: 'text-red-400' },
  'package-lock.json': { Icon: FileLock, cls: 'text-muted-foreground' },
  'pnpm-lock.yaml': { Icon: FileLock, cls: 'text-muted-foreground' },
  'yarn.lock': { Icon: FileLock, cls: 'text-muted-foreground' },
  'bun.lockb': { Icon: FileLock, cls: 'text-muted-foreground' },
  'cargo.lock': { Icon: FileLock, cls: 'text-muted-foreground' },
  'tsconfig.json': { Icon: FileCog, cls: 'text-blue-500' },
  dockerfile: { Icon: FileCode, cls: 'text-sky-500' },
  makefile: { Icon: FileCog, cls: 'text-muted-foreground' },
}

const DEFAULT_FILE_ICON: FileIconSpec = { Icon: File, cls: 'text-muted-foreground' }

// 根据文件名挑选图标：完整文件名 > 特例前缀 > 扩展名 > 默认
export function getFileIcon(name: string): FileIconSpec {
  const lower = name.toLowerCase()
  if (NAME_ICON[lower]) return NAME_ICON[lower]
  if (lower.startsWith('readme')) return { Icon: BookOpen, cls: 'text-sky-500' }
  if (lower.startsWith('.env')) return { Icon: FileKey, cls: 'text-amber-500' }
  if (lower.startsWith('license') || lower.startsWith('licence')) return { Icon: FileText, cls: 'text-amber-500' }
  const ext = lower.includes('.') ? lower.slice(lower.lastIndexOf('.') + 1) : ''
  return EXT_ICON[ext] ?? DEFAULT_FILE_ICON
}
