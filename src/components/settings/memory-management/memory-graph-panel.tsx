'use client'

import { Network as NetworkIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { type GraphData, fetchMemoryGraph, readMemoryFile } from '@/lib/api/memory'
import { useForceGraph } from '@/lib/hooks/use-force-graph'
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

// ─── Component ─────────────────────────────────────────────────────────────

export function MemoryGraphPanel() {
  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterBucket, setFilterBucket] = useState<BucketFilter>('all')
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

  const handleNodeClick = useCallback(async (nodeId: string) => {
    setDetailLoading(true)
    try {
      const d = await readMemoryFile(nodeId)
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
    setDetail(null)
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

  // Clear detail when data changes
  useEffect(() => {
    setDetail(null)
  }, [data])

  // Adapt memory GraphData to ForceGraphData format
  const forceData = useMemo(() => {
    if (!data) return null
    return {
      nodes: data.nodes.map((n) => ({
        id: n.path,
        name: n.name,
        type: 'memory',
        labels: [n.bucket],
        properties: { bucket: n.bucket, importance: n.importance, tags: n.tags, description: n.description },
        degree: n.degree,
      })),
      edges: data.edges.map((e) => ({
        id: `${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: e.predicate ?? 'related',
        properties: {},
      })),
    }
  }, [data])

  const { containerRef, canvasRef, selectedNode } = useForceGraph(forceData, {
    nodeRadius: (node) => 5 + Math.min(node.degree ?? 0, 8) * 1.8,
    linkDistance: 120,
    charge: -250,
    nodeColor: (node) => BUCKET_COLORS[node.properties.bucket as string] ?? '#6b7280',
    edgeStyle: (_link, selected) => {
      const dim = selected !== null
      return {
        color: dim ? 'rgba(120,120,140,0.06)' : 'rgba(120,120,140,0.25)',
        dash: null,
        width: 1.5,
      }
    },
    nodeLabel: (node) => node.name.length > 14 ? node.name.slice(0, 14) + '…' : node.name,
    onNodeClick: (nodeId) => void handleNodeClick(nodeId),
  })

  // Clear detail when selection is cleared by hook
  useEffect(() => {
    if (!selectedNode) {
      setDetail(null)
    }
  }, [selectedNode])

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

        {selectedNode && (
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
