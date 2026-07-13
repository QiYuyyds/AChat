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
}

interface AuthState {
  user: AuthUser | null
  config: AuthConfig
  isLoading: boolean
  isAuthenticated: boolean

  initialize: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
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
  config: { allowRegistration: false },
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
          config: { allowRegistration: data.config?.allowRegistration ?? false },
          isAuthenticated: true,
          isLoading: false,
        })
      } else {
        set({ user: null, isAuthenticated: false, isLoading: false })
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
    storeToken(data.tokens?.access_token ?? '')
    set({
      user: data.user,
      config: { allowRegistration: data.config?.allowRegistration ?? false },
      isAuthenticated: true,
    })
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
    storeToken(data.tokens?.access_token ?? '')
    set({
      user: data.user,
      config: { allowRegistration: data.config?.allowRegistration ?? true },
      isAuthenticated: true,
    })
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
          storeToken(data.tokens?.access_token ?? '')
          set({
            user: data.user,
            config: { allowRegistration: data.config?.allowRegistration ?? get().config.allowRegistration },
            isAuthenticated: true,
          })
          return true
        }
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
