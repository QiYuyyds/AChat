// Base URL for the backend API.
// - Pure web: NEXT_PUBLIC_API_BASE_URL or same-origin ('').
// - Desktop (window.achatDesktop): always the local engine base URL.

import { alignLoopbackHost } from '@/shared/desktop'

function desktopEngineBase(): string | null {
  if (typeof window === 'undefined') return null
  const bridge = window.achatDesktop
  if (!bridge?.isDesktop || !bridge.engineBaseUrl) return null
  return alignLoopbackHost(bridge.engineBaseUrl)
}

const ENV_API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? ''

/**
 * Resolve API base for the current runtime.
 * Prefer a function for desktop so late bridge injection still works.
 */
export function getApiBaseUrl(): string {
  return desktopEngineBase() ?? ENV_API_BASE
}

/** @deprecated Prefer getApiBaseUrl() in new code; kept for call-site compatibility. */
export const API_BASE_URL = ENV_API_BASE

// Proxy getter used by modules that imported a const — re-read on access via helper only.
// Existing `API_BASE_URL + '/api/...'` call sites are updated gradually; authFetch/stream use getApiBaseUrl.
