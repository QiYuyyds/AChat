import { authFetch } from '@/lib/api'
import { API_BASE_URL } from '@/lib/config'

// ─── Types ──────────────────────────────────────────────────────────────────

export interface GraphStats {
  total_nodes: number
  total_edges: number
  entity_types: Array<{ type: string; count: number }>
}

export interface GraphSubgraphParams {
  keyword?: string
  max_depth?: number
  max_nodes?: number
  exclude_chunk?: boolean
}

export interface GraphNode {
  id: string
  name: string
  type: string
  labels: string[]
  properties: Record<string, unknown>
}

export interface GraphEdge {
  id: string
  source_id: string
  target_id: string
  type: string
  properties: Record<string, unknown>
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

// ─── API ─────────────────────────────────────────────────────────────────────

async function json<T>(req: Promise<Response>): Promise<T> {
  const res = await req
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function fetchGraphStats(): Promise<GraphStats> {
  return json<GraphStats>(
    authFetch(`${API_BASE_URL}/api/graph/stats`),
  )
}

export async function fetchGraphSubgraph(params: GraphSubgraphParams = {}): Promise<GraphData> {
  const search = new URLSearchParams()
  if (params.keyword) search.set('keyword', params.keyword)
  if (params.max_depth) search.set('max_depth', String(params.max_depth))
  if (params.max_nodes) search.set('max_nodes', String(params.max_nodes))
  if (params.exclude_chunk) search.set('exclude_chunk', 'true')
  const qs = search.toString()
  return json<GraphData>(
    authFetch(`${API_BASE_URL}/api/graph/subgraph${qs ? '?' + qs : ''}`),
  )
}

export async function fetchGraphLabels(): Promise<string[]> {
  const res = await authFetch(`${API_BASE_URL}/api/graph/labels`)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  const data = await res.json() as { labels: string[] }
  return data.labels
}
