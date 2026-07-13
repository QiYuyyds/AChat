import { API_BASE_URL } from '@/lib/config'
import { authFetch } from '@/lib/api'

async function json<T>(req: Promise<Response>): Promise<T> {
  const res = await req
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<T>
}

// ─── Types ─────────────────────────────────────────────────────────────────

export interface LongTermMemoryItem {
  id: number
  content: string
  importance: number
  category: string
  tags: string[]
  scope: string
  agentId: string
  createdAt: number
  lastAccessed: number
}

export interface LongTermMemoryListResponse {
  items: LongTermMemoryItem[]
  total: number
  page: number
  size: number
}

export interface LTMUpdateBody {
  content?: string
  importance?: number
  category?: string
  tags?: string[]
}

export interface PreferenceItem {
  key: string
  value: string
}

export interface PreferenceListResponse {
  items: PreferenceItem[]
  total: number
}

export interface SessionMemoryItem {
  conversationId: string
  title: string
  summary: string
  coversUpTo: number | null
  createdAt: number
}

export interface SessionMemoryListResponse {
  items: SessionMemoryItem[]
  total: number
}

export interface SessionMemoryDetail {
  conversationId: string
  title: string
  summary: string
  coversUpTo: number | null
}

// ─── LTM API ───────────────────────────────────────────────────────────────

export async function fetchLongTermMemories(params: {
  agentId?: string
  category?: string
  tag?: string
  page?: number
  size?: number
}): Promise<LongTermMemoryListResponse> {
  const search = new URLSearchParams()
  if (params.agentId) search.set('agent_id', params.agentId)
  if (params.category) search.set('category', params.category)
  if (params.tag) search.set('tag', params.tag)
  if (params.page) search.set('page', String(params.page))
  if (params.size) search.set('size', String(params.size))
  const qs = search.toString()
  return json<LongTermMemoryListResponse>(
    authFetch(`${API_BASE_URL}/api/memory/long-term${qs ? '?' + qs : ''}`),
  )
}

export async function updateLongTermMemory(
  id: number,
  body: LTMUpdateBody,
): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(
    authFetch(`${API_BASE_URL}/api/memory/long-term/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteLongTermMemory(id: number): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(
    authFetch(`${API_BASE_URL}/api/memory/long-term/${id}`, {
      method: 'DELETE',
    }),
  )
}

// ─── Preference API ────────────────────────────────────────────────────────

export async function fetchPreferences(): Promise<PreferenceListResponse> {
  return json<PreferenceListResponse>(
    authFetch(`${API_BASE_URL}/api/memory/preferences`),
  )
}

export async function updatePreference(
  key: string,
  value: string,
): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(
    authFetch(`${API_BASE_URL}/api/memory/preferences/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  )
}

export async function deletePreference(key: string): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(
    authFetch(`${API_BASE_URL}/api/memory/preferences/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    }),
  )
}

// ─── Session Memory API ───────────────────────────────────────────────────

export async function fetchSessionMemories(): Promise<SessionMemoryListResponse> {
  return json<SessionMemoryListResponse>(
    authFetch(`${API_BASE_URL}/api/memory/sessions`),
  )
}

export async function fetchSessionMemoryDetail(
  conversationId: string,
): Promise<SessionMemoryDetail> {
  return json<SessionMemoryDetail>(
    authFetch(`${API_BASE_URL}/api/memory/session/${conversationId}`),
  )
}
