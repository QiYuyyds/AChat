/**
 * Frontend desktop capability helper.
 * Safe to import from web — all desktop paths no-op when bridge is absent.
 */

import {
  ENGINE_TOKEN_HEADER,
  alignLoopbackHost,
  engineFetch,
  engineUrl,
  getDesktopBridge,
  isDesktopMode,
  probeEngineHealth,
  urlTargetsEngine,
  waitForEngineToken,
  type DesktopEngineStatus,
} from '@/shared/desktop'

export {
  ENGINE_TOKEN_HEADER,
  alignLoopbackHost,
  engineFetch,
  engineUrl,
  getDesktopBridge,
  isDesktopMode,
  probeEngineHealth,
  urlTargetsEngine,
  waitForEngineToken,
  type DesktopEngineStatus,
}

/** Prefer native folder picker in desktop; returns null if cancelled or not desktop. */
export async function selectLocalDirectory(): Promise<string | null> {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.selectDirectory()
}

export async function restartLocalEngine(): Promise<void> {
  const bridge = getDesktopBridge()
  if (!bridge) {
    throw new Error('restartLocalEngine is only available in desktop mode')
  }
  await bridge.restartEngine()
}

export async function getLocalEngineStatus(): Promise<DesktopEngineStatus | 'web'> {
  if (!isDesktopMode()) return 'web'
  return probeEngineHealth()
}

/**
 * Desktop routing (v1 pivot):
 * - **All** business REST/SSE → local engine (`engineBaseUrl`)
 * - Native: selectDirectory / engine status / restart via bridge
 * - Pure web (no bridge): unchanged official API_BASE_URL
 *
 * Legacy dual-plane cloud prefixes are gated off (empty) by default.
 */
export const DESKTOP_LOCAL_ENGINE_PATH_PREFIXES = [
  '/api/',
  '/healthz',
  '/health',
] as const

/** @deprecated v0 dual-plane; empty so desktop never routes business traffic to remote API. */
export const DESKTOP_OFFICIAL_CLOUD_PATH_PREFIXES = [] as const

/**
 * Base URL for API traffic in desktop mode = engine (page-aligned host); pure web = officialBase.
 */
export function executionBaseUrl(officialBase: string): string {
  if (!isDesktopMode()) return officialBase
  const bridge = getDesktopBridge()
  if (!bridge?.engineBaseUrl) return officialBase
  return alignLoopbackHost(bridge.engineBaseUrl)
}

/** True when `url` targets the local engine in desktop mode (loopback-aware). */
export function isExecutionUrl(url: string, officialBase: string): boolean {
  if (!isDesktopMode()) return false
  const engine = executionBaseUrl(officialBase)
  if (!engine) {
    // Same-origin desktop (static UI served by engine): relative paths
    return url.startsWith('/api') || url.startsWith('/health')
  }
  return urlTargetsEngine(url, engine)
}

/**
 * Attach engine token whenever the request is going to the local engine.
 * Desktop v1: all business traffic is engine-bound — prefer always-on attach.
 */
export function attachEngineTokenHeaders(
  headers: Record<string, string>,
  url?: string,
  officialBase?: string,
): Record<string, string> {
  if (!isDesktopMode()) return headers
  // When URL is known, only attach for engine targets; otherwise attach (desktop default).
  if (url != null && officialBase != null && !isExecutionUrl(url, officialBase)) {
    return headers
  }
  try {
    const bridge = getDesktopBridge()
    if (bridge?.engineToken) {
      headers[ENGINE_TOKEN_HEADER] = bridge.engineToken
    }
  } catch {
    // ignore
  }
  return headers
}
