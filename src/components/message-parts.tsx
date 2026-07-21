'use client'

import { Check, ChevronDown, ChevronRight, Copy, Download, ExternalLink, File as FileIcon, FileText, FolderGit2, Image as ImageIcon, Layers, Loader2, Package, Presentation, Rocket, Sparkles, Terminal, XCircle } from 'lucide-react'
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { PlanStep, PlanStepStatus } from '@/shared/types'

import { Card, CardContent } from '@/components/ui/card'
import { AttachmentChip } from '@/components/attachment-chip'
import { Button } from '@/components/ui/button'
import { CodeBlock } from '@/components/code-block'
import { DiffBlock } from '@/components/diff-block'
import { Markdown } from '@/components/markdown'
import { formatDuration } from '@/lib/format'
import { artifactPreviewPath } from '@/lib/artifact-preview'
import { deployConversationArtifact, fetchArtifact } from '@/lib/api'
import { getToolDisplayName, isBashToolName } from '@/lib/tool-display'
import { useElapsedTimer } from '@/lib/use-elapsed-timer'
import { cn } from '@/lib/utils'
import type { MessagePart } from '@/shared/types'
import { useAppStore } from '@/stores/app-store'

// ─── Part type classification ──────────────────────────
const PROCESS_PART_TYPES = new Set<string>(['thinking', 'tool_use', 'file_write_preview'])

type MessageStatus = 'streaming' | 'complete' | 'error' | 'aborted' | 'interrupted'

type ResultEntry = { result: unknown; isError: boolean; endedAt?: number }

// ─── PartList: 调度入口 ─────────────────────────────────
export function PartList({
  parts,
  conversationId,
  messageStatus = 'complete',
  messageRole = 'agent',
}: {
  parts: MessagePart[]
  conversationId: string
  messageStatus?: MessageStatus
  messageRole?: 'user' | 'agent' | 'system'
}) {
  const isUser = messageRole === 'user'

  const { resultByCallId, items, lastContentPartIndex } = useMemo(() => {
    // 把 tool_result 按 callId 提前到对应 tool_use 的状态里
    const byCallId = new Map<string, ResultEntry>()
    for (const p of parts) {
      if (p.type === 'tool_result') {
        byCallId.set(p.callId, { result: p.result, isError: p.isError, endedAt: p.endedAt })
      }
    }

    // 双段聚类：连续过程型 part 归入 ProcessSegment，结论型各自独立
    type RenderItem =
      | { kind: 'process'; parts: Array<{ part: MessagePart; index: number }> }
      | { kind: 'conclusion'; part: MessagePart; index: number }

    const renderItems: RenderItem[] = []
    let currentProcess: Array<{ part: MessagePart; index: number }> = []

    parts.forEach((p, i) => {
      if (p.type === 'tool_result') return
      if (PROCESS_PART_TYPES.has(p.type)) {
        currentProcess.push({ part: p, index: i })
      } else {
        if (currentProcess.length > 0) {
          renderItems.push({ kind: 'process', parts: currentProcess })
          currentProcess = []
        }
        renderItems.push({ kind: 'conclusion', part: p, index: i })
      }
    })
    if (currentProcess.length > 0) {
      renderItems.push({ kind: 'process', parts: currentProcess })
    }

    // 计算最后一个有 content 的 part index（用于判断 thinking/file_write_preview 是否正在流式）
    let lastIdx = -1
    parts.forEach((p, i) => {
      if (p.type === 'text' || p.type === 'thinking' || p.type === 'code' || p.type === 'file_write_preview') {
        lastIdx = i
      }
    })

    return { resultByCallId: byCallId, items: renderItems, lastContentPartIndex: lastIdx }
  }, [parts])

  return (
    <div className="space-y-3">
      {items.map((item, i) => {
        if (item.kind === 'process') {
          return (
            <ProcessSegment
              key={`seg-${i}`}
              segmentParts={item.parts}
              resultByCallId={resultByCallId}
              messageStatus={messageStatus}
              lastContentPartIndex={lastContentPartIndex}
            />
          )
        }
        const isLastContentPart = item.index === lastContentPartIndex
        const isStreaming = messageStatus === 'streaming' && isLastContentPart
        return (
          <PartRenderer
            key={`p-${item.index}`}
            part={item.part}
            conversationId={conversationId}
            isStreaming={isStreaming}
            isUser={isUser}
            messageStatus={messageStatus}
          />
        )
      })}
    </div>
  )
}

