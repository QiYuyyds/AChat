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
  showLoginDialog: boolean

  initialize: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  vipLogin: (password: string) => Promise<void>
  register: (email: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshToken: () => Promise<boolean>
  updateAvatar: (avatarUrl: string) => void
  openLoginDialog: () => void
  closeLoginDialog: () => void
}

const TOKEN_STORAGE_KEY = 'agenthub_access_token'
const AUTH_CACHE_KEY = 'agenthub_auth_cache'

const DEFAULT_CONFIG: AuthConfig = {
  allowRegistration: false,
  vipLoginEnabled: false,
}

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

export function hasToken(): boolean {
  return getAccessToken() !== null
}

function _storeAuthCache(user: AuthUser | null, config: AuthConfig): void {
  try {
    localStorage.setItem(
      AUTH_CACHE_KEY,
      JSON.stringify({ user, config }),
    )
  } catch {
    // best-effort
  }
}

function _loadCachedAuth(): { user: AuthUser | null; config: AuthConfig } | null {
  try {
    const raw = localStorage.getItem(AUTH_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { user: AuthUser | null; config: AuthConfig }
    return {
      user: parsed.user ?? null,
      config: {
        allowRegistration: parsed.config?.allowRegistration ?? false,
        vipLoginEnabled: parsed.config?.vipLoginEnabled ?? false,
      },
    }
  } catch {
    return null
  }
}

function _clearAuthCache(): void {
  try {
    localStorage.removeItem(AUTH_CACHE_KEY)
  } catch {
    // best-effort
  }
}

let refreshPromise: Promise<boolean> | null = null

/** 桌面代理错误体是 {"detail": "..."} JSON；解析出人话，失败退回原文。 */
function extractErrorMessage(body: string, fallback: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: string }
    return parsed.detail ?? body
  } catch {
    return body || fallback
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  config: DEFAULT_CONFIG,
  isLoading: true,
  isAuthenticated: false,
  showLoginDialog: false,

  initialize: async () => {
    // Listen for auth-expired events from authFetch 401 fallback
    const onAuthExpired = () => {
      set({ isAuthenticated: false, showLoginDialog: true })
    }
    window.addEventListener('auth-expired', onAuthExpired as EventListener)

    // 桌面模式分支：/api/desktop/session 存在即桌面形态。有 cloud_session 标记
    // 直接进入（离线容忍）；无标记进登录页（云端强制登录，经本地 /api/auth/* 代理）。
    // web 模式该端点 404，走原有 token 流程，行为不变。
    try {
      const desktopRes = await fetch(`${API_BASE_URL}/api/desktop/session`, {
        credentials: 'include',
      })
      if (desktopRes.ok) {
        const desktop = (await desktopRes.json()) as {
          mode: string
          loggedIn: boolean
          user: { email: string; name: string; loggedInAt: number } | null
        }
        if (desktop.mode === 'desktop') {
          if (desktop.loggedIn && desktop.user) {
            set({
              user: {
                id: 'local_desktop_user',
                email: desktop.user.email,
                name: desktop.user.name,
                avatarUrl: null,
              },
              isAuthenticated: true,
              isLoading: false,
            })
          } else {
            set({ user: null, isAuthenticated: false, showLoginDialog: true, isLoading: false })
          }
          return
        }
      }
    } catch {
      // 探测失败（如 dev 后端未启动）→ 落回 web 流程
    }

    const token = getAccessToken()

    // Optimistic path: token present → render immediately, verify in background
    if (token) {
      const cached = _loadCachedAuth()
      set({
        user: cached?.user ?? null,
        config: cached?.config ?? DEFAULT_CONFIG,
        isAuthenticated: true,
        isLoading: false,
      })

      // Background verification — update user/config if they changed
      try {
        const res = await fetch(`${API_BASE_URL}/api/auth/me`, {
          credentials: 'include',
          headers: { Authorization: `Bearer ${token}` },
        })
        if (res.ok) {
          const data = await res.json()
          const newConfig: AuthConfig = {
            allowRegistration: data.config?.allowRegistration ?? false,
            vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
          }
          set({ user: data.user, config: newConfig })
          _storeAuthCache(data.user, newConfig)
        }
        // 401 is handled by authFetch's auth-expired event for API calls;
        // this bare fetch doesn't go through authFetch, so handle 401 here
        if (res.status === 401) {
          clearToken()
          _clearAuthCache()
          set({ user: null, isAuthenticated: false, showLoginDialog: true })
        }
      } catch {
        // Network error — keep optimistic state; authFetch will handle it
        // on the next API call if the token is truly invalid
      }
      return
    }

    // No token — first-time user: fetch config to determine registration/VIP
    try {
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
      throw new Error(extractErrorMessage(body, `Login failed (${res.status})`))
    }
    const data = await res.json()
    storeToken(data.tokens?.access_token ?? '')
    const config: AuthConfig = {
      allowRegistration: data.config?.allowRegistration ?? false,
      vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
    }
    _storeAuthCache(data.user, config)
    set({
      user: data.user,
      config,
      isAuthenticated: true,
      showLoginDialog: false,
    })
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
    storeToken(data.tokens?.access_token ?? '')
    const config: AuthConfig = {
      allowRegistration: data.config?.allowRegistration ?? false,
      vipLoginEnabled: data.config?.vipLoginEnabled ?? true,
    }
    _storeAuthCache(data.user, config)
    set({
      user: data.user,
      config,
      isAuthenticated: true,
      showLoginDialog: false,
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
      throw new Error(extractErrorMessage(body, `Registration failed (${res.status})`))
    }
    const data = await res.json()
    storeToken(data.tokens?.access_token ?? '')
    const config: AuthConfig = {
      allowRegistration: data.config?.allowRegistration ?? true,
      vipLoginEnabled: data.config?.vipLoginEnabled ?? false,
    }
    _storeAuthCache(data.user, config)
    set({
      user: data.user,
      config,
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
    _clearAuthCache()
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
          const config: AuthConfig = {
            allowRegistration: data.config?.allowRegistration ?? get().config.allowRegistration,
            vipLoginEnabled: data.config?.vipLoginEnabled ?? get().config.vipLoginEnabled,
          }
          _storeAuthCache(data.user, config)
          set({
            user: data.user,
            config,
            isAuthenticated: true,
          })
          return true
        }
        clearToken()
        _clearAuthCache()
        set({ user: null, isAuthenticated: false })
        return false
      } catch {
        clearToken()
        _clearAuthCache()
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
      const updated = { ...state.user, avatarUrl }
      _storeAuthCache(updated, state.config)
      return { user: updated }
    })
  },

  openLoginDialog: () => {
    set({ showLoginDialog: true })
  },

  closeLoginDialog: () => {
    set({ showLoginDialog: false })
  },
}))
