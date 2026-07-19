'use client'

import { create } from 'zustand'

import { getApiBaseUrl } from '@/lib/config'
import {
  attachEngineTokenHeaders,
  isDesktopMode,
  waitForEngineToken,
} from '@/lib/desktop'

export interface AuthUser {
  id: string
  email: string
  name: string
  avatarUrl: string | null
}

interface AuthConfig {
  allowRegistration: boolean
  vipLoginEnabled: boolean
}

interface AuthState {
  user: AuthUser | null
  config: AuthConfig
  isLoading: boolean
  isAuthenticated: boolean

  initialize: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  vipLogin: (password: string) => Promise<void>
  register: (email: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshToken: () => Promise<boolean>
  updateAvatar: (avatarUrl: string) => void
}

const TOKEN_STORAGE_KEY = 'agenthub_access_token'
const REFRESH_STORAGE_KEY = 'agenthub_refresh_token'

function storeToken(token: string, refreshToken?: string | null): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
    if (refreshToken) {
      localStorage.setItem(REFRESH_STORAGE_KEY, refreshToken)
    }
  } catch {
    // localStorage may be unavailable (SSR, privacy mode)
  }
}

function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    localStorage.removeItem(REFRESH_STORAGE_KEY)
  } catch {
    // best-effort
  }
}

function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_STORAGE_KEY)
  } catch {
    return null
  }
}

function authRequestInit(init?: RequestInit): RequestInit {
  const headers: Record<string, string> = {
    ...(init?.headers ? Object.fromEntries(new Headers(init.headers).entries()) : {}),
  }
  // Desktop: always attach engine token for local engine auth endpoints.
  attachEngineTokenHeaders(headers)
  return {
    ...init,
    credentials: isDesktopMode() ? 'omit' : 'include',
    headers,
  }
}

async function ensureDesktopEngineReady(): Promise<void> {
  if (typeof window === 'undefined') return
  const w = window as Window & { __TAURI_INTERNALS__?: unknown; __TAURI__?: unknown }
  const looksDesktop =
    w.__TAURI_INTERNALS__ != null ||
    w.__TAURI__ != null ||
    window.achatDesktop?.isDesktop === true
  if (!looksDesktop) return
  await waitForEngineToken(8000)
}

/**
 * Optional legacy handoff for cloud_api_client feature flag.
 * v1 local JWT does not require this; keep best-effort no-op on failure.
 */
async function handoffDesktopSession(token: string | null, userId?: string | null): Promise<void> {
  try {
    const { engineFetch, isDesktopMode: desk } = await import('@/lib/desktop')
    if (!desk()) return
    if (token) {
      await engineFetch('/api/desktop/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, user_id: userId ?? null }),
      })
    } else {
      await engineFetch('/api/desktop/session', { method: 'DELETE' })
    }
  } catch {
    // best-effort
  }
}

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

