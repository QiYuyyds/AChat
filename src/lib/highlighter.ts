import type { BundledLanguage, Highlighter } from 'shiki'
import { bundledLanguages, createHighlighter } from 'shiki'

// 预加载的常用语言：首屏 highlight 不用等额外 grammar 下载
const PRELOAD_LANGS: BundledLanguage[] = [
  'typescript',
  'tsx',
  'javascript',
  'jsx',
  'python',
  'json',
  'bash',
  'markdown',
]

// 用户输入的语言别名 → bundled lang id
const LANG_ALIAS: Record<string, BundledLanguage> = {
  ts: 'typescript',
  js: 'javascript',
  py: 'python',
  rs: 'rust',
  sh: 'bash',
  zsh: 'bash',
  shell: 'bash',
  yml: 'yaml',
  md: 'markdown',
  'c++': 'cpp',
  cxx: 'cpp',
  cc: 'cpp',
  hpp: 'cpp',
  h: 'c',
  cs: 'csharp',
  'objective-c': 'objc',
  rb: 'ruby',
  kt: 'kotlin',
  gql: 'graphql',
}

let highlighterPromise: Promise<Highlighter> | null = null
const loadedLangs = new Set<string>(PRELOAD_LANGS)

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: ['github-light', 'github-dark'],
      langs: PRELOAD_LANGS,
    })
  }
  return highlighterPromise
}

function resolveLang(raw: string | null | undefined): BundledLanguage | null {
  if (!raw) return null
  const k = raw.toLowerCase().trim()
  if (k in LANG_ALIAS) return LANG_ALIAS[k]
  if (k in bundledLanguages) return k as BundledLanguage
  return null
}

export function normalizeLang(raw: string | null | undefined): string {
  if (!raw) return 'text'
  const resolved = resolveLang(raw)
  return resolved ?? 'text'
}

/** 渲染成双主题 HTML：用 CSS 变量驱动 light/dark 切换。语法表按需加载。 */
export async function highlightToHtml(code: string, lang: string): Promise<string> {
  const resolved = resolveLang(lang)
  if (!resolved) return wrapPlain(code)
  try {
    const hl = await getHighlighter()
    if (!loadedLangs.has(resolved)) {
      await hl.loadLanguage(resolved)
      loadedLangs.add(resolved)
    }
    return hl.codeToHtml(code, {
      lang: resolved,
      themes: { light: 'github-light', dark: 'github-dark' },
    })
  } catch {
    return wrapPlain(code)
  }
}

function wrapPlain(code: string): string {
  const escaped = code
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return `<pre><code>${escaped}</code></pre>`
}
