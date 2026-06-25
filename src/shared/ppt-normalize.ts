import type {
  ArtifactContent,
  PptBlock,
  PptColumn,
  PptColumnBlock,
  PptLayout,
  PptSlide,
  PptTimelineItem,
  PptTone,
} from './types'

export interface NormalizedPptDeck {
  title?: string
  theme?: Extract<ArtifactContent, { type: 'ppt' }>['theme']
  slides: NormalizedPptSlide[]
}

export interface NormalizedPptSlide {
  title?: string
  subtitle?: string
  layout: PptLayout
  notes?: string
  blocks: PptBlock[]
}

export function normalizePptDeck(content: Extract<ArtifactContent, { type: 'ppt' }>): NormalizedPptDeck {
  return {
    title: content.title,
    theme: content.theme,
    slides: content.slides.map((slide) => normalizePptSlide(slide, content.title)),
  }
}

export function toEditablePptContent(content: Extract<ArtifactContent, { type: 'ppt' }>): Extract<ArtifactContent, { type: 'ppt' }> {
  const deck = normalizePptDeck(content)
  return {
    type: 'ppt',
    ...(deck.title ? { title: deck.title } : {}),
    ...(deck.theme ? { theme: deck.theme } : {}),
    slides: deck.slides.map((slide) => ({
      ...(slide.title ? { title: slide.title } : {}),
      ...(slide.subtitle ? { subtitle: slide.subtitle } : {}),
      layout: slide.layout,
      ...(slide.blocks.length > 0 ? { blocks: slide.blocks } : {}),
      ...(slide.notes ? { notes: slide.notes } : {}),
    })),
  }
}

export function normalizePptSlide(slide: PptSlide, deckTitle?: string): NormalizedPptSlide {
  const layout = normalizeLayout(slide.layout)
  const centered = layout === 'title' || layout === 'section'
  const title = cleanText(slide.title) ?? (centered ? cleanText(deckTitle) : undefined)
  const subtitle = cleanText(slide.subtitle)
  const blocks = normalizeBlocks(slide.blocks)
  const legacyBullets = normalizeStringList(slide.bullets)

  if (legacyBullets.length > 0) {
    blocks.push({ type: 'bullets', items: legacyBullets })
  }

  return {
    ...(title ? { title } : {}),
    ...(subtitle ? { subtitle } : {}),
    layout,
    ...(cleanText(slide.notes) ? { notes: cleanText(slide.notes) } : {}),
    blocks,
  }
}

export function normalizeLayout(layout: unknown): PptLayout {
  return isPptLayout(layout) ? layout : 'title-bullets'
}

export function normalizeBlocks(rawBlocks: unknown): PptBlock[] {
  if (!Array.isArray(rawBlocks)) return []
  const out: PptBlock[] = []
  for (const raw of rawBlocks) {
    const block = normalizeBlock(raw)
    if (block) out.push(block)
  }
  return out
}

function normalizeBlock(raw: unknown): PptBlock | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const obj = raw as Record<string, unknown>
  const type = cleanText(obj.type)

  if (type === 'heading') {
    const text = readBlockText(obj)
    if (!text) return null
    const level = obj.level === 1 || obj.level === 2 ? obj.level : undefined
    return { type: 'heading', text, ...(level ? { level } : {}) }
  }

  if (type === 'paragraph') {
    const text = readBlockText(obj)
    return text ? { type: 'paragraph', text } : null
  }

  if (type === 'bullets') {
    const items = normalizeStringList(obj.items ?? obj.bullets ?? obj.points)
    return items.length > 0 ? { type: 'bullets', items, ordered: obj.ordered === true } : null
  }

  if (type === 'metric') {
    const label = cleanText(obj.label)
    const value = cleanText(obj.value)
    if (!label || !value) return null
    const change = cleanText(obj.change)
    const tone = normalizeTone(obj.tone)
    return { type: 'metric', label, value, ...(change ? { change } : {}), ...(tone ? { tone } : {}) }
  }

  if (type === 'quote') {
    const text = readBlockText(obj)
    if (!text) return null
    const attribution = cleanText(obj.attribution ?? obj.author ?? obj.source)
    return { type: 'quote', text, ...(attribution ? { attribution } : {}) }
  }

  if (type === 'timeline') {
    const items = normalizeTimelineItems(obj.items)
    return items.length > 0 ? { type: 'timeline', items } : null
  }

  if (type === 'columns') {
    const columns = normalizeColumns(obj.columns)
    return columns.length > 0 ? { type: 'columns', columns } : null
  }

  if (type === 'callout') {
    const text = readBlockText(obj)
    if (!text) return null
    const title = cleanText(obj.title)
    const tone = normalizeTone(obj.tone)
    return { type: 'callout', ...(title ? { title } : {}), text, ...(tone ? { tone } : {}) }
  }

  if (type === 'divider') return { type: 'divider' }

  if (type === 'spacer') {
    const size = obj.size === 'sm' || obj.size === 'md' || obj.size === 'lg' ? obj.size : undefined
    return { type: 'spacer', ...(size ? { size } : {}) }
  }

  return null
}

function normalizeColumns(rawColumns: unknown): PptColumn[] {
  if (!Array.isArray(rawColumns)) return []
  const out: PptColumn[] = []
  for (const raw of rawColumns.slice(0, 3)) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const obj = raw as Record<string, unknown>
    const title = cleanText(obj.title)
    const blocks = normalizeColumnBlocks(obj.blocks)
    const bullets = normalizeStringList(obj.bullets ?? obj.items ?? obj.points)
    if (bullets.length > 0) blocks.push({ type: 'bullets', items: bullets })
    if (!title && blocks.length === 0) continue
    out.push({ ...(title ? { title } : {}), blocks })
  }
  return out
}

function normalizeColumnBlocks(rawBlocks: unknown): PptColumnBlock[] {
  if (!Array.isArray(rawBlocks)) return []
  const out: PptColumnBlock[] = []
  for (const raw of rawBlocks) {
    const block = normalizeBlock(raw)
    if (
      block?.type === 'paragraph' ||
      block?.type === 'bullets' ||
      block?.type === 'metric' ||
      block?.type === 'callout'
    ) {
      out.push(block)
    }
  }
  return out
}

function normalizeTimelineItems(rawItems: unknown): PptTimelineItem[] {
  if (!Array.isArray(rawItems)) return []
  const out: PptTimelineItem[] = []
  for (const raw of rawItems) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const obj = raw as Record<string, unknown>
    const label = cleanText(obj.label ?? obj.date ?? obj.phase)
    if (!label) continue
    const title = cleanText(obj.title)
    const text = cleanText(obj.text ?? obj.description)
    out.push({ label, ...(title ? { title } : {}), ...(text ? { text } : {}) })
  }
  return out
}

function normalizeStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => (typeof item === 'string' ? splitLines(item) : []))
  }
  if (typeof value === 'string') return splitLines(value)
  return []
}

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function readBlockText(obj: Record<string, unknown>): string | undefined {
  return cleanText(obj.text ?? obj.content ?? obj.body)
}

function cleanText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function normalizeTone(value: unknown): PptTone | undefined {
  return value === 'neutral' ||
    value === 'positive' ||
    value === 'negative' ||
    value === 'info' ||
    value === 'warning'
    ? value
    : undefined
}

function isPptLayout(value: unknown): value is PptLayout {
  return (
    value === 'title' ||
    value === 'title-bullets' ||
    value === 'section' ||
    value === 'blank' ||
    value === 'content' ||
    value === 'two-column' ||
    value === 'metrics' ||
    value === 'timeline' ||
    value === 'quote'
  )
}
