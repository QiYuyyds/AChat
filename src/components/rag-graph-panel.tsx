'use client'

import { Network as NetworkIcon, Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  type GraphData,
  type GraphStats,
  fetchGraphStats,
  fetchGraphSubgraph,
} from '@/lib/api/graph'
import { useForceGraph } from '@/lib/hooks/use-force-graph'
import { cn } from '@/lib/utils'

// ─── Color config ───────────────────────────────────────────────────────────

const ENTITY_COLORS: Record<string, string> = {
  Person: '#3b82f6',
  Organization: '#8b5cf6',
  Concept: '#10b981',
  Event: '#f59e0b',
  Location: '#ef4444',
  Unknown: '#6b7280',
}

const CHUNK_COLOR = '#6366f1'
const EDGE_COLOR_MENTIONS = 'rgba(99, 102, 241, 0.25)'
const EDGE_COLOR_RELATION = 'rgba(120, 120, 140, 0.25)'

function getNodeColor(node: { type: string; properties: Record<string, unknown> }): string {
  if (node.type === 'Chunk') return CHUNK_COLOR
  const label = (node.properties.label as string) || node.type
  return ENTITY_COLORS[label] ?? ENTITY_COLORS.Unknown
}

// ─── Component ─────────────────────────────────────────────────────────────

