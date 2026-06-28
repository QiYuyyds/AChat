'use client'

import { Sparkles, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

/**
 * SelectionPopover —— 监听全局 selectionchange，当选区落在标记为
 * `data-selection-target="..."` 的容器内时，弹出浮动框「让 Agent 改这段」。
 *
 * 点击后把选中的文字 + 来源标签塞到 store.pendingQuoteForInput，MessageInput
 * 会显示一个引用 chip，下次发送时 prepend 到消息内容。
 *
 * 这样规避了「右键菜单和浏览器原生菜单冲突」的问题。
 */

interface State {
  text: string
  sourceLabel: string
  kind: 'rewrite' | 'ask'
  artifactId?: string
  filePath?: string
  rect: DOMRect
}

const MAX_TEXT_PREVIEW = 4000

export function SelectionPopover() {
  const setPendingQuote = useAppStore((s) => s.setPendingQuote)
  const [state, setState] = useState<State | null>(null)

  useEffect(() => {
    const handler = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
        setState(null)
        return
      }
      const text = sel.toString()
      if (!text.trim()) {
        setState(null)
        return
      }
      const range = sel.getRangeAt(0)
      const node = range.commonAncestorContainer
      const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as Element)
      const target = el?.closest('[data-selection-target]') as HTMLElement | null
      if (!target) {
        setState(null)
        return
      }
      const sourceLabel = target.dataset.selectionLabel ?? '选中片段'
      // 聊天消息选区是「就这段提问」，artifact/文件是「改写」
      const kind = target.dataset.selectionTarget === 'message' ? 'ask' : 'rewrite'
      const artifactId = target.dataset.selectionArtifactId
      const filePath = target.dataset.selectionFilePath
      const rect = range.getBoundingClientRect()
      if (rect.width === 0 && rect.height === 0) {
        setState(null)
        return
      }
      setState({
        text: text.length > MAX_TEXT_PREVIEW ? text.slice(0, MAX_TEXT_PREVIEW) + '\n[...截断]' : text,
        sourceLabel,
        kind,
        artifactId,
        filePath,
        rect,
      })
    }
    document.addEventListener('selectionchange', handler)
    return () => document.removeEventListener('selectionchange', handler)
  }, [])

  if (!state) return null

  const popoverWidth = 200
  const top = Math.max(8, state.rect.top - 44)
  const left = Math.min(
    window.innerWidth - popoverWidth - 8,
    Math.max(8, state.rect.left + state.rect.width / 2 - popoverWidth / 2),
  )

  const handlePick = () => {
    setPendingQuote({
      text: state.text,
      sourceLabel: state.sourceLabel,
      kind: state.kind,
      artifactId: state.artifactId,
      filePath: state.filePath,
    })
    window.getSelection()?.removeAllRanges()
    setState(null)
  }

  return (
    <div
      style={{ position: 'fixed', top, left, width: popoverWidth, zIndex: 60 }}
      className={cn(
        'flex items-center gap-1 rounded-md border bg-popover px-1.5 py-1 text-xs shadow-lg',
        'animate-in fade-in slide-in-from-bottom-1 duration-150',
      )}
      // 阻止 mousedown 冒泡导致 selection 提前清掉
      onMouseDown={(e) => e.preventDefault()}
    >
      <button
        type="button"
        onClick={handlePick}
        className="flex flex-1 items-center gap-1 rounded px-1.5 py-0.5 text-left hover:bg-accent"
      >
        <Sparkles className="size-3 text-primary" />
        <span className="font-medium">{state.kind === 'ask' ? '问 Agent 这段' : '让 Agent 改这段'}</span>
      </button>
      <button
        type="button"
        onClick={() => {
          window.getSelection()?.removeAllRanges()
          setState(null)
        }}
        className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        title="关闭"
      >
        <X className="size-3" />
      </button>
    </div>
  )
}
