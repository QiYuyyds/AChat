/**
 * Desktop bridge contract injected by the Tauri shell as `window.achatDesktop`.
 * Pure web builds never inject this object — absence means normal browser mode.
 */

export type DesktopEngineStatus = 'starting' | 'ready' | 'error'

export interface AchatDesktopBridge {
  isDesktop: true
  engineBaseUrl: string
  engineToken: string
  appVersion: string
  selectDirectory(): Promise<string | null>
  openPath(path: string): Promise<void>
  getEngineStatus(): Promise<DesktopEngineStatus>
  restartEngine(): Promise<void>
}

export interface AchatDesktopWindow {
  achatDesktop?: AchatDesktopBridge
}

declare global {
  interface Window {
    achatDesktop?: AchatDesktopBridge
  }
}

const SESSION_TOKEN_KEY = 'achat_engine_token'
const SESSION_BASE_KEY = 'achat_engine_base'
const SESSION_VERSION_KEY = 'achat_engine_app_version'

function isLikelyTauriShell(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as Window & {
    __TAURI_INTERNALS__?: unknown
    __TAURI__?: unknown
  }
  return w.__TAURI_INTERNALS__ != null || w.__TAURI__ != null
}

function readSession(key: string): string {
  try {
    return sessionStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeSession(token: string, base: string, version?: string): void {
  try {
    if (token) sessionStorage.setItem(SESSION_TOKEN_KEY, token)
    if (base) sessionStorage.setItem(SESSION_BASE_KEY, base)
    if (version) sessionStorage.setItem(SESSION_VERSION_KEY, version)
  } catch {
    // ignore
  }
}

function invokeBridgeMethods(): Pick<
  AchatDesktopBridge,
  'selectDirectory' | 'openPath' | 'getEngineStatus' | 'restartEngine'
> {
  return {
    selectDirectory: async () => {
      try {
        return (
          (await (
            window as Window & {
              __TAURI_INTERNALS__?: { invoke: (c: string) => Promise<string | null> }
            }
          ).__TAURI_INTERNALS__?.invoke('select_directory')) ?? null
        )
      } catch {
        return null
      }
    },
    openPath: async (path: string) => {
      try {
        await (
          window as Window & {
            __TAURI_INTERNALS__?: {
              invoke: (c: string, a: { path: string }) => Promise<void>
            }
          }
        ).__TAURI_INTERNALS__?.invoke('open_path', { path })
      } catch {
        // ignore
      }
    },
    getEngineStatus: async () => {
      try {
        const s = await (
          window as Window & {
            __TAURI_INTERNALS__?: { invoke: (c: string) => Promise<string> }
          }
        ).__TAURI_INTERNALS__?.invoke('get_engine_status')
        if (s === 'ready' || s === 'starting' || s === 'error') return s
      } catch {
        // ignore
      }
      return 'ready'
    },
    restartEngine: async () => {
      await (
        window as Window & {
          __TAURI_INTERNALS__?: { invoke: (c: string) => Promise<void> }
        }
      ).__TAURI_INTERNALS__?.invoke('restart_engine')
    },
  }
}

/**
 * Cold-start handoff:
 * 1. Shell navigates to `/?__et=<token>` (preferred, always wins)
 * 2. Shell `window.eval` injects `window.achatDesktop`
 * 3. sessionStorage cache (stable token across restarts once shell uses persistent token)
 */
function bootstrapBridgeFromUrlAndSession(): void {
  if (typeof window === 'undefined') return

  // 1) URL handoff always wins (fresh navigation from shell).
  try {
    const url = new URL(window.location.href)
    const et = url.searchParams.get('__et')
    if (et) {
      writeSession(et, window.location.origin)
      url.searchParams.delete('__et')
      const clean = `${url.pathname}${url.search}${url.hash}`
      window.history.replaceState({}, '', clean || '/')
      window.achatDesktop = {
        isDesktop: true,
        engineBaseUrl: window.location.origin,
        engineToken: et,
        appVersion: readSession(SESSION_VERSION_KEY) || '0.0.0',
        ...invokeBridgeMethods(),
      }
      return
    }
  } catch {
    // ignore
  }

  // 2) Live inject from shell — sync cache and keep it.
  const existing = window.achatDesktop
  if (existing?.isDesktop === true && existing.engineToken) {
    writeSession(
      existing.engineToken,
      existing.engineBaseUrl || window.location.origin,
      existing.appVersion,
    )
    return
  }

  // 3) Rebuild from session (stable token) or same-origin loopback in Tauri shell.
  if (!isLikelyTauriShell() && !readSession(SESSION_TOKEN_KEY)) {
    return
  }

  const token = readSession(SESSION_TOKEN_KEY)
  if (!token) {
    // Tauri shell but no token yet: mark desktop with empty token so callers can wait.
    if (isLikelyTauriShell()) {
      window.achatDesktop = {
        isDesktop: true,
        engineBaseUrl: window.location.origin,
        engineToken: '',
        appVersion: '0.0.0',
        ...invokeBridgeMethods(),
      }
    }
    return
  }

  const base = readSession(SESSION_BASE_KEY) || window.location.origin
  const version = readSession(SESSION_VERSION_KEY) || '0.0.0'
  window.achatDesktop = {
    isDesktop: true,
    engineBaseUrl: base,
    engineToken: token,
    appVersion: version,
    ...invokeBridgeMethods(),
  }
}

if (typeof window !== 'undefined') {
  bootstrapBridgeFromUrlAndSession()
  window.addEventListener('achat-desktop-ready', () => {
    bootstrapBridgeFromUrlAndSession()
  })
}

/** Wait until shell has provided a non-empty engine token (or timeout). */
export async function waitForEngineToken(timeoutMs = 8000): Promise<AchatDesktopBridge | null> {
  if (typeof window === 'undefined') return null
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    bootstrapBridgeFromUrlAndSession()
    const b = window.achatDesktop
    if (b?.isDesktop === true && b.engineToken) {
      return b
    }
    await new Promise((r) => setTimeout(r, 50))
  }
  bootstrapBridgeFromUrlAndSession()
  const b = window.achatDesktop
  if (b?.isDesktop === true && b.engineToken) return b
  return b?.isDesktop === true ? b : null
}

export function isDesktopMode(): boolean {
  if (typeof window === 'undefined') return false
  if (window.achatDesktop?.isDesktop === true) return true
  if (isLikelyTauriShell()) return true
  bootstrapBridgeFromUrlAndSession()
  return window.achatDesktop?.isDesktop === true
}

export function getDesktopBridge(): AchatDesktopBridge | null {
  if (typeof window === 'undefined') return null
  bootstrapBridgeFromUrlAndSession()
  const bridge = window.achatDesktop
  if (!bridge || bridge.isDesktop !== true) return null
  return bridge
}

export const ENGINE_TOKEN_HEADER = 'X-Engine-Token'
