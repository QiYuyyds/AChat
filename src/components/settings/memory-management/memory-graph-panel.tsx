'use client'

import { Network as NetworkIcon } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { type GraphData, type GraphNode, fetchMemoryGraph, readMemoryFile } from '@/lib/api/memory'
import { useGuideSideEffectRefresh } from '@/lib/use-guide-refresh'
import { cn } from '@/lib/utils'

// ─── Bucket visual config ──────────────────────────────────────────────────

const BUCKET_COLORS: Record<string, string> = {
  procedure: '#3b82f6',
  personal: '#8b5cf6',
  wiki: '#10b981',
  daily: '#f59e0b',
}

const BUCKET_LABELS: Record<string, string> = {
  all: '全部',
  procedure: '经验',
  personal: '个人',
  wiki: '知识',
  daily: '日常',
}

const BUCKETS = ['all', 'procedure', 'personal', 'wiki', 'daily'] as const
type BucketFilter = (typeof BUCKETS)[number]

// ─── Types ────────────────────────────────────────────────────────────────

interface SimNode extends GraphNode {
  x: number
  y: number
  vx: number
  vy: number
  fx: number | null
  fy: number | null
}

interface SimLink {
  source: SimNode
  target: SimNode
  predicate: string | null
}

// ─── Component ─────────────────────────────────────────────────────────────

