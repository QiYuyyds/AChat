'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

// ─── Shared Graph Types ───────────────────────────────────────────────────

export interface ForceGraphNode {
  id: string
  name: string
  type: string
  labels: string[]
  properties: Record<string, unknown>
  degree?: number
}

export interface ForceGraphEdge {
  id: string
  source: string
  target: string
  type: string
  properties: Record<string, unknown>
}

export interface ForceGraphData {
  nodes: ForceGraphNode[]
  edges: ForceGraphEdge[]
}

// ─── Internal Simulation Types ────────────────────────────────────────────

interface SimNode extends ForceGraphNode {
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
  type: string
}

// ─── Hook Options ──────────────────────────────────────────────────────────

interface UseForceGraphOptions {
  nodeRadius?: (node: SimNode) => number
  linkDistance?: number
  charge?: number
  nodeColor?: (node: SimNode) => string
  edgeStyle?: (link: SimLink, selected: string | null) => { color: string; dash: number[] | null; width: number }
  nodeLabel?: (node: SimNode) => string
  onNodeClick?: (nodeId: string) => void
}

export interface UseForceGraphResult {
  selectedNode: ForceGraphNode | null
  clearSelection: () => void
  containerRef: React.RefObject<HTMLDivElement | null>
  canvasRef: React.RefObject<HTMLCanvasElement | null>
}

export function useForceGraph(
  data: ForceGraphData | null,
  options: UseForceGraphOptions = {},
): UseForceGraphResult {
  const {
    nodeRadius = (node) => 5 + Math.min(node.degree ?? 0, 8) * 1.8,
    linkDistance = 120,
    charge = -250,
    nodeColor = () => '#6b7280',
    edgeStyle,
    nodeLabel = (node) => node.name.length > 14 ? node.name.slice(0, 14) + '…' : node.name,
    onNodeClick,
  } = options

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [selectedNode, setSelectedNode] = useState<ForceGraphNode | null>(null)
  const selectedRef = useRef<string | null>(null)
  const transformRef = useRef({ k: 1, x: 0, y: 0 })

  const clearSelection = useCallback(() => {
    setSelectedNode(null)
    selectedRef.current = null
  }, [])

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

    const nodeMap = new Map(simNodes.map((n) => [n.id, n]))
    const simLinks: SimLink[] = data.edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: nodeMap.get(e.source)!,
        target: nodeMap.get(e.target)!,
        type: e.type,
      }))

    const neighbors = new Map<string, Set<string>>()
    for (const n of simNodes) neighbors.set(n.id, new Set())
    for (const link of simLinks) {
      neighbors.get(link.source.id)?.add(link.target.id)
      neighbors.get(link.target.id)?.add(link.source.id)
    }

    // ── Rendering ─────────────────────────────────────────────────────────
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const drawOnce = () => {
      ctx.save()
      ctx.clearRect(0, 0, w * dpr, h * dpr)
      ctx.scale(dpr, dpr)

      const t = transformRef.current
      ctx.translate(t.x, t.y)
      ctx.scale(t.k, t.k)

      const selected = selectedRef.current
      const neighborSet = selected ? neighbors.get(selected) : null
      const isDimmed = (id: string) => {
        if (!selected) return false
        if (id === selected) return false
        return !neighborSet?.has(id)
      }

      // Draw edges
      for (const link of simLinks) {
        const dim = selected
          ? link.source.id !== selected && link.target.id !== selected
          : false
        ctx.beginPath()
        ctx.moveTo(link.source.x, link.source.y)
        ctx.lineTo(link.target.x, link.target.y)

        if (edgeStyle) {
          const style = edgeStyle(link, selected)
          if (style.dash) {
            ctx.setLineDash(style.dash)
          } else {
            ctx.setLineDash([])
          }
          ctx.strokeStyle = style.color
          ctx.lineWidth = style.width
        } else {
          ctx.setLineDash([])
          ctx.strokeStyle = dim ? 'rgba(120,120,140,0.06)' : 'rgba(120,120,140,0.25)'
          ctx.lineWidth = 1.5
        }
        ctx.stroke()
      }
      ctx.setLineDash([])

      // Draw nodes
      for (const node of simNodes) {
        const dim = isDimmed(node.id)
        const isSelected = node.id === selected
        const color = nodeColor(node)
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
          const showLabel = (node.degree ?? 0) >= 2 || isSelected || t.k > 1.5
          if (showLabel) {
            ctx.fillStyle = 'rgba(200,200,210,0.9)'
            ctx.font = '11px system-ui, sans-serif'
            ctx.textAlign = 'center'
            ctx.fillText(nodeLabel(node), node.x, node.y - r - 5)
          }
        }
      }

      ctx.restore()
    }

    // ── d3-force simulation ────────────────────────────────────────────────
    let sim: { stop: () => void; alpha: (v: number) => void } | null = null

    void (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceX, forceY, forceCollide } = await import('d3-force')

      const nodes = simNodes as unknown as Array<{ x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null; id: string }>
      const links = simLinks.map((l) => ({ source: l.source, target: l.target }))

      const chargeForce = forceManyBody().strength(charge)
      const linkForce = forceLink(links)
        .id((d: unknown) => (d as SimNode).id)
        .distance(linkDistance)
        .strength(0.15)
      const collideForce = forceCollide().radius((d: unknown) => {
        const node = d as SimNode
        return 8 + Math.min(node.degree ?? 0, 8) * 2
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
          setSelectedNode(hit)
          selectedRef.current = hit.id
          onNodeClick?.(hit.id)
        } else {
          setSelectedNode(null)
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
  }, [data, onNodeClick, charge, linkDistance, nodeRadius, nodeColor, edgeStyle, nodeLabel])

  return {
    selectedNode,
    clearSelection,
    containerRef,
    canvasRef,
  }
}