// ─── ProcessSegment: 过程段折叠/展开 ──────────────────────
function ProcessSegment({
  segmentParts,
  resultByCallId,
  messageStatus,
  lastContentPartIndex,
}: {
  segmentParts: Array<{ part: MessagePart; index: number }>
  resultByCallId: Map<string, ResultEntry>
  messageStatus: MessageStatus
  lastContentPartIndex: number
}) {
  const isStreaming = messageStatus === 'streaming'
  const [userOverride, setUserOverride] = useState<boolean | null>(null)

  // status 变化时重置用户手动覆盖
  useEffect(() => {
    setUserOverride(null)
  }, [isStreaming])

  const expanded = userOverride !== null ? userOverride : isStreaming
  const summary = computeProcessSummary(segmentParts, resultByCallId)

  const toggle = () => setUserOverride(!expanded)

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={toggle}
        className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground transition-all duration-200 hover:bg-muted hover:border-border hover:text-foreground"
      >
        <ChevronRight className="size-3 shrink-0" />
        <span>{summary}</span>
      </button>
    )
  }

  return (
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={toggle}
        className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground transition-all duration-200 hover:bg-muted hover:border-border hover:text-foreground"
      >
        <ChevronDown className="size-3 shrink-0" />
        <span>{summary}</span>
      </button>
      <div className="overflow-hidden animate-in fade-in-0 slide-in-from-top-1 duration-200 space-y-0.5">
        {segmentParts.map(({ part, index }) => {
          const isLastContentPart = index === lastContentPartIndex
          const partStreaming = isStreaming && isLastContentPart
          return (
            <ProcessPartRenderer
              key={`pp-${index}`}
              part={part}
              isStreaming={partStreaming}
              resultByCallId={resultByCallId}
            />
          )
        })}
      </div>
    </div>
  )
}

function computeProcessSummary(
  segmentParts: Array<{ part: MessagePart; index: number }>,
  resultByCallId: Map<string, ResultEntry>,
): string {
  let hasThinking = false
  let thinkingDuration = 0
  let toolCount = 0
  let minStarted: number | undefined
  let maxEnded: number | undefined

  for (const { part } of segmentParts) {
    if (part.type === 'thinking') {
      hasThinking = true
      if (part.startedAt !== undefined && part.endedAt !== undefined) {
        thinkingDuration += part.endedAt - part.startedAt
        if (minStarted === undefined || part.startedAt < minStarted) minStarted = part.startedAt
        if (maxEnded === undefined || part.endedAt > maxEnded) maxEnded = part.endedAt
      }
    } else if (part.type === 'tool_use') {
      toolCount++
      if (part.startedAt !== undefined) {
        if (minStarted === undefined || part.startedAt < minStarted) minStarted = part.startedAt
      }
      const result = resultByCallId.get(part.callId)
      if (result?.endedAt !== undefined) {
        if (maxEnded === undefined || result.endedAt > maxEnded) maxEnded = result.endedAt
      }
    } else if (part.type === 'file_write_preview') {
      toolCount++
    }
  }

  const totalDuration = minStarted !== undefined && maxEnded !== undefined ? maxEnded - minStarted : null
  const totalLabel = totalDuration !== null ? ` · ${formatDuration(totalDuration)}` : ''

  if (hasThinking && toolCount > 0) {
    return `▸ 思考 ${formatDuration(thinkingDuration)} · ${toolCount} 个工具${totalLabel}`
  }
  if (hasThinking && toolCount === 0) {
    return `▸ 已深度思考 ${formatDuration(thinkingDuration)}`
  }
  return `▸ ${toolCount} 个工具${totalLabel}`
}

// ─── ProcessPartRenderer: 过程段内部紧凑渲染 ──────────────
function ProcessPartRenderer({
  part,
  isStreaming,
  resultByCallId,
}: {
  part: MessagePart
  isStreaming: boolean
  resultByCallId: Map<string, ResultEntry>
}) {
  switch (part.type) {
    case 'thinking':
      return <ThinkingPart content={part.content} isStreaming={isStreaming} />
    case 'tool_use':
      return (
        <ToolUsePart
          toolName={part.toolName}
          args={part.args}
          callId={part.callId}
          startedAt={part.startedAt}
          completion={resultByCallId.get(part.callId)}
        />
      )
    case 'file_write_preview':
      return (
        <FileWritePreviewPart
          path={part.path}
          content={part.content}
          callId={part.callId}
          status={part.status}
          language={part.language}
          oldContent={part.oldContent}
          newContent={part.newContent}
          isStreaming={isStreaming}
        />
      )
    default:
      return null
  }
}

function PartRenderer({
  part,
  conversationId,
  isStreaming = false,
  isUser = false,
  messageStatus = 'complete',
}: {
  part: MessagePart
  conversationId: string
  isStreaming?: boolean
  isUser?: boolean
  messageStatus?: MessageStatus
}) {
  switch (part.type) {
    case 'text':
      return <TextPart content={part.content} isStreaming={isStreaming} isUser={isUser} messageStatus={messageStatus} />
    case 'code':
      return <CodePart language={part.language} content={part.content} />
    case 'artifact_ref':
      return <ArtifactRefPart artifactId={part.artifactId} />
    case 'deploy_status':
      return <DeployStatusPart deployment={part.deployment} />
    case 'execution_plan':
      return <ExecutionPlanPart steps={part.steps} planId={part.planId} complexity={part.complexity} />
    case 'deploy_candidates':
      return <DeployCandidatesPart conversationId={conversationId} candidates={part.candidates} />
    case 'image_attachment':
    case 'file_attachment':
      return (
        <AttachmentChip
          context="message"
          attachment={{
            id: part.attachmentId,
            fileName: part.fileName,
            size: part.size,
            mimeType: part.mimeType,
            kind: part.type === 'image_attachment' ? 'image' : 'file',
          }}
        />
      )
    default:
      return null
  }
}