export function MemoryGraphPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterBucket, setFilterBucket] = useState<BucketFilter>('all')
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [detail, setDetail] = useState<{
    path: string
    name: string
    body: string
    description: string
    tags: string[]
    importance: number
    bucket: string
  } | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const transformRef = useRef({ k: 1, x: 0, y: 0 })
  const selectedRef = useRef<string | null>(null)

  const handleNodeClick = useCallback(async (path: string) => {
    setSelectedPath(path)
    selectedRef.current = path
    setDetailLoading(true)
    try {
      const d = await readMemoryFile(path)
      setDetail({
        path: d.path,
        name: d.name,
        body: d.body,
        description: d.description,
        tags: d.tags,
        importance: d.importance,
        bucket: d.bucket,
      })
    } catch (err) {
      console.error('[MemoryGraphPanel] read file failed', err)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const load = useCallback(async (bucket: BucketFilter) => {
    setLoading(true)
    setSelectedPath(null)
    setDetail(null)
    selectedRef.current = null
    try {
      const result = await fetchMemoryGraph({
        bucket: bucket === 'all' ? undefined : bucket,
      })
      setData(result)
    } catch (err) {
      console.error('[MemoryGraphPanel] load failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(filterBucket)
  }, [load, filterBucket])

  useGuideSideEffectRefresh('memory', () => {
    void load(filterBucket)
  })

  useEffect(() => {
    selectedRef.current = selectedPath
  }, [selectedPath])

  // Main simulation + rendering + interaction
  useEffect(() => {
    if (!data || data.nodes.length === 0) return

    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const dpr = window.devicePixelRatio || 1
    const rect = container.getBoundingClientRect()
    const w = rect.width
    const h = rect.height

    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`

    const cx = w / 2
    const cy = h / 2
    const initRadius = Math.min(w, h) * 0.35

    const simNodes: SimNode[] = data.nodes.map((n, i) => {
      const angle = (i / data.nodes.length) * Math.PI * 2
      return {
        ...n,
        x: cx + Math.cos(angle) * initRadius,
        y: cy + Math.sin(angle) * initRadius,
        vx: 0,
        vy: 0,
        fx: null,
        fy: null,
      }
    })

    const nodeMap = new Map(simNodes.map((n) => [n.path, n]))
    const simLinks: SimLink[] = data.edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: nodeMap.get(e.source)!,
        target: nodeMap.get(e.target)!,
        predicate: e.predicate,
      }))

    const neighbors = new Map<string, Set<string>>()
    for (const n of simNodes) neighbors.set(n.path, new Set())
    for (const link of simLinks) {
      neighbors.get(link.source.path)?.add(link.target.path)
      neighbors.get(link.target.path)?.add(link.source.path)
    }

    // ── Rendering setup ───────────────────────────────────────────────────
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const nodeRadius = (node: SimNode) => 5 + Math.min(node.degree, 8) * 1.8

    const drawOnce = () => {
      ctx.save()
      ctx.clearRect(0, 0, w * dpr, h * dpr)
      ctx.scale(dpr, dpr)

      const t = transformRef.current
      ctx.translate(t.x, t.y)
      ctx.scale(t.k, t.k)

      const selected = selectedRef.current
      const neighborSet = selected ? neighbors.get(selected) : null
      const isDimmed = (path: string) => {
        if (!selected) return false
        if (path === selected) return false
        return !neighborSet?.has(path)
      }

      // Draw edges
      for (const link of simLinks) {
        const dim = selected
          ? link.source.path !== selected && link.target.path !== selected
          : false
        ctx.beginPath()
        ctx.moveTo(link.source.x, link.source.y)
        ctx.lineTo(link.target.x, link.target.y)

        if (link.predicate === 'derived_from') {
          ctx.setLineDash([5, 4])
        } else if (link.predicate) {
          ctx.setLineDash([2, 3])
        } else {
          ctx.setLineDash([])
        }
        ctx.strokeStyle = dim ? 'rgba(120,120,140,0.06)' : 'rgba(120,120,140,0.25)'
        ctx.lineWidth = 1.5
        ctx.stroke()
      }
      ctx.setLineDash([])

      // Draw nodes
      for (const node of simNodes) {
        const dim = isDimmed(node.path)
        const isSelected = node.path === selected
        const color = BUCKET_COLORS[node.bucket] ?? '#6b7280'
        const r = nodeRadius(node)

        if (isSelected) {
          ctx.beginPath()
          ctx.arc(node.x, node.y, r + 6, 0, 2 * Math.PI)
          ctx.fillStyle = color + '30'
          ctx.fill()
        }

        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
        ctx.fillStyle = dim ? color + '20' : color
        ctx.strokeStyle = isSelected ? '#fff' : dim ? color + '30' : color + 'cc'
        ctx.lineWidth = isSelected ? 2.5 : 1.2
        ctx.fill()
        ctx.stroke()

        if (!dim && t.k > 0.8) {
          const showLabel = node.degree >= 2 || isSelected || t.k > 1.5
          if (showLabel) {
            ctx.fillStyle = 'rgba(200,200,210,0.9)'
            ctx.font = '11px system-ui, sans-serif'
            ctx.textAlign = 'center'
            const label = node.name.length > 14 ? node.name.slice(0, 14) + '…' : node.name
            ctx.fillText(label, node.x, node.y - r - 5)
          }
        }
      }

      ctx.restore()
    }

    // ── d3-force simulation ────────────────────────────────────────────────
    let sim: { stop: () => void; alpha: (v: number) => void } | null = null

    void (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceX, forceY, forceCollide } = await import('d3-force')

      const nodes = simNodes as unknown as Array<{ x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null; path: string }>
      const links = simLinks.map((l) => ({ source: l.source, target: l.target, predicate: l.predicate }))

      const chargeForce = forceManyBody().strength(-250)
      const linkForce = forceLink(links)
        .id((d: unknown) => (d as SimNode).path)
        .distance(120)
        .strength(0.15)
      const collideForce = forceCollide().radius((d: unknown) => {
        const node = d as SimNode
        return 8 + Math.min(node.degree, 8) * 2
      })
      const xForce = forceX(cx).strength(0.04)
      const yForce = forceY(cy).strength(0.04)

      const s = forceSimulation(nodes)
        .force('charge', chargeForce)
        .force('link', linkForce)
        .force('collide', collideForce)
        .force('x', xForce)
        .force('y', yForce)
        .alphaDecay(0.02)
        .velocityDecay(0.3)

      s.on('tick', drawOnce)

      sim = {
        stop: () => s.stop(),
        alpha: (v: number) => { s.alpha(v) },
      }
    })()

    // Also draw on raf for smooth pan/zoom (even after sim cools)
    let rafId = 0
    const rafDraw = () => {
      drawOnce()
      rafId = requestAnimationFrame(rafDraw)
    }
    rafId = requestAnimationFrame(rafDraw)

    // ── Interaction ──────────────────────────────────────────────────────────

    const screenToWorld = (sx: number, sy: number) => {
      const t = transformRef.current
      return {
        x: (sx - t.x) / t.k,
        y: (sy - t.y) / t.k,
      }
    }

    const hitTest = (wx: number, wy: number): SimNode | null => {
      for (const node of simNodes) {
        const r = nodeRadius(node)
        const dx = wx - node.x
        const dy = wy - node.y
        if (dx * dx + dy * dy <= (r + 3) * (r + 3)) return node
      }
      return null
    }

    let mode: 'idle' | 'panning' | 'dragging' = 'idle'
    let dragNode: SimNode | null = null
    let lastX = 0
    let lastY = 0
    let downX = 0
    let downY = 0
    let moved = false

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const t = transformRef.current
      const delta = -e.deltaY * 0.0015
      const newK = Math.max(0.2, Math.min(8, t.k * (1 + delta)))
      const cr = canvas.getBoundingClientRect()
      const mx = e.clientX - cr.left
      const my = e.clientY - cr.top
      t.x = mx - ((mx - t.x) / t.k) * newK
      t.y = my - ((my - t.y) / t.k) * newK
      t.k = newK
    }

    const onMouseDown = (e: MouseEvent) => {
      const cr = canvas.getBoundingClientRect()
      const sx = e.clientX - cr.left
      const sy = e.clientY - cr.top
      const { x: wx, y: wy } = screenToWorld(sx, sy)
      const hit = hitTest(wx, wy)

      downX = e.clientX
      downY = e.clientY
      moved = false

      if (hit) {
        mode = 'dragging'
        dragNode = hit
        hit.fx = hit.x
        hit.fy = hit.y
        if (sim) sim.alpha(0.5)
        canvas.style.cursor = 'grabbing'
      } else {
        mode = 'panning'
        lastX = e.clientX
        lastY = e.clientY
        canvas.style.cursor = 'grabbing'
      }
    }

    const onMouseMove = (e: MouseEvent) => {
      if (e.clientX !== downX || e.clientY !== downY) moved = true

      if (mode === 'panning') {
        const t = transformRef.current
        t.x += e.clientX - lastX
        t.y += e.clientY - lastY
        lastX = e.clientX
        lastY = e.clientY
      } else if (mode === 'dragging' && dragNode) {
        const cr = canvas.getBoundingClientRect()
        const sx = e.clientX - cr.left
        const sy = e.clientY - cr.top
        const { x: wx, y: wy } = screenToWorld(sx, sy)
        dragNode.fx = wx
        dragNode.fy = wy
      }
    }

    const onMouseUp = (e: MouseEvent) => {
      if (mode === 'dragging' && dragNode) {
        dragNode.fx = null
        dragNode.fy = null
        dragNode = null
      }

      if (!moved) {
        const cr = canvas.getBoundingClientRect()
        const sx = e.clientX - cr.left
        const sy = e.clientY - cr.top
        const { x: wx, y: wy } = screenToWorld(sx, sy)
        const hit = hitTest(wx, wy)

        if (hit) {
          void handleNodeClick(hit.path)
        } else {
          setSelectedPath(null)
          setDetail(null)
          selectedRef.current = null
        }
      }

      mode = 'idle'
      canvas.style.cursor = 'grab'
    }

    const onDoubleClick = (e: MouseEvent) => {
      const cr = canvas.getBoundingClientRect()
      const sx = e.clientX - cr.left
      const sy = e.clientY - cr.top
      const { x: wx, y: wy } = screenToWorld(sx, sy)
      if (!hitTest(wx, wy)) {
        transformRef.current = { k: 1, x: 0, y: 0 }
      }
    }

    canvas.addEventListener('wheel', onWheel, { passive: false })
    canvas.addEventListener('mousedown', onMouseDown)
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    canvas.addEventListener('dblclick', onDoubleClick)
    canvas.style.cursor = 'grab'

    return () => {
      cancelAnimationFrame(rafId)
      sim?.stop()
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      canvas.removeEventListener('dblclick', onDoubleClick)
    }
  }, [data, handleNodeClick])

  const nodeCount = data?.nodes.length ?? 0
  const edgeCount = data?.edges.length ?? 0

  return (
    <div className="flex h-full min-h-0 flex-col gap-2.5">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="记忆类型">
          {BUCKETS.map((b) => {
            const selected = filterBucket === b
            const color = b === 'all' ? null : BUCKET_COLORS[b]
            return (
              <button
                key={b}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setFilterBucket(b)}
                className={cn(
                  'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium transition-colors',
                  selected
                    ? 'border-primary/30 bg-primary/10 text-primary'
                    : 'border-transparent bg-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                )}
              >
                {color && (
                  <span
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                )}
                {BUCKET_LABELS[b]}
              </button>
            )
          })}
        </div>
        <div className="ml-auto text-[11px] text-muted-foreground">
          <span className="font-mono tabular-nums">{nodeCount}</span> 节点 ·{' '}
          <span className="font-mono tabular-nums">{edgeCount}</span> 边
        </div>
      </div>

      {/* Hint line */}
      <div className="px-1 text-[10px] text-muted-foreground/60">
        滚轮缩放 · 拖拽节点 · 双击空白复位 · 点击节点查看详情
      </div>

      {/* Graph + detail */}
      <div className="flex min-h-0 flex-1 gap-2.5">
        <div
          ref={containerRef}
          className="relative min-w-0 flex-1 overflow-hidden rounded-lg border bg-background/60"
        >
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <svg className="size-5 animate-spin text-muted-foreground" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
                <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
            </div>
          )}

          {!loading && nodeCount === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center">
              <div className="cognition-ambient pointer-events-none absolute size-40 rounded-full bg-primary/8 blur-3xl" />
              <div className="relative">
                <div className="flex size-14 items-center justify-center rounded-2xl border border-border/50 bg-gradient-to-br from-muted to-muted/50 shadow-[var(--shadow-sm)]">
                  <NetworkIcon className="size-6 text-muted-foreground/70 cognition-empty-float" />
                </div>
              </div>
              <div className="cognition-fade-up relative space-y-0.5">
                <p className="text-sm font-semibold text-foreground">暂无记忆图谱</p>
                <p className="text-xs text-muted-foreground">
                  Agent 在对话中积累的 wikilink 关联会在这里可视化展示
                </p>
              </div>
            </div>
          )}

          <canvas ref={canvasRef} className="absolute inset-0" />
        </div>

        {selectedPath && (
          <div className="flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-card shadow-[var(--shadow-sm)]">
            {detailLoading && !detail ? (
              <div className="flex items-center justify-center py-12">
                <svg className="size-4 animate-spin text-muted-foreground" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
                  <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                </svg>
              </div>
            ) : detail ? (
              <GraphDetailPanel detail={detail} />
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Detail panel ──────────────────────────────────────────────────────────

function GraphDetailPanel({
  detail,
}: {
  detail: {
    path: string
    name: string
    body: string
    description: string
    tags: string[]
    importance: number
    bucket: string
  }
}) {
  const color = BUCKET_COLORS[detail.bucket] ?? '#6b7280'
  const cleanBody = detail.body.replace(/\n?\*Source:.*$/m, '').replace(/\n?- \*Source:.*$/m, '').trim()

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <div className="flex items-center gap-2 pb-2">
        <span
          className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
          style={{ backgroundColor: `${color}20`, color }}
        >
          <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} />
          {BUCKET_LABELS[detail.bucket] ?? detail.bucket}
        </span>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {detail.name}
        </h3>
      </div>

      {detail.description && (
        <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
          {detail.description}
        </p>
      )}

      <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span>重要性</span>
        <span className="relative inline-block h-1.5 w-12 overflow-hidden rounded-full bg-muted">
          <span
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              width: `${Math.round(detail.importance * 100)}%`,
              backgroundColor: color,
            }}
          />
        </span>
        <span className="font-mono tabular-nums font-medium text-foreground/80">
          {detail.importance.toFixed(2)}
        </span>
      </div>

      {detail.tags.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {detail.tags.map((tag) => (
            <span
              key={tag}
              className="rounded-md border border-primary/12 bg-primary/6 px-1.5 py-0.5 text-[10px] text-foreground/75"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="mb-2 font-mono text-[9px] text-muted-foreground/50">
        {detail.path}
      </div>

      <div className="mt-2 rounded-md border border-border/50 bg-background p-3">
        <div className="prose prose-sm max-w-none dark:prose-invert prose-p:text-foreground/85 prose-li:text-foreground/85">
          {cleanBody.split('\n').map((line, i) => {
            if (line.startsWith('## ')) {
              return <h3 key={i} className="text-sm font-semibold mt-3 mb-1.5 text-foreground">{line.replace('## ', '')}</h3>
            }
            if (line.startsWith('### ')) {
              return <h4 key={i} className="text-[13px] font-semibold mt-2 mb-1 text-foreground/90">{line.replace('### ', '')}</h4>
            }
            if (line.startsWith('---')) {
              return <hr key={i} className="my-2 border-border/50" />
            }
            if (line.trim().startsWith('- ')) {
              return (
                <li key={i} className="ml-4 list-disc text-[12px] leading-relaxed text-foreground/85">
                  {line.trim().slice(2)}
                </li>
              )
            }
            if (line.trim() === '') {
              return <div key={i} className="h-1.5" />
            }
            return <p key={i} className="text-[12px] leading-relaxed text-foreground/85 my-0.5">{line}</p>
          })}
        </div>
      </div>
    </div>
  )
}
