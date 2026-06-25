const MERMAID_DECLARATION_RE =
  /^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|pie|journey|gitGraph|mindmap|timeline|quadrantChart|requirementDiagram|C4Context|C4Container|C4Component|C4Dynamic|C4Deployment|architecture-beta|block-beta|packet-beta|sankey-beta|xychart-beta)\b/i

export interface MermaidNormaliseResult {
  ok: true
  source: string
}

export interface MermaidNormaliseError {
  ok: false
  error: string
}

export type MermaidNormaliseOutcome = MermaidNormaliseResult | MermaidNormaliseError

export function normaliseMermaidSource(rawSource: string): MermaidNormaliseOutcome {
  const source = stripMermaidFence(rawSource).replace(/\r\n?/g, '\n').trim()
  if (!source) return { ok: false, error: 'Mermaid source is empty.' }

  const firstLine = firstSignificantLine(source)
  if (!firstLine || !MERMAID_DECLARATION_RE.test(firstLine)) {
    return {
      ok: false,
      error:
        'Mermaid source must start with a supported diagram declaration such as "flowchart TD", "sequenceDiagram", or "classDiagram".',
    }
  }

  const normalised = isFlowchart(firstLine) ? normaliseFlowchartLabels(source) : source
  const validationError = validateMermaidSourceStatic(normalised)
  if (validationError) return { ok: false, error: validationError }

  return { ok: true, source: normalised }
}

export function formatMermaidError(error: string): string {
  return error
    .replace(/\s+Expecting\s+/g, '\nExpecting ')
    .replace(/\s+got\s+/g, '\ngot ')
    .trim()
}

function stripMermaidFence(source: string): string {
  const trimmed = source.trim()
  const match = trimmed.match(/^```(?:mermaid|mmd)?\s*\n([\s\S]*?)\n```$/i)
  return match ? match[1] : source
}

function firstSignificantLine(source: string): string | null {
  for (const line of source.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('%%')) continue
    return trimmed
  }
  return null
}

function isFlowchart(firstLine: string): boolean {
  return /^(?:flowchart|graph)\b/i.test(firstLine)
}

function normaliseFlowchartLabels(source: string): string {
  return source
    .split('\n')
    .map((line) => {
      const subgraph = line.match(/^(\s*subgraph\s+)([A-Za-z][\w-]*)(\[)([^\]"']+)(\]\s*)$/)
      if (subgraph) {
        return `${subgraph[1]}${subgraph[2]}["${escapeMermaidLabel(subgraph[4])}"]${subgraph[5].slice(1)}`
      }

      return line.replace(
        /(^|[\s;&])([A-Za-z][\w-]*)(\[)([^\]"'\]\n]+)(\])/g,
        (_full, prefix: string, id: string, open: string, label: string, close: string) =>
          `${prefix}${id}${open}"${escapeMermaidLabel(label)}"${close}`,
      )
    })
    .join('\n')
}

function escapeMermaidLabel(label: string): string {
  return label.trim().replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function validateMermaidSourceStatic(source: string): string | null {
  const lines = source.split('\n')
  for (const [index, line] of lines.entries()) {
    const lineNumber = index + 1
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('%%')) continue

    if (trimmed.startsWith('```')) {
      return `Line ${lineNumber}: remove Markdown code fences before saving a Mermaid diagram.`
    }

    if (/^style\s+/i.test(trimmed) && !isValidStyleLine(trimmed)) {
      return `Line ${lineNumber}: invalid style syntax. Use "style ID fill:#hex,color:#hex" without trailing prose.\n${trimmed}`
    }

    const balanceError = validateBracketBalance(trimmed)
    if (balanceError) return `Line ${lineNumber}: ${balanceError}\n${trimmed}`
  }

  return null
}

function isValidStyleLine(line: string): boolean {
  return /^style\s+[A-Za-z][\w-]*\s+[A-Za-z-]+:[^\s,]+(?:,[A-Za-z-]+:[^\s,]+)*$/i.test(line)
}

function validateBracketBalance(line: string): string | null {
  const stack: string[] = []
  let quote: '"' | "'" | null = null
  let escaped = false

  for (const ch of line) {
    if (quote) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === quote) quote = null
      continue
    }

    if (ch === '"' || ch === "'") {
      quote = ch
      continue
    }
    if (ch === '[' || ch === '(' || ch === '{') {
      stack.push(ch)
      continue
    }
    if (ch === ']' || ch === ')' || ch === '}') {
      const open = stack.pop()
      if (!open || !matchesBracket(open, ch)) return `unmatched "${ch}"`
    }
  }

  if (quote) return `unclosed ${quote} quote`
  if (stack.length > 0) return `unclosed "${stack[stack.length - 1]}"`
  return null
}

function matchesBracket(open: string, close: string): boolean {
  return (
    (open === '[' && close === ']') ||
    (open === '(' && close === ')') ||
    (open === '{' && close === '}')
  )
}
