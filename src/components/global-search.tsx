'use client'

import { CornerDownLeft, Loader2, Search, SearchX, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useSearchStore } from '@/stores/search-store'

import { SearchResultItem } from './search-result-item'

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border bg-muted px-1 font-sans text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  )
}

export function GlobalSearch() {
  const isOpen = useSearchStore((s) => s.isOpen)
  const openSearch = useSearchStore((s) => s.openSearch)
  const closeSearch = useSearchStore((s) => s.closeSearch)
  const query = useSearchStore((s) => s.query)
  const setQuery = useSearchStore((s) => s.setQuery)
  const hits = useSearchStore((s) => s.hits)
  const loading = useSearchStore((s) => s.loading)
  const error = useSearchStore((s) => s.error)
  const jumpToHit = useSearchStore((s) => s.jumpToHit)

  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // ⌘K / Ctrl+K 全局快捷键。挂在常驻弹窗上，侧栏折叠时也生效。
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openSearch()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openSearch])

  useEffect(() => {
    if (isOpen) {
      setActive(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [isOpen])

  useEffect(() => {
    setActive(0)
  }, [hits])

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, Math.max(hits.length - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && hits[active]) {
      e.preventDefault()
      jumpToHit(hits[active])
    }
  }

  const tooShort = query.trim().length < 2
  const showEmpty = !error && !tooShort && hits.length === 0 && !loading

  return (
    <Dialog open={isOpen} onOpenChange={(o) => { if (!o) closeSearch() }}>
      <DialogContent
        showCloseButton={false}
        className="top-[12%] max-w-2xl translate-y-0 gap-0 overflow-hidden rounded-xl p-0 shadow-2xl"
      >
        <DialogTitle className="sr-only">搜索消息</DialogTitle>

        {/* 搜索输入 */}
        <div className="flex h-14 items-center gap-3 border-b px-4">
          {loading ? (
            <Loader2 className="size-5 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <Search className="size-5 shrink-0 text-muted-foreground" />
          )}
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="搜索所有会话的消息…"
            maxLength={200}
            className="h-full flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="grid size-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="清除"
            >
              <X className="size-4" />
            </button>
          )}
        </div>

        {/* 结果区 */}
        <div className="max-h-[60vh] min-h-[7rem] overflow-y-auto p-2">
          {error && (
            <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
              <SearchX className="size-7 text-muted-foreground/70" />
              <p className="text-sm font-medium text-foreground">搜索失败</p>
              <p className="text-xs text-muted-foreground">{error}</p>
            </div>
          )}

          {!error && tooShort && (
            <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
              <Search className="size-7 text-muted-foreground/70" />
              <p className="text-sm font-medium text-foreground">输入至少 2 个字符开始搜索</p>
              <p className="text-xs text-muted-foreground">支持中英文 · 跨所有会话查找消息内容</p>
            </div>
          )}

          {showEmpty && (
            <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
              <SearchX className="size-7 text-muted-foreground/70" />
              <p className="text-sm font-medium text-foreground">没有找到匹配的消息</p>
              <p className="text-xs text-muted-foreground">换个关键词试试</p>
            </div>
          )}

          {hits.length > 0 && (
            <ul role="listbox" className="space-y-0.5">
              {hits.map((hit, i) => (
                <SearchResultItem
                  key={hit.messageId}
                  hit={hit}
                  active={i === active}
                  onClick={() => jumpToHit(hit)}
                />
              ))}
            </ul>
          )}
        </div>

        {/* 底部：键盘提示 + 结果数 */}
        <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd>
              导航
            </span>
            <span className="flex items-center gap-1">
              <Kbd>
                <CornerDownLeft className="size-3" />
              </Kbd>
              打开
            </span>
            <span className="flex items-center gap-1">
              <Kbd>esc</Kbd>
              关闭
            </span>
          </div>
          <span className="tabular-nums">
            {loading ? '搜索中…' : hits.length > 0 ? `${hits.length} 条结果` : ''}
          </span>
        </div>
      </DialogContent>
    </Dialog>
  )
}
