import type { ElectronBridge } from '@/lib/electron-bridge'

declare global {
  interface Window {
    /** 仅桌面壳（electron preload）注入；web 环境为 undefined */
    electronAPI?: ElectronBridge
  }
}

export {}
