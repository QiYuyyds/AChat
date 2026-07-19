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

export function isDesktopMode(): boolean {
  return typeof window !== 'undefined' && window.achatDesktop?.isDesktop === true
}

export function getDesktopBridge(): AchatDesktopBridge | null {
  if (typeof window === 'undefined') return null
  const bridge = window.achatDesktop
  if (!bridge || bridge.isDesktop !== true) return null
  return bridge
}

export const ENGINE_TOKEN_HEADER = 'X-Engine-Token'
