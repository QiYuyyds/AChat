import { contextBridge, ipcRenderer, webUtils } from 'electron'

/**
 * contextIsolation 白名单桥（详见 openspec/changes/add-desktop-runtime tasks 5.1）。
 * 只暴露两个能力：OS 原生目录选择对话框 + 拖拽文件的绝对路径解析。
 * sandbox: true 下 ipcRenderer / contextBridge / webUtils 均可用。
 */
contextBridge.exposeInMainWorld('electronAPI', {
  pickDirectory: (): Promise<string | null> => ipcRenderer.invoke('dialog:pick-directory'),
  getPathForFile: (file: File): string => webUtils.getPathForFile(file),
})
