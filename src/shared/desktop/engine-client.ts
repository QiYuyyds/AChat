import {
  ENGINE_TOKEN_HEADER,
  getDesktopBridge,
  isDesktopMode,
  type DesktopEngineStatus,
} from './bridge'

/** Build absolute URL against the local engine when desktop mode is active. */
export function engineUrl(path: string): string | null {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  const base = bridge.engineBaseUrl.replace(/\/$/, '')
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${base}${suffix}`
}

/** Headers required for local engine calls (engine token only — not user JWT). */
export function engineAuthHeaders(
  extra?: HeadersInit,
): Record<string, string> {
  const bridge = getDesktopBridge()
  const headers: Record<string, string> = {
    ...(extra ? Object.fromEntries(new Headers(extra).entries()) : {}),
  }
  if (bridge?.engineToken) {
    headers[ENGINE_TOKEN_HEADER] = bridge.engineToken
  }
  return headers
}

/**
 * fetch() helper for local engine endpoints. No-ops as a throw if not desktop.
 * Does not attach cloud user JWT — that stays on official API calls.
 */
export async function engineFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const url = engineUrl(path)
  if (!url) {
    throw new Error('engineFetch called outside desktop mode (no achatDesktop)')
  }
  const headers = engineAuthHeaders(init?.headers)
  return fetch(url, {
    ...init,
    headers,
    // Local engine is loopback; credentials not required for engine token auth.
    credentials: 'omit',
  })
}

export async function probeEngineHealth(): Promise<DesktopEngineStatus> {
  if (!isDesktopMode()) return 'error'
  const bridge = getDesktopBridge()
  if (!bridge) return 'error'
  try {
    const status = await bridge.getEngineStatus()
    if (status === 'ready' || status === 'starting' || status === 'error') {
      // Double-check with HTTP when bridge claims ready.
      if (status === 'ready') {
        const res = await engineFetch('/healthz', { method: 'GET' })
        return res.ok ? 'ready' : 'error'
      }
      return status
    }
  } catch {
    // fall through to HTTP probe
  }
  try {
    const res = await engineFetch('/healthz', { method: 'GET' })
    return res.ok ? 'ready' : 'error'
  } catch {
    return 'error'
  }
}
