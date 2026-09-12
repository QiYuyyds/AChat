'use client'

/**
 * Electron preload 桥的类型与安全访问器（详见 src/types/electron.d.ts）。
 * web 模式下 window.electronAPI 不存在 —— 所有调用点必须走这里的判空助手，
 * 并保留非桌面回退路径（DirPickerDialog listdir / 无拖拽绑定）。
 */

export interface ElectronBridge {
  pickDirectory: () => Promise<string | null>
  getPathForFile: (file: File) => string
}

export function getElectronBridge(): ElectronBridge | null {
  if (typeof window === 'undefined') return null
  return window.electronAPI ?? null
}

/** 桌面壳内运行（preload 注入了 electronAPI）时为 true。 */
export function isDesktopShell(): boolean {
  return getElectronBridge() !== null
}
