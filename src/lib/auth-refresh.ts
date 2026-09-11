/**
 * Single-flight access-token refresh shared by all consumers.
 *
 * Previously `src/lib/api.ts` (401 retry) and `auth-store.refreshToken`
 * (session init) each kept their own in-flight refresh promise, so the two
 * could fire concurrent refreshes and the last writer won the token. All
 * refreshes now go through `refreshAccessToken()`; consumers that need the
 * refreshed user/config register via `onRefreshSuccess` — this module stays
 * store-agnostic because the store sits above the api layer and importing it
 * from here would create an import cycle (see api.ts).
 */

import { API_BASE_URL } from '@/lib/config'

const TOKEN_STORAGE_KEY = 'agenthub_access_token'

/** Subset of the `/api/auth/refresh` response consumers rely on. */
export interface RefreshSuccessData {
  tokens?: { access_token?: string }
  user?: unknown
  config?: { allowRegistration?: boolean; vipLoginEnabled?: boolean }
}

type RefreshSuccessCallback = (data: RefreshSuccessData) => void

let _refreshPromise: Promise<boolean> | null = null
const _successCallbacks = new Set<RefreshSuccessCallback>()

function _storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // best-effort (SSR, privacy mode)
  }
}

/** Register a callback invoked (in registration order) after each successful
 * refresh. Returns an unsubscribe function. */
export function onRefreshSuccess(cb: RefreshSuccessCallback): () => void {
  _successCallbacks.add(cb)
  return () => {
    _successCallbacks.delete(cb)
  }
}

/** POST /api/auth/refresh once; concurrent callers share the in-flight
 * promise. On success the new token is written to localStorage before the
 * success callbacks run. Resolves `true` on HTTP 200, `false` otherwise
 * (callers keep their own failure handling). */
export function refreshAccessToken(): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise
  _refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) return false
      const data = (await res.json()) as RefreshSuccessData
      const token = data.tokens?.access_token
      if (token) {
        _storeToken(token)
      }
      for (const cb of _successCallbacks) {
        cb(data)
      }
      return true
    } catch {
      return false
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}
