/**
 * Frontend desktop capability helper.
 * Safe to import from web — all desktop paths no-op when bridge is absent.
 */

import {
  ENGINE_TOKEN_HEADER,
  engineFetch,
  engineUrl,
  getDesktopBridge,
  isDesktopMode,
  probeEngineHealth,
  type DesktopEngineStatus,
} from '@/shared/desktop'

export {
  ENGINE_TOKEN_HEADER,
  engineFetch,
  engineUrl,
  getDesktopBridge,
  isDesktopMode,
  probeEngineHealth,
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
 * Desktop routing principle (v1):
 * - Auth / account / cloud-authoritative CRUD → official API (API_BASE_URL)
 * - Agent execution / local tools / local SSE → local engine (engineBaseUrl + token)
 *
 * Callers should use `isDesktopMode()` + `engineFetch` for execution plane.
 */
export const DESKTOP_LOCAL_ENGINE_PATH_PREFIXES = [
  '/api/messages', // send / stream execution may target engine in desktop (see task 6.3 wiring)
  '/api/stream',
  '/api/fs',
  '/api/pending',
  '/api/runs',
  '/healthz',
] as const

export const DESKTOP_OFFICIAL_CLOUD_PATH_PREFIXES = [
  '/api/auth',
  '/api/profile',
  '/api/settings',
  '/api/conversations',
  '/api/agents',
  '/api/documents',
  '/api/memory',
  '/api/skills',
  '/api/mcp',
] as const

/**
 * Base URL for Agent execution / tools / local stream in desktop mode.
 * Falls back to official API base for pure web.
 */
export function executionBaseUrl(officialBase: string): string {
  if (!isDesktopMode()) return officialBase
  const bridge = getDesktopBridge()
  return bridge?.engineBaseUrl?.replace(/\/$/, '') || officialBase
}

export function isExecutionUrl(url: string, officialBase: string): boolean {
  if (!isDesktopMode()) return false
  const engine = executionBaseUrl(officialBase)
  return engine.length > 0 && url.startsWith(engine)
}
