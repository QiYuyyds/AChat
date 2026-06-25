import { app } from 'electron'
import path from 'node:path'

/**
 * 在 Electron main 启动早期注入 `AGENTHUB_DATA_DIR`，决定 SQLite DB 与 workspace
 * 文件的位置。后续 `src/db/client.ts` 与 `src/server/conversation-service.ts` 读这个 env。
 *
 * 详见 Spec 12 §5。
 */
export function setupDataDir(): void {
  // 已被外部（CI / e2e / 调用方）设置过就不动
  if (process.env.AGENTHUB_DATA_DIR) return

  if (app.isPackaged) {
    // macOS: ~/Library/Application Support/AgentHub/data
    // Windows: %APPDATA%\AgentHub\data
    process.env.AGENTHUB_DATA_DIR = path.join(app.getPath('userData'), 'data')
  } else {
    // electron:dev 走仓库根的 .agenthub-data（与 web 模式共用 DB / workspace）
    // __dirname = dist-electron/，回到仓库根再拼
    process.env.AGENTHUB_DATA_DIR = path.resolve(__dirname, '..', '.agenthub-data')
  }
}