let refreshPromise: Promise<boolean> | null = null

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  config: { allowRegistration: false, vipLoginEnabled: false },
  isLoading: true,
  isAuthenticated: false,

  initialize: async () => {
    try {
      // Desktop WebView: wait for shell token before any /api/auth/* call.
      await ensureDesktopEngineReady()

      const base = getApiBaseUrl()
      const token = getAccessToken()
      const headers: Record<string, string> = {}
      if (token) headers.Authorization = `Bearer ${token}`
      // Desktop without a bearer token cannot use cookies across engine origin —
      // treat as logged-out and show login (do not hang on failed cookie me).
      if (isDesktopMode() && !token) {
        const configRes = await fetch(`${base}/api/auth/config`, authRequestInit())
        const config = configRes.ok ? await configRes.json() : {}
        set({
          user: null,
          config: {
            allowRegistration: config.allowRegistration ?? false,
            vipLoginEnabled: config.vipLoginEnabled ?? false,
          },
          isAuthenticated: false,
          isLoading: false,
        })
        return
      }
      const res = await fetch(
        `${base}/api/auth/me`,
        authRequestInit({ headers }),
      )
      if (res.ok) {
        const data = await res.json()
        set({
          user: data.user,
          config: {
            allowRegistration: data.config?.allowRegistration ?? false,
            vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
          },
          isAuthenticated: true,
          isLoading: false,
        })
        // Cookie session may be valid without a localStorage access token.
        // Do NOT force refreshToken() here: empty-body refresh fails on desktop
        // and previously cleared auth, which can flap AuthGate between / and /login.
        const existing = getAccessToken()
        if (existing) {
          void handoffDesktopSession(existing, data.user?.id)
        }
      } else {
        const configRes = await fetch(`${base}/api/auth/config`, authRequestInit())
        const config = configRes.ok ? await configRes.json() : {}
        set({
          user: null,
          config: {
            allowRegistration: config.allowRegistration ?? false,
            vipLoginEnabled: config.vipLoginEnabled ?? false,
          },
          isAuthenticated: false,
          isLoading: false,
        })
      }
    } catch {
      set({ user: null, isAuthenticated: false, isLoading: false })
    }
  },

  login: async (email: string, password: string) => {
    await ensureDesktopEngineReady()
    const base = getApiBaseUrl()
    const doLogin = () =>
      fetch(
        `${base}/api/auth/login`,
        authRequestInit({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        }),
      )
    let res = await doLogin()
    // One retry after waiting for a late shell reinject (cold start race).
    if (res.status === 401 && isDesktopMode()) {
      const body = await res.clone().text()
      if (body.includes('Invalid engine token') || body.includes('Engine token')) {
        await waitForEngineToken(3000)
        res = await doLogin()
      }
    }
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `Login failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token, data.tokens?.refresh_token ?? null)
    set({
      user: data.user,
      config: {
        allowRegistration: data.config?.allowRegistration ?? false,
        vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
      },
      isAuthenticated: true,
    })
    void handoffDesktopSession(token || null, data.user?.id)
  },

  vipLogin: async (password: string) => {
    await ensureDesktopEngineReady()
    const base = getApiBaseUrl()
    const doVip = () =>
      fetch(
        `${base}/api/auth/vip-login`,
        authRequestInit({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password }),
        }),
      )
    let res = await doVip()
    if (res.status === 401 && isDesktopMode()) {
      const body = await res.clone().text()
      if (body.includes('Invalid engine token') || body.includes('Engine token')) {
        await waitForEngineToken(3000)
        res = await doVip()
      }
    }
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `VIP login failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token, data.tokens?.refresh_token ?? null)
    set({
      user: data.user,
      config: {
        allowRegistration: data.config?.allowRegistration ?? false,
        vipLoginEnabled: data.config?.vipLoginEnabled ?? true,
      },
      isAuthenticated: true,
    })
    void handoffDesktopSession(token || null, data.user?.id)
  },

  register: async (email: string, name: string, password: string) => {
    const base = getApiBaseUrl()
    const res = await fetch(
      `${base}/api/auth/register`,
      authRequestInit({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, name, password }),
      }),
    )
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `Registration failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token, data.tokens?.refresh_token ?? null)
    set({
      user: data.user,
      config: {
        allowRegistration: data.config?.allowRegistration ?? true,
        vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
      },
      isAuthenticated: true,
    })
    void handoffDesktopSession(token || null, data.user?.id)
  },

  logout: async () => {
    try {
      const base = getApiBaseUrl()
      await fetch(`${base}/api/auth/logout`, authRequestInit({ method: 'POST' }))
    } catch {
      // best-effort
    }
    void handoffDesktopSession(null)
    clearToken()
    set({ user: null, isAuthenticated: false })
  },

  refreshToken: async () => {
    if (refreshPromise) return refreshPromise
    refreshPromise = (async () => {
      try {
        const base = getApiBaseUrl()
        const refresh = getRefreshToken()
        // Desktop (cross-origin) has no cookie: must send refreshToken in body.
        // Web can rely on cookie; body still works when present.
        const res = await fetch(
          `${base}/api/auth/refresh`,
          authRequestInit({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
              refresh ? { refreshToken: refresh } : {},
            ),
          }),
        )
        if (res.ok) {
          const data = await res.json()
          const token = data.tokens?.access_token ?? ''
          storeToken(token, data.tokens?.refresh_token ?? refresh)
          set({
            user: data.user,
            config: {
              allowRegistration: data.config?.allowRegistration ?? get().config.allowRegistration,
              vipLoginEnabled: data.config?.vipLoginEnabled ?? get().config.vipLoginEnabled,
            },
            isAuthenticated: true,
          })
          void handoffDesktopSession(token || null, data.user?.id)
          return true
        }
        void handoffDesktopSession(null)
        clearToken()
        set({ user: null, isAuthenticated: false })
        return false
      } catch {
        clearToken()
        set({ user: null, isAuthenticated: false })
        return false
      } finally {
        refreshPromise = null
      }
    })()
    return refreshPromise
  },

  updateAvatar: (avatarUrl: string) => {
    set((state) => {
      if (!state.user) return state
      return { user: { ...state.user, avatarUrl } }
    })
  },
}))
