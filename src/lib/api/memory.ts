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

// ─── Types: Memory Files (file-native) ─────────────────────────────────────

export interface MemoryFileItem {
  path: string
  name: string
  description: string
  bucket: string
  agentId: string | null
  tags: string[]
  importance: number
  createdAt: string
  updatedAt: string
  source: string
  bodyPreview: string
}

export interface MemoryFileListResponse {
  items: MemoryFileItem[]
  total: number
}

export interface MemoryFileDetail {
  path: string
  name: string
  description: string
  agentId: string | null
  tags: string[]
  importance: number
  bucket: string
  createdAt: string
  updatedAt: string
  source: string
  body: string
}

export interface MemoryFileWriteBody {
  name: string
  body: string
  description?: string
  agentId?: string | null
  tags?: string[]
  importance?: number
  bucket?: string
}

export interface MemorySearchResult {
  path: string
  name: string
  content: string
  score: number
  source: string
  frontmatter: Record<string, unknown>
}

export interface MemorySearchResponse {
  items: MemorySearchResult[]
  total: number
}

// ─── Types: Proactive ──────────────────────────────────────────────────────

export interface ProactiveTopic {
  title: string
  reason: string
  keywords: string[]
  evidence: string
  bucket: 'procedure' | 'personal' | 'wiki' | 'daily' | string
}

export interface ProactiveResponse {
  topics: ProactiveTopic[]
  total: number
}

// ─── Types: Preferences (preserved) ────────────────────────────────────────

export interface PreferenceItem {
  key: string
  value: string
}

export interface PreferenceListResponse {
  items: PreferenceItem[]
  total: number
}

// ─── Types: Session Memory (preserved) ─────────────────────────────────────

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

// ─── Memory File API ───────────────────────────────────────────────────────

export async function fetchMemoryFiles(params: {
  bucket?: string
  agentId?: string
}): Promise<MemoryFileListResponse> {
  const search = new URLSearchParams()
  if (params.bucket) search.set('bucket', params.bucket)
  if (params.agentId) search.set('agent_id', params.agentId)
  const qs = search.toString()
  return json<MemoryFileListResponse>(
    authFetch(`${API_BASE_URL}/api/memory/files${qs ? '?' + qs : ''}`),
  )
}

export async function readMemoryFile(path: string): Promise<MemoryFileDetail> {
  return json<MemoryFileDetail>(
    authFetch(`${API_BASE_URL}/api/memory/files/${encodeURIComponent(path)}`),
  )
}

export async function writeMemoryFile(
  path: string,
  body: MemoryFileWriteBody,
): Promise<{ ok: boolean; path: string }> {
  return json<{ ok: boolean; path: string }>(
    authFetch(`${API_BASE_URL}/api/memory/files/${encodeURIComponent(path)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteMemoryFile(path: string): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(
    authFetch(`${API_BASE_URL}/api/memory/files/${encodeURIComponent(path)}`, {
      method: 'DELETE',
    }),
  )
}

export async function searchMemoryFiles(params: {
  query: string
  topK?: number
  agentId?: string
  bucket?: string
}): Promise<MemorySearchResponse> {
  const search = new URLSearchParams()
  search.set('query', params.query)
  if (params.topK) search.set('top_k', String(params.topK))
  if (params.agentId) search.set('agent_id', params.agentId)
  if (params.bucket) search.set('bucket', params.bucket)
  return json<MemorySearchResponse>(
    authFetch(`${API_BASE_URL}/api/memory/search?${search.toString()}`),
  )
}

// ─── Proactive API ─────────────────────────────────────────────────────────

export async function fetchProactiveTopics(): Promise<ProactiveResponse> {
  return json<ProactiveResponse>(
    authFetch(`${API_BASE_URL}/api/memory/proactive`),
  )
}

export async function triggerAutoDream(): Promise<{ ok: boolean; result: unknown }> {
  return json<{ ok: boolean; result: unknown }>(
    authFetch(`${API_BASE_URL}/api/memory/auto-dream`, {
      method: 'POST',
    }),
  )
}

// ─── Preference API (preserved) ────────────────────────────────────────────

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

// ─── Session Memory API (preserved) ────────────────────────────────────────

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
