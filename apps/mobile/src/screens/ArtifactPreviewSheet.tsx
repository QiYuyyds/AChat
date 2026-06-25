import { useMemo, useState } from 'react'
import { Code2, Download, Eye, FileCode2, FileText, Image as ImageIcon, Layers, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { classifyHunkLine, type DiffLine } from '../lib/diff'
import { formatTime } from '../lib/format'
import type { MobileArtifact, MobileArtifactContent } from '../types'
import { DiffView } from './DiffView'

export function ArtifactPreviewSheet({
  artifact,
  loading,
  error,
  onClose,
}: {
  artifact: MobileArtifact | null
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  if (!artifact && !loading && !error) return null

  return (
    <div className="artifact-sheet-layer" role="dialog" aria-modal="true" aria-label="产物预览">
      <button type="button" className="artifact-sheet-backdrop" aria-label="关闭产物预览" onClick={onClose} />
      <section className="artifact-sheet">
        <div className="artifact-sheet-handle" aria-hidden="true" />
        <header className="artifact-sheet-header">
          <div className="artifact-title-row">
            <ArtifactTypeIcon type={artifact?.type} />
            <div>
              <p className="eyebrow">Artifact</p>
              <h2>{artifact?.title ?? '载入产物'}</h2>
              {artifact && (
                <p>
                  {artifact.type} · v{artifact.version} · {formatTime(artifact.createdAt)}
                </p>
              )}
            </div>
          </div>
          <button type="button" className="chrome-button" aria-label="关闭产物预览" onClick={onClose}>
            <X className="chrome-icon" aria-hidden="true" />
          </button>
        </header>

        {loading ? (
          <div className="artifact-loading">正在载入产物...</div>
        ) : error ? (
          <div className="error-banner">{error}</div>
        ) : artifact ? (
          <ArtifactContentView artifact={artifact} />
        ) : null}
      </section>
    </div>
  )
}

function ArtifactContentView({ artifact }: { artifact: MobileArtifact }) {
  const content = artifact.content
  switch (content.type) {
    case 'web_app':
      return <WebAppArtifact content={content} />
    case 'document':
      return <DocumentArtifact content={content} />
    case 'image':
      return <ImageArtifact content={content} />
    case 'code_file':
      return <CodeFileArtifact content={content} />
    case 'diff':
      return <DiffArtifact content={content} />
  }
}

function WebAppArtifact({ content }: { content: Extract<MobileArtifactContent, { type: 'web_app' }> }) {
  const [view, setView] = useState<'preview' | 'source'>('preview')
  const [activeFile, setActiveFile] = useState(content.entry)
  const fileNames = Object.keys(content.files)
  const html = useMemo(() => buildIframeHtml(content.files, content.entry), [content.files, content.entry])
  const source = content.files[activeFile] ?? content.files[content.entry] ?? ''

  return (
    <div className="artifact-view">
      <div className="artifact-toolbar">
        <SegmentedButton active={view === 'preview'} onClick={() => setView('preview')}>
          <Eye className="button-icon" aria-hidden="true" />
          预览
        </SegmentedButton>
        <SegmentedButton active={view === 'source'} onClick={() => setView('source')}>
          <Code2 className="button-icon" aria-hidden="true" />
          源码
        </SegmentedButton>
      </div>

      {view === 'preview' ? (
        <iframe
          key={html.length}
          title="Artifact preview"
          className="artifact-web-frame"
          sandbox="allow-scripts"
          srcDoc={html}
        />
      ) : (
        <div className="artifact-source-view">
          {fileNames.length > 1 && (
            <select
              value={activeFile}
              onChange={(event) => setActiveFile(event.target.value)}
              className="artifact-file-select"
            >
              {fileNames.map((fileName) => (
                <option key={fileName} value={fileName}>
                  {fileName}
                </option>
              ))}
            </select>
          )}
          <pre>{source}</pre>
        </div>
      )}
    </div>
  )
}

function DocumentArtifact({ content }: { content: Extract<MobileArtifactContent, { type: 'document' }> }) {
  return (
    <article className="artifact-document mobile-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.content}</ReactMarkdown>
    </article>
  )
}

function ImageArtifact({ content }: { content: Extract<MobileArtifactContent, { type: 'image' }> }) {
  return (
    <div className="artifact-image-wrap">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={content.url} alt={content.alt} />
      <p>{content.alt}</p>
    </div>
  )
}

function CodeFileArtifact({ content }: { content: Extract<MobileArtifactContent, { type: 'code_file' }> }) {
  return (
    <div className="artifact-empty">
      <FileCode2 className="artifact-empty-icon" aria-hidden="true" />
      <h3>{content.workspacePath}</h3>
      <p>
        {content.language} · {(content.sizeBytes / 1024).toFixed(1)} KB · 工作区文件内容暂不通过移动端直读
      </p>
    </div>
  )
}

function DiffArtifact({ content }: { content: Extract<MobileArtifactContent, { type: 'diff' }> }) {
  return (
    <div className="artifact-source-view">
      <DiffView lines={hunksToDiffLines(content.hunks)} />
    </div>
  )
}

function hunksToDiffLines(hunks: Extract<MobileArtifactContent, { type: 'diff' }>['hunks']): DiffLine[] {
  const lines: DiffLine[] = []
  for (const hunk of hunks) {
    lines.push({
      kind: 'hunk',
      text: `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`,
    })
    for (const raw of hunk.lines) {
      lines.push({ kind: classifyHunkLine(raw), text: raw.replace(/^[+\- ]/, '') })
    }
  }
  return lines
}

function SegmentedButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button type="button" className={active ? 'segmented-button active' : 'segmented-button'} onClick={onClick}>
      {children}
    </button>
  )
}

function ArtifactTypeIcon({ type }: { type?: string }) {
  if (type === 'document') return <FileText className="artifact-type-icon" aria-hidden="true" />
  if (type === 'image') return <ImageIcon className="artifact-type-icon" aria-hidden="true" />
  if (type === 'code_file' || type === 'diff') return <FileCode2 className="artifact-type-icon" aria-hidden="true" />
  if (type === 'web_app') return <Layers className="artifact-type-icon" aria-hidden="true" />
  return <Download className="artifact-type-icon" aria-hidden="true" />
}

function buildIframeHtml(files: Record<string, string>, entry: string): string {
  const html = files[entry] ?? files['index.html'] ?? ''
  const css = files['style.css'] ?? files['styles.css'] ?? ''
  const js = files['script.js'] ?? files['main.js'] ?? files['app.js'] ?? ''

  const styleTag = css ? `<style>\n${css}\n</style>` : ''
  const scriptTag = js ? `<script>(function(){\n${js}\n})();<` + '/script>' : ''

  if (/<\/head>/i.test(html)) {
    return html.replace(/<\/head>/i, `${styleTag}\n</head>`).replace(/<\/body>/i, `${scriptTag}\n</body>`)
  }

  return [
    '<!doctype html>',
    '<html lang="zh-CN">',
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1">',
    styleTag,
    '</head>',
    '<body>',
    html,
    scriptTag,
    '</body>',
    '</html>',
  ].join('\n')
}
