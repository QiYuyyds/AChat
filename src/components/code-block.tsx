'use client'

import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'

import { highlightToHtml, normalizeLang } from '@/lib/highlighter'
import { cn } from '@/lib/utils'

interface CodeBlockProps {
  code: string
  language: string
  className?: string
}

/**
 * Shiki 双主题代码块。第一次渲染先 fallback 纯 pre，异步加载后替换为 highlight HTML。
 */
export function CodeBlock({ code, language, className }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const displayLang = normalizeLang(language)
  const langLabel = displayLang === 'text' ? language || 'text' : displayLang

  useEffect(() => {
    let cancelled = false
    highlightToHtml(code, language).then((out) => {
      if (!cancelled) setHtml(out)
    })
    return () => {
      cancelled = true
    }
  }, [code, language])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore
    }
  }

  return (
    <div
      className={cn(
        'group relative my-2 overflow-hidden rounded-md border',
        'border-zinc-200 bg-[#f6f8fa] text-zinc-900',
        'dark:border-zinc-800 dark:bg-[#0d1117] dark:text-zinc-100',
        className,
      )}
    >
      <div
        className={cn(
          'flex items-center justify-between border-b px-3 py-1.5 text-xs',
          'border-zinc-200 dark:border-zinc-800',
        )}
      >
        <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
          {langLabel}
        </span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            'flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] opacity-0 transition group-hover:opacity-100',
            'text-zinc-500 hover:bg-zinc-200 hover:text-zinc-900',
            'dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100',
          )}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {html ? (
        <div
          className="shiki-host overflow-x-auto px-3 py-2 text-xs leading-relaxed [&_pre]:!bg-transparent [&_pre]:!m-0 [&_pre]:!p-0 [&_code]:font-mono"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-relaxed">
          <code>{code}</code>
        </pre>
      )}
    </div>
  )
}
