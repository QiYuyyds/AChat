'use client'

import { create } from 'zustand'

import { API_BASE_URL } from '@/lib/config'

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

function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // localStorage may be unavailable (SSR, privacy mode)
  }
}

function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // best-effort
  }
}

/** Hand user JWT to local engine for cloud API calls (desktop only; never logs token). */
async function handoffDesktopSession(token: string | null, userId?: string | null): Promise<void> {
  try {
    const { engineFetch, isDesktopMode } = await import('@/lib/desktop')
    if (!isDesktopMode()) return
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
    // best-effort; engine may still be starting
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
      const res = await fetch(`${API_BASE_URL}/api/auth/me`, {
        credentials: 'include',
      })
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
        // Desktop engine needs the JWT for cloud mirror; cookie-only sessions
        // may not have localStorage token — refresh to obtain one.
        const existing = getAccessToken()
        if (existing) {
          void handoffDesktopSession(existing, data.user?.id)
        } else {
          void get().refreshToken()
        }
      } else {
        const configRes = await fetch(`${API_BASE_URL}/api/auth/config`, {
          credentials: 'include',
        })
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
    const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `Login failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token)
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
    const res = await fetch(`${API_BASE_URL}/api/auth/vip-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ password }),
    })
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `VIP login failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token)
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
    const res = await fetch(`${API_BASE_URL}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, name, password }),
    })
    if (!res.ok) {
      const body = await res.text()
      throw new Error(body || `Registration failed (${res.status})`)
    }
    const data = await res.json()
    const token = data.tokens?.access_token ?? ''
    storeToken(token)
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
      await fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
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
        const res = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        })
        if (res.ok) {
          const data = await res.json()
          const token = data.tokens?.access_token ?? ''
          storeToken(token)
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