export function RagGraphPanel() {
  const [stats, setStats] = useState<GraphStats | null>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [maxDepth, setMaxDepth] = useState(2)
  const [maxNodes, setMaxNodes] = useState(50)
  const [excludeChunk, setExcludeChunk] = useState(false)
  const [searchInput, setSearchInput] = useState('')

  // Fetch stats on mount
  useEffect(() => {
    void fetchGraphStats()
      .then(setStats)
      .catch((err) => console.error('[RagGraphPanel] stats failed', err))
  }, [])

  const loadSubgraph = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchGraphSubgraph({
        keyword: keyword || '*',
        max_depth: maxDepth,
        max_nodes: maxNodes,
        exclude_chunk: excludeChunk,
      })
      setGraphData(data)
    } catch (err) {
      console.error('[RagGraphPanel] subgraph failed', err)
      setGraphData(null)
    } finally {
      setLoading(false)
    }
  }, [keyword, maxDepth, maxNodes, excludeChunk])

  // Fetch subgraph on mount and when params change
  useEffect(() => {
    void loadSubgraph()
  }, [loadSubgraph])

  // Adapt GraphData to ForceGraphData format
  const forceData = useMemo(() => {
    if (!graphData) return null
    return {
      nodes: graphData.nodes.map((n) => ({
        ...n,
        degree: graphData.edges.filter(
          (e) => e.source_id === n.id || e.target_id === n.id,
        ).length,
      })),
      edges: graphData.edges.map((e) => ({
        id: e.id,
        source: e.source_id,
        target: e.target_id,
        type: e.type,
        properties: e.properties,
      })),
    }
  }, [graphData])

  const { containerRef, canvasRef, selectedNode } = useForceGraph(forceData, {
    nodeRadius: (node) => 5 + Math.min(node.degree ?? 0, 8) * 1.8,
    linkDistance: 100,
    charge: -200,
    nodeColor: (node) => getNodeColor(node),
    edgeStyle: (link, selected) => {
      const dim = selected !== null
      const isMentions = link.type === 'MENTIONS'
      return {
        color: dim
          ? 'rgba(120,120,140,0.06)'
          : isMentions
            ? EDGE_COLOR_MENTIONS
            : EDGE_COLOR_RELATION,
        dash: isMentions ? [2, 3] : null,
        width: 1.5,
      }
    },
    nodeLabel: (node) => node.name.length > 14 ? node.name.slice(0, 14) + '…' : node.name,
  })

  const nodeCount = graphData?.nodes.length ?? 0
  const edgeCount = graphData?.edges.length ?? 0

  const handleSearch = () => {
    setKeyword(searchInput.trim())
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2.5">
      {/* Stats bar */}
      {stats && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card/50 px-3 py-2 text-[11px]">
          <span className="font-medium text-muted-foreground">图谱统计</span>
          <span className="text-foreground">
            <span className="font-mono tabular-nums">{stats.total_nodes}</span> 节点
          </span>
          <span className="text-foreground">
            <span className="font-mono tabular-nums">{stats.total_edges}</span> 边
          </span>
          {stats.entity_types.length > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="text-muted-foreground">实体类型：</span>
              {stats.entity_types.slice(0, 5).map((et) => (
                <span
                  key={et.type}
                  className="inline-flex items-center gap-1 rounded-full bg-muted/60 px-1.5 py-0.5"
                >
                  <span
                    className="size-1.5 rounded-full"
                    style={{ backgroundColor: ENTITY_COLORS[et.type] ?? ENTITY_COLORS.Unknown }}
                  />
                  {et.type}
                  <span className="font-mono text-muted-foreground">{et.count}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/50 p-2.5 shadow-[var(--shadow-sm)]">
        {/* Search input */}
        <div className="relative flex-1 min-w-[120px]">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSearch()
            }}
            placeholder="搜索实体..."
            className="h-7 w-full rounded-md border border-border/50 bg-background/60 pl-7 pr-2 text-[11px] placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/30"
          />
        </div>

        {/* Max depth selector */}
        <select
          value={maxDepth}
          onChange={(e) => setMaxDepth(Number(e.target.value))}
          className="h-7 rounded-md border border-border/50 bg-background/60 px-2 text-[11px] text-foreground/80 focus:outline-none focus:ring-1 focus:ring-primary/30"
        >
          <option value={1}>1 跳</option>
          <option value={2}>2 跳</option>
          <option value={3}>3 跳</option>
          <option value={4}>4 跳</option>
          <option value={5}>5 跳</option>
        </select>

        {/* Max nodes selector */}
        <select
          value={maxNodes}
          onChange={(e) => setMaxNodes(Number(e.target.value))}
          className="h-7 rounded-md border border-border/50 bg-background/60 px-2 text-[11px] text-foreground/80 focus:outline-none focus:ring-1 focus:ring-primary/30"
        >
          <option value={50}>50 节点</option>
          <option value={100}>100 节点</option>
          <option value={200}>200 节点</option>
        </select>

        {/* Exclude chunk toggle */}
        <button
          type="button"
          onClick={() => setExcludeChunk(!excludeChunk)}
          className={cn(
            'inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[11px] font-medium transition-colors',
            excludeChunk
              ? 'border-primary/30 bg-primary/10 text-primary'
              : 'border-border/50 bg-background/60 text-muted-foreground hover:text-foreground',
          )}
        >
          <span className="size-1.5 rounded-full" style={{ backgroundColor: CHUNK_COLOR }} />
          {excludeChunk ? '隐藏 Chunk' : '包含 Chunk'}
        </button>

        <div className="ml-auto text-[11px] text-muted-foreground">
          <span className="font-mono tabular-nums">{nodeCount}</span> 节点 ·{' '}
          <span className="font-mono tabular-nums">{edgeCount}</span> 边
        </div>
      </div>

      {/* Hint */}
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
                <p className="text-sm font-semibold text-foreground">暂无图谱数据</p>
                <p className="text-xs text-muted-foreground">
                  上传文档并入库 RAG 后，知识图谱将在此可视化展示
                </p>
              </div>
            </div>
          )}

          <canvas ref={canvasRef} className="absolute inset-0" />
        </div>

        {selectedNode && (
          <div className="flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-card shadow-[var(--shadow-sm)]">
            <NodeDetailPanel node={selectedNode} />
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Node detail panel ─────────────────────────────────────────────────────

function NodeDetailPanel({
  node,
}: {
  node: {
    id: string
    name: string
    type: string
    labels: string[]
    properties: Record<string, unknown>
  }
}) {
  const color = getNodeColor(node)
  const isChunk = node.type === 'Chunk'
  const typeLabel = isChunk ? 'Chunk' : (node.properties.label as string) || node.type

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <div className="flex items-center gap-2 pb-2">
        <span
          className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
          style={{ backgroundColor: `${color}20`, color }}
        >
          <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} />
          {typeLabel}
        </span>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {node.name}
        </h3>
      </div>

      {node.labels.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {node.labels.map((label) => (
            <span
              key={label}
              className="rounded-md border border-primary/12 bg-primary/6 px-1.5 py-0.5 text-[10px] text-foreground/75"
            >
              {label}
            </span>
          ))}
        </div>
      )}

      <div className="mb-2 font-mono text-[9px] text-muted-foreground/50">
        ID: {node.id}
      </div>

      {Object.keys(node.properties).length > 0 && (
        <div className="mt-2 rounded-md border border-border/50 bg-background p-3">
          <p className="mb-1.5 text-[10px] font-medium text-muted-foreground">属性</p>
          <div className="space-y-1">
            {Object.entries(node.properties).map(([key, value]) => (
              <div key={key} className="flex items-start gap-2 text-[11px]">
                <span className="shrink-0 font-mono text-muted-foreground">{key}:</span>
                <span className="min-w-0 break-all text-foreground/85">
                  {typeof value === 'string' ? value : JSON.stringify(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