// ─── Execution Plan ──────────────────────────────────────
const STATUS_ICON: Record<PlanStepStatus, ReactNode> = {
  pending: <span className="text-muted-foreground">⬚</span>,
  in_progress: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />,
  done: <Check className="h-3.5 w-3.5 text-success" />,
  failed: <XCircle className="h-3.5 w-3.5 text-destructive" />,
  skipped: <span className="text-muted-foreground">⏭</span>,
}

function ExecutionPlanPart({
  steps,
  planId,
  complexity,
}: {
  steps: PlanStep[]
  planId: string
  complexity: 'simple' | 'moderate' | 'complex'
}) {
  const doneCount = steps.filter((s) => s.status === 'done').length
  const totalCount = steps.length
  const progress = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0

  return (
    <Card className="border-l-4 border-l-primary py-0 gap-0">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Layers className="size-4 text-primary" />
            执行计划
          </div>
          <span className="text-xs text-muted-foreground">
            {doneCount}/{totalCount} · {progress}%
          </span>
        </div>
        {/* Progress bar */}
        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        {/* Step list */}
        <div className="space-y-1">
          {steps.map((step) => (
            <div key={step.id} className="flex items-center gap-2 text-sm">
              <span className="flex-shrink-0">{STATUS_ICON[step.status]}</span>
              <span className={step.status === 'done' ? 'line-through text-muted-foreground' : step.status === 'in_progress' ? 'font-medium' : ''}>
                {step.title}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// ─── Text ──────────────────────────────────────────────
function TextPart({
  content,
  isStreaming = false,
  isUser = false,
  messageStatus = 'complete',
}: {
  content: string
  isStreaming?: boolean
  isUser?: boolean
  messageStatus?: MessageStatus
}) {
  if (!content) return null

  const bubbleClass = cn(
    'rounded-lg px-4 py-3 shadow-[var(--shadow-sm)]',
    isUser
      ? 'bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/25'
      : 'bg-card border border-border/50',
    !isUser && messageStatus === 'error' && 'border-destructive/40 bg-destructive/10',
    !isUser && (messageStatus === 'aborted' || messageStatus === 'interrupted') && 'border-muted-foreground/40 bg-muted/60',
  )

  // Streaming fallback: plain <pre> to avoid O(N×S) markdown re-parsing per delta.
  // Font-sans matches the markdown body text; container styling matches complete render.
  if (isStreaming) {
    return (
      <div className={bubbleClass}>
        <div className={cn('text-foreground', isUser ? 'text-sm leading-6' : 'text-[15px] leading-7')}>
          <pre className="whitespace-pre-wrap break-words font-sans">
            {content}
          </pre>
        </div>
      </div>
    )
  }

  const segments = useMemo(() => splitQuotedSelections(content), [content])
  return (
    <div className={cn(bubbleClass, 'space-y-2')}>
      {segments.map((seg, i) =>
        seg.kind === 'quote' ? (
          <QuotedSelectionCard key={i} {...seg} />
        ) : (
          <Markdown key={i}>{seg.text}</Markdown>
        ),
      )}
    </div>
  )
}

interface QuotedSegment {
  kind: 'quote'
  source?: string
  artifactId?: string
  filePath?: string
  text: string
}
interface PlainSegment {
  kind: 'plain'
  text: string
}
type Segment = QuotedSegment | PlainSegment

/** 把 <quoted_selection source=".." artifactId=".." filePath="..">...</quoted_selection> 块和普通文本切开。 */
function splitQuotedSelections(content: string): Segment[] {
  const re = /<quoted_selection([^>]*)>([\s\S]*?)<\/quoted_selection>/g
  const out: Segment[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    if (m.index > last) {
      const before = content.slice(last, m.index).trim()
      if (before) out.push({ kind: 'plain', text: before })
    }
    const attrs = m[1] ?? ''
    out.push({
      kind: 'quote',
      source: extractAttr(attrs, 'source'),
      artifactId: extractAttr(attrs, 'artifactId'),
      filePath: extractAttr(attrs, 'filePath'),
      text: m[2].trim(),
    })
    last = m.index + m[0].length
  }
  if (last < content.length) {
    const tail = content.slice(last).trim()
    if (tail) out.push({ kind: 'plain', text: tail })
  }
  if (out.length === 0) out.push({ kind: 'plain', text: content })
  return out
}

function extractAttr(attrs: string, name: string): string | undefined {
  const m = new RegExp(`${name}\\s*=\\s*"([^"]*)"`).exec(attrs)
  return m?.[1]
}

function QuotedSelectionCard({ source, artifactId, filePath, text }: QuotedSegment) {
  const [expanded, setExpanded] = useState(false)
  const lines = text.split('\n')
  const collapsed = lines.length > 4
  return (
    <div className="overflow-hidden rounded-md border border-primary/30 bg-primary/5 text-xs">
      <div className="flex items-center gap-1.5 border-b border-primary/20 bg-primary/10 px-2.5 py-1 text-[11px]">
        <Sparkles className="size-3 text-primary" />
        <span className="font-medium text-primary">引用</span>
        {source && (
          <>
            <span className="text-muted-foreground">·</span>
            <span className="truncate text-muted-foreground">{source}</span>
          </>
        )}
        {(artifactId || filePath) && (
          <code className="ml-1 truncate font-mono text-[10px] text-muted-foreground/70">
            {artifactId ?? filePath}
          </code>
        )}
        {collapsed && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto rounded px-1 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {expanded ? '收起' : '展开'}
          </button>
        )}
      </div>
      <pre
        className={cn(
          'whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground/90',
          !expanded && collapsed && 'line-clamp-3',
        )}
      >
        {text}
      </pre>
    </div>
  )
}

// ─── Thinking（borderless italic text, collapse/expand handled by ProcessSegment）──
function ThinkingPart({
  content,
  isStreaming,
}: {
  content: string
  isStreaming: boolean
}) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom during streaming
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [content, isStreaming])

  if (!content) return null

  return (
    <div
      ref={scrollRef}
      className="max-h-40 overflow-y-auto border-l-2 border-primary/20 pl-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground/60"
    >
      {content}
    </div>
  )
}

// ─── Code ──────────────────────────────────────────────
function CodePart({ language, content }: { language: string; content: string }) {
  return <CodeBlock code={content} language={language} />
}

// ─── ToolUse + 内嵌 result ──────────────────────────────
function ToolUsePart({
  toolName,
  args,
  callId,
  startedAt,
  completion,
}: {
  toolName: string
  args: unknown
  callId: string
  startedAt?: number
  completion?: { result: unknown; isError: boolean; endedAt?: number }
}) {
  const [showDetails, setShowDetails] = useState(false)
  const displayName = getToolDisplayName(toolName, args)
  const command = isBashToolName(toolName) ? extractCommand(args) : null
  const remainingArgs = command ? omitCommand(args) : args
  const bashResult =
    isBashToolName(toolName) && completion
      ? extractBashResult(completion.result) ??
        (completion.isError && typeof completion.result === 'string'
          ? { output: completion.result }
          : null)
      : null

  // Direction B: detect fs_write/fs_edit with diff data in tool result
  const isFileDiffTool = (toolName === 'fs_write' || toolName === 'fs_edit') && completion && !completion.isError
  const diffData = isFileDiffTool && typeof completion.result === 'object' && completion.result !== null
    ? (() => {
        const r = completion.result as Record<string, unknown>
        const oldContent = r.oldContent as string | null | undefined
        const newContent = r.newContent as string | null | undefined
        const path = r.path as string | undefined
        if (oldContent !== undefined && newContent !== undefined) {
          const ext = path ? path.split('.').pop() : undefined
          return { oldContent: oldContent ?? '', newContent: newContent ?? '', language: ext }
        }
        return null
      })()
    : null

  const state: 'running' | 'success' | 'error' = !completion
    ? 'running'
    : completion.isError
      ? 'error'
      : 'success'

  const isRunning = state === 'running'
  const liveElapsed = useElapsedTimer(startedAt, isRunning)
  const completedDuration =
    startedAt && completion?.endedAt
      ? completion.endedAt - startedAt
      : null

  const iconColor = {
    running: 'text-warning',
    success: 'text-success',
    error: 'text-destructive',
  }[state]

  const label = {
    running: '调用中',
    success: '已完成',
    error: '失败',
  }[state]

  const durationLabel = isRunning
    ? liveElapsed !== null
      ? ` · ${formatDuration(liveElapsed)}...`
      : ''
    : completedDuration !== null
      ? ` · ${formatDuration(completedDuration)}`
      : ''

  const toggleDetails = () => setShowDetails((v) => !v)
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      toggleDetails()
    }
  }

  const stateBorder = {
    running: 'border-l-2 border-l-warning/50',
    success: 'border-l-2 border-l-success/50',
    error: 'border-l-2 border-l-destructive/50',
  }[state]

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={showDetails}
      title={showDetails ? '隐藏工具调用详情' : '展开工具调用详情'}
      onClick={toggleDetails}
      onKeyDown={handleKeyDown}
      className={cn(
        'w-full cursor-pointer rounded transition hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
        stateBorder,
      )}
    >
      <div className="flex min-w-0 items-center gap-2 px-1.5 py-0.5 text-xs text-muted-foreground">
        {state === 'running' && <Loader2 className={cn('size-3.5 shrink-0 animate-spin', iconColor)} />}
        {state === 'success' && <Check className={cn('size-3.5 shrink-0', iconColor)} />}
        {state === 'error' && <XCircle className={cn('size-3.5 shrink-0', iconColor)} />}
        <span className="min-w-0 max-w-[12rem] truncate font-medium">
          {displayName}
        </span>
        <span>·</span>
        <span className="shrink-0">{label}</span>
        {durationLabel && (
          <span className="shrink-0">{durationLabel}</span>
        )}
        <ChevronDown
          className={cn(
            'ml-auto size-3 shrink-0 transition-transform',
            !showDetails && '-rotate-90',
          )}
        />
      </div>

      {command && <CommandPreview command={command} expanded={showDetails} />}
      {bashResult && (
        <BashOutputPreview
          result={bashResult}
          expanded={showDetails}
          tone={completion?.isError ? 'error' : 'neutral'}
        />
      )}
      {/* Direction B: inline diff preview for fs_write/fs_edit */}
      {isFileDiffTool && diffData && !showDetails && (
        <CompactDiffPreview oldCode={diffData.oldContent} newCode={diffData.newContent} />
      )}
      {isFileDiffTool && diffData && showDetails && (
        <DiffBlock oldCode={diffData.oldContent} newCode={diffData.newContent} language={diffData.language} />
      )}

      {showDetails && (
        <div className="min-w-0 space-y-2 px-1 pb-1 pt-0.5 animate-in fade-in-0 slide-in-from-top-1 duration-200">
          {remainingArgs !== null && (
            <ToolDetailBlock label={command ? '其他参数' : '参数'} value={remainingArgs} />
          )}
          {completion && (
            <ToolDetailBlock
              label={completion.isError ? '错误' : '返回'}
              value={completion.result}
              tone={completion.isError ? 'error' : 'neutral'}
            />
          )}
          <div className="flex min-w-0 flex-wrap gap-x-2 gap-y-1 text-[10px] text-muted-foreground/50">
            <span className="font-mono">{toolName}</span>
            <span className="font-mono">{callId}</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── CompactDiffPreview: ToolUsePart 内的紧凑 diff 预览 ──────────────────────
function CompactDiffPreview({ oldCode, newCode }: { oldCode: string; newCode: string }) {
  // Simple unified diff: show up to 8 changed lines
  const oldLines = oldCode.split('\n')
  const newLines = newCode.split('\n')
  const maxLines = 8

  // Find changed lines by comparing line by line
  const changedLines: Array<{ kind: 'removed' | 'added'; text: string }> = []
  let oldIdx = 0
  let newIdx = 0
  while (oldIdx < oldLines.length || newIdx < newLines.length) {
    if (oldIdx < oldLines.length && newIdx < newLines.length && oldLines[oldIdx] === newLines[newIdx]) {
      oldIdx++
      newIdx++
    } else {
      // Check if line was removed
      if (oldIdx < oldLines.length && (newIdx >= newLines.length || oldLines[oldIdx] !== newLines[newIdx])) {
        changedLines.push({ kind: 'removed', text: oldLines[oldIdx] })
        oldIdx++
      }
      // Check if line was added
      if (newIdx < newLines.length && (oldIdx >= oldLines.length || oldLines[oldIdx] !== newLines[newIdx])) {
        changedLines.push({ kind: 'added', text: newLines[newIdx] })
        newIdx++
      }
    }
  }

  const displayed = changedLines.slice(0, maxLines)
  const hasMore = changedLines.length > maxLines

  return (
    <div className="mt-1 overflow-hidden rounded bg-muted/30 text-[11px]">
      {displayed.map((line, i) => (
        <div
          key={i}
          className={cn(
            'px-2 py-px font-mono',
            line.kind === 'removed' && 'bg-destructive/10 text-destructive',
            line.kind === 'added' && 'bg-success/10 text-success',
          )}
        >
          <span className="mr-1 inline-block w-3 text-center text-muted-foreground/50">
            {line.kind === 'removed' ? '-' : '+'}
          </span>
          <span className="break-all">{line.text}</span>
        </div>
      ))}
      {hasMore && (
        <div className="px-2 py-0.5 text-center text-muted-foreground">
          +{changedLines.length - maxLines} more lines
        </div>
      )}
    </div>
  )
}

// ─── FileWritePreviewPart: 流式文件写入预览 ──────────────────────────────
function FileWritePreviewPart({
  path,
  content,
  callId,
  status,
  language,
  oldContent,
  newContent,
  isStreaming = false,
}: {
  path: string
  content: string
  callId: string
  status: 'streaming' | 'complete' | 'failed'
  language?: string
  oldContent?: string | null
  newContent?: string | null
  isStreaming?: boolean
}) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll during streaming
  const scrollToBottom = useCallback(() => {
    if (scrollRef.current && isStreaming) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [isStreaming])

  useEffect(() => {
    if (isStreaming) {
      scrollToBottom()
    }
  }, [content, isStreaming, scrollToBottom])

  const displayName = path || '正在写入...'
  const derivedLanguage = language || (path ? path.split('.').pop() : undefined)

  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 px-1 py-0.5 text-xs text-muted-foreground">
        <FileIcon className="size-3 shrink-0" />
        <span className="min-w-0 max-w-[16rem] truncate font-medium">{displayName}</span>
        <span>·</span>
        {status === 'streaming' && (
          <>
            <Loader2 className="size-3 animate-spin" />
            <span>生成中</span>
          </>
        )}
        {status === 'complete' && oldContent && (
          <>
            <Check className="size-3 text-success" />
            <span className="text-success">已完成</span>
          </>
        )}
        {status === 'complete' && !oldContent && (
          <>
            <Check className="size-3 text-success" />
            <span className="text-success">已创建</span>
          </>
        )}
        {status === 'failed' && (
          <>
            <XCircle className="size-3 text-destructive" />
            <span className="text-destructive">失败</span>
          </>
        )}
      </div>

      {status === 'streaming' && (
        <div ref={scrollRef} className="max-h-[24rem] overflow-auto rounded bg-muted/30">
          <div className="relative">
            {/* Streaming fallback: plain <pre> to avoid O(N×S) Shiki re-highlight per delta */}
            <pre className="px-2 py-1 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
              {content}
            </pre>
            {isStreaming && (
              <span className="absolute bottom-1 right-2 inline-block size-2 animate-pulse rounded-full bg-success" />
            )}
          </div>
        </div>
      )}

      {status === 'complete' && oldContent != null && newContent != null && (
        <div className="max-h-[32rem] overflow-auto">
          <DiffBlock
            oldCode={oldContent}
            newCode={newContent}
            language={derivedLanguage}
          />
        </div>
      )}

      {status === 'complete' && oldContent == null && newContent != null && (
        <div className="max-h-[32rem] overflow-auto">
          <CodeBlock
            code={newContent}
            language={derivedLanguage || 'text'}
          />
        </div>
      )}

      {status === 'failed' && (
        <div className="px-2 py-1 text-xs text-muted-foreground">
          {content ? (
            <details>
              <summary className="cursor-pointer text-destructive">写入失败 — 查看部分内容</summary>
              <pre className="mt-1 max-h-[12rem] overflow-auto rounded bg-muted/50 p-2 font-mono text-[11px]">
                {content}
              </pre>
            </details>
          ) : (
            <span className="text-destructive">写入失败</span>
          )}
        </div>
      )}
    </div>
  )
}

function CommandPreview({ command, expanded }: { command: string; expanded: boolean }) {
  return (
    <TerminalPreviewBlock
      label="命令"
      content={command}
      copyTitle="复制命令"
      expanded={expanded}
    />
  )
}

interface BashResultPreview {
  output: string
  exitCode?: number | null
  truncated?: boolean
  timedOut?: boolean
}

function BashOutputPreview({
  result,
  expanded,
  tone,
}: {
  result: BashResultPreview
  expanded: boolean
  tone: 'neutral' | 'error'
}) {
  const meta = [
    typeof result.exitCode === 'number' ? `exit ${result.exitCode}` : null,
    result.timedOut ? 'timeout' : null,
    result.truncated ? 'truncated' : null,
  ].filter(Boolean)
  const shouldWarn = tone === 'error' || result.timedOut || (result.exitCode ?? 0) !== 0

  return (
    <TerminalPreviewBlock
      label="输出"
      content={result.output || '(无输出)'}
      copyTitle="复制输出"
      expanded={expanded}
      meta={meta.length > 0 ? meta.join(' · ') : undefined}
      tone={shouldWarn ? 'error' : 'neutral'}
      collapsedMaxClassName="max-h-44"
    />
  )
}

function TerminalPreviewBlock({
  label,
  content,
  copyTitle,
  expanded,
  meta,
  tone = 'neutral',
  collapsedMaxClassName = 'max-h-20',
}: {
  label: string
  content: string
  copyTitle: string
  expanded: boolean
  meta?: string
  tone?: 'neutral' | 'error'
  collapsedMaxClassName?: string
}) {
  const [copied, setCopied] = useState(false)

  const copy = async (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore
    }
  }

  return (
    <div
      className={cn(
        'min-w-0 overflow-hidden rounded-md border shadow-sm',
        'bg-muted/60 text-foreground',
        tone === 'error'
          ? 'border-destructive/50'
          : 'border-border',
      )}
    >
      <div className="flex min-w-0 items-center gap-2 border-b border-border/60 bg-muted/60 px-2.5 py-1.5 text-[10px] text-muted-foreground">
        <Terminal className="size-3 shrink-0" />
        <span className="shrink-0 font-medium">{label}</span>
        {meta && <span className="min-w-0 truncate font-mono">{meta}</span>}
        <button
          type="button"
          onClick={(event) => void copy(event)}
          className="ml-auto inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground transition hover:bg-accent hover:text-foreground"
          title={copyTitle}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        className={cn(
          'min-w-0 max-w-full overflow-auto px-2.5 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]',
          expanded ? 'max-h-80' : collapsedMaxClassName,
        )}
      >
        <code>{content}</code>
      </pre>
    </div>
  )
}

function ToolDetailBlock({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: unknown
  tone?: 'neutral' | 'error'
}) {
  return (
    <div
      className={cn(
        'min-w-0 overflow-hidden rounded bg-muted/40',
        tone === 'error' && 'bg-destructive/10',
      )}
    >
      <div className="px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
        {label}
      </div>
      <pre className="max-h-72 min-w-0 max-w-full overflow-auto px-2 py-1 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
        <code>{formatToolValue(value)}</code>
      </pre>
    </div>
  )
}

function extractCommand(value: unknown): string | null {
  if (!isPlainRecord(value)) return null
  const command = value.command
  return typeof command === 'string' && command.trim() ? command : null
}

function omitCommand(value: unknown): unknown | null {
  if (!isPlainRecord(value)) return value
  const rest = { ...value }
  delete rest.command
  return Object.keys(rest).length > 0 ? rest : null
}

function extractBashResult(value: unknown): BashResultPreview | null {
  if (!isPlainRecord(value) || typeof value.output !== 'string') return null
  return {
    output: value.output,
    exitCode:
      typeof value.exitCode === 'number' || value.exitCode === null ? value.exitCode : undefined,
    truncated: typeof value.truncated === 'boolean' ? value.truncated : undefined,
    timedOut: typeof value.timedOut === 'boolean' ? value.timedOut : undefined,
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function formatToolValue(value: unknown): string {
  if (typeof value === 'string') return value
  const json = JSON.stringify(value, null, 2)
  return json ?? String(value)
}


// ─── ArtifactRef ───────────────────────────────────────
function ArtifactRefPart({ artifactId }: { artifactId: string }) {
  const artifact = useAppStore((s) => s.artifacts[artifactId])
  const upsertArtifact = useAppStore((s) => s.upsertArtifact)
  const openPreview = useAppStore((s) => s.openArtifactPreview)
  const [status, setStatus] = useState<'loading' | 'deleted'>('loading')

  // Lazy load: store 里没有该 artifact 时，按需 fetch（404 即视为已删除）
  useEffect(() => {
    if (artifact) return
    let cancelled = false
    fetchArtifact(artifactId)
      .then((row) => {
        if (!cancelled) upsertArtifact(row)
      })
      .catch(() => {
        if (!cancelled) setStatus('deleted')
      })
    return () => {
      cancelled = true
    }
  }, [artifactId, artifact, upsertArtifact])

  if (status === 'deleted' && !artifact) {
    return (
      <Card className="border-dashed bg-muted/40">
        <CardContent className="flex items-center gap-2 px-3 py-2">
          <XCircle className="size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-muted-foreground line-through">
              产物已删除
            </div>
            <div className="font-mono text-[10px] text-muted-foreground">{artifactId}</div>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!artifact) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>产物加载中…</span>
        </CardContent>
      </Card>
    )
  }

  const isWebApp = artifact.type === 'web_app'

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => openPreview(artifact.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') openPreview(artifact.id)
      }}
      className="cursor-pointer transition hover:border-primary/40 hover:shadow-sm"
    >
      <CardContent className="flex items-start gap-3 px-3 py-2">
        <ArtifactIcon type={artifact.type} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{artifact.title}</div>
          <div className="text-xs text-muted-foreground">
            {artifact.type} · v{artifact.version} · 点击预览
          </div>
        </div>
        {isWebApp && (
          <div className="flex shrink-0 items-center gap-1">
            <IconAction
              title="打开预览 URL"
              onClick={(event) => {
                event.stopPropagation()
                openPreviewUrl(artifact.id)
              }}
            >
              <ExternalLink className="size-3.5" />
            </IconAction>
            <IconAction
              title="复制预览 URL"
              onClick={(event) => {
                event.stopPropagation()
                copyPreviewUrl(artifact.id)
              }}
            >
              <Copy className="size-3.5" />
            </IconAction>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ArtifactIcon({ type }: { type: string }) {
  if (type === 'image') return <ImageIcon className="size-5 shrink-0 text-muted-foreground" />
  if (type === 'document') return <FileText className="size-5 shrink-0 text-muted-foreground" />
  if (type === 'ppt') return <Presentation className="size-5 shrink-0 text-muted-foreground" />
  if (type === 'project') return <FolderGit2 className="size-5 shrink-0 text-muted-foreground" />
  return <Layers className="size-5 shrink-0 text-muted-foreground" />
}

function DeployCandidatesPart({
  conversationId,
  candidates,
}: {
  conversationId: string
  candidates: Extract<MessagePart, { type: 'deploy_candidates' }>['candidates']
}) {
  const agents = useAppStore((s) => s.agents)
  const upsertMessage = useAppStore((s) => s.upsertMessage)
  const [deployingId, setDeployingId] = useState<string | null>(null)
  const [deployedId, setDeployedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const deploy = async (artifactId: string) => {
    if (deployingId) return
    setDeployingId(artifactId)
    setError(null)
    try {
      const result = await deployConversationArtifact(conversationId, artifactId)
      upsertMessage(result.message)
      setDeployedId(artifactId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDeployingId(null)
    }
  }

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardContent className="space-y-2 px-3 py-2">
        <div className="flex items-center gap-2">
          <Rocket className="size-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">选择要部署的产物</div>
            <div className="text-xs text-muted-foreground">
              当前会话有 {candidates.length} 个网页产物
            </div>
          </div>
        </div>

        <div className="divide-y rounded-md border bg-background/70">
          {candidates.map((candidate) => {
            const agent = agents[candidate.createdByAgentId]
            const busy = deployingId === candidate.artifactId
            const deployed = deployedId === candidate.artifactId
            return (
              <div
                key={candidate.artifactId}
                className="flex items-center gap-3 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{candidate.title}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    v{candidate.version} · {agent?.name ?? candidate.createdByAgentId} ·{' '}
                    {formatCompactDate(candidate.createdAt)}
                  </div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground/70">
                    {candidate.artifactId}
                  </div>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant={deployed ? 'secondary' : 'outline'}
                  disabled={Boolean(deployingId) || deployed}
                  onClick={() => void deploy(candidate.artifactId)}
                  className="shrink-0"
                >
                  {busy ? (
                    <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                  ) : (
                    <Rocket className="mr-1.5 size-3.5" />
                  )}
                  {deployed ? '已部署' : '部署'}
                </Button>
              </div>
            )
          })}
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function DeployStatusPart({
  deployment,
}: {
  deployment: Extract<MessagePart, { type: 'deploy_status' }>['deployment']
}) {
  const ready = deployment.status === 'ready'
  const previewUrl = resolvePreviewUrl(deployment.previewPath)
  const isLocalStatic = deployment.deploymentType === 'local_static'
  const isExternalStatic = deployment.deploymentType === 'external_static'
  const fallbackPreviewPath = deployment.localPreviewPath
  const fallbackPreviewUrl = fallbackPreviewPath ? resolvePreviewUrl(fallbackPreviewPath) : null
  const actionPreviewPath = ready ? deployment.previewPath : fallbackPreviewPath
  const sourceLabel =
    deployment.sourceType === 'workspace'
      ? `工作区 ${deployment.workspacePath ?? '目录'}`
      : `v${deployment.version}`

  return (
    <Card
      className={cn(
        ready
          ? 'border-primary/30 bg-primary/5'
          : 'border-destructive/30 bg-destructive/10',
      )}
    >
      <CardContent className="flex items-start gap-3 px-3 py-2">
        {ready ? (
          <Rocket className="mt-0.5 size-4 shrink-0 text-primary" />
        ) : (
          <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">
            {ready
              ? isExternalStatic
                ? '外部静态发布已就绪'
                : isLocalStatic
                  ? '本地静态发布已就绪'
                  : '部署预览已就绪'
              : isExternalStatic
                ? '外部静态发布失败'
                : '部署预览失败'}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {deployment.title} · {sourceLabel}
            {(isLocalStatic || isExternalStatic) && ` · ${deployment.id}`}
          </div>
          {ready ? (
            <div className="mt-1 space-y-0.5">
              <div className="truncate font-mono text-[11px] text-primary">
                {previewUrl}
              </div>
              {fallbackPreviewUrl && fallbackPreviewUrl !== previewUrl && (
                <div className="truncate text-[11px] text-muted-foreground">
                  本地回退：<span className="font-mono">{fallbackPreviewUrl}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="mt-1 space-y-0.5">
              <div className="text-xs text-destructive">
                {deployment.error ?? 'Unknown deployment error'}
              </div>
              {fallbackPreviewUrl && (
                <div className="truncate text-[11px] text-muted-foreground">
                  本地回退：<span className="font-mono">{fallbackPreviewUrl}</span>
                </div>
              )}
            </div>
          )}
        </div>
        {(ready || actionPreviewPath || deployment.sourceDownloadPath || deployment.containerDownloadPath) && (
          <div className="flex shrink-0 items-center gap-1">
            {actionPreviewPath && (
              <>
                <IconAction title={ready ? '打开预览 URL' : '打开本地回退预览'} onClick={() => openPath(actionPreviewPath)}>
                  <ExternalLink className="size-3.5" />
                </IconAction>
                <IconAction title={ready ? '复制预览 URL' : '复制本地回退预览'} onClick={() => copyPath(actionPreviewPath)}>
                  <Copy className="size-3.5" />
                </IconAction>
              </>
            )}
            {deployment.sourceDownloadPath && (
              <IconLinkAction title="下载源码包" href={deployment.sourceDownloadPath}>
                <Download className="size-3.5" />
              </IconLinkAction>
            )}
            {deployment.containerDownloadPath && (
              <IconLinkAction title="下载容器包" href={deployment.containerDownloadPath}>
                <Package className="size-3.5" />
              </IconLinkAction>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function IconLinkAction({
  title,
  href,
  children,
}: {
  title: string
  href: string
  children: ReactNode
}) {
  return (
    <a
      href={href}
      title={title}
      download
      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-background/80 hover:text-foreground"
    >
      {children}
    </a>
  )
}

function IconAction({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: (event: MouseEvent<HTMLButtonElement>) => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-background/80 hover:text-foreground"
    >
      {children}
    </button>
  )
}

function openPreviewUrl(artifactId: string): void {
  openPath(artifactPreviewPath(artifactId))
}

function copyPreviewUrl(artifactId: string): void {
  copyPath(artifactPreviewPath(artifactId))
}

function openPath(path: string): void {
  window.open(path, '_blank', 'noopener,noreferrer')
}

function copyPath(path: string): void {
  navigator.clipboard?.writeText(resolvePreviewUrl(path)).catch(() => {})
}

function resolvePreviewUrl(path: string): string {
  return new URL(path, window.location.origin).toString()
}

function formatCompactDate(ts: number): string {
  return new Date(ts).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
