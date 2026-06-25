import type {
  ConnectionConfig,
  MobileAskUserAnswers,
  MobileArtifact,
  MobileConversationDetail,
  MobileSnapshot,
} from '../types'

export interface MobileApiClient {
  getSnapshot(): Promise<MobileSnapshot>
  getConversation(id: string): Promise<MobileConversationDetail>
  getArtifact(id: string): Promise<MobileArtifact>
  sendMessage(id: string, content: string): Promise<void>
  withdrawMessage(conversationId: string, messageId: string): Promise<void>
  editMessage(conversationId: string, messageId: string, content: string): Promise<void>
  regenerate(conversationId: string): Promise<void>
  decidePendingWrite(id: string, action: 'approve' | 'reject'): Promise<void>
  answerPendingQuestion(id: string, answers: MobileAskUserAnswers): Promise<void>
}

export function createMobileApiClient(config: ConnectionConfig): MobileApiClient {
  const baseUrl = normalizeBaseUrl(config.baseUrl)

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    if (!baseUrl) throw new Error('Desktop host is not configured')
    if (!config.deviceToken) throw new Error('Device token is not configured')

    const res = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${config.deviceToken}`,
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    }).catch((err) => {
      const message = err instanceof Error ? err.message : String(err)
      if (isLoopbackUrl(baseUrl)) {
        throw new Error(
          `Cannot reach ${baseUrl}. If this is running on a real phone, use the desktop LAN/Tailscale IP instead of localhost.`,
        )
      }
      throw new Error(`Cannot reach ${baseUrl}: ${message}`)
    })

    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
    }
    return res.json() as Promise<T>
  }

  return {
    getSnapshot: () => request<MobileSnapshot>('/api/mobile/snapshot'),
    getConversation: (id) => request<MobileConversationDetail>(`/api/mobile/conversations/${id}`),
    getArtifact: async (id) => {
      const result = await request<{ artifact: MobileArtifact }>(`/api/mobile/artifacts/${id}`)
      return result.artifact
    },
    sendMessage: async (id, content) => {
      await request<{ messageId: string; runIds: string[] }>(
        `/api/mobile/conversations/${id}/messages`,
        {
          method: 'POST',
          body: JSON.stringify({ content }),
        },
      )
    },
    decidePendingWrite: async (id, action) => {
      await request<{ ok: true }>(`/api/mobile/pending-writes/${id}`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
    },
    answerPendingQuestion: async (id, answers) => {
      await request<{ ok: true }>(`/api/mobile/pending-questions/${id}`, {
        method: 'POST',
        body: JSON.stringify({ answers }),
      })
    },
    withdrawMessage: async (conversationId, messageId) => {
      await request(`/api/mobile/conversations/${conversationId}/messages/${messageId}/withdraw`, {
        method: 'POST',
      })
    },
    editMessage: async (conversationId, messageId, content) => {
      await request(`/api/mobile/conversations/${conversationId}/messages/${messageId}/edit`, {
        method: 'POST',
        body: JSON.stringify({ content }),
      })
    },
    regenerate: async (conversationId) => {
      await request(`/api/mobile/conversations/${conversationId}/regenerate`, { method: 'POST' })
    },
  }
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

function isLoopbackUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
  } catch {
    return false
  }
}
