import { app } from 'electron'
import path from 'node:path'

/**
 * 在 Electron main 启动早期注入 `AGENTHUB_DATA_DIR`，决定 SQLite DB 与 workspace
 * 文件的位置（sidecar 的 DATABASE_URL / 桌面数据管道都锚定这个目录）。
 *
 * 详见 openspec/changes/add-desktop-runtime/ design D5。
 */
export function setupDataDir(): void {
  if (!process.env.AGENTHUB_CODEGRAPH_RESOURCES) {
    process.env.AGENTHUB_CODEGRAPH_RESOURCES = app.isPackaged
      ? path.join(process.resourcesPath, 'codegraph')
      : path.resolve(__dirname, '..', 'resources', 'codegraph')
  }
  // 已被外部（CI / e2e / 调用方）设置过就不动
  if (process.env.AGENTHUB_DATA_DIR) return

  if (app.isPackaged) {
    // macOS: ~/Library/Application Support/AChat/data
    // Windows: %APPDATA%\AChat\data
    process.env.AGENTHUB_DATA_DIR = path.join(app.getPath('userData'), 'data')
  } else {
    // electron:dev 用独立的 .agenthub-data-desktop：桌面 sidecar 走单库模式（同一
    // SQLite 文件承载 27 张表），与 web dev 的双库文件混用会触发跨库 FK 重建互踩
    // __dirname = dist-electron/，回到仓库根再拼
    process.env.AGENTHUB_DATA_DIR = path.resolve(__dirname, '..', '.agenthub-data-desktop')
  }
}
