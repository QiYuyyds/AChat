import { app, BrowserWindow, dialog, ipcMain, session, shell } from 'electron'
import path from 'node:path'

import { setupDataDir } from './paths'
import { setSidecarRecoveredListener, shutdownSidecar, startDesktopRuntime, StartupError } from './server-bootstrap'

// Electron 默认用 package.json 的 `name` 字段（'bytedance-agenthub'）作为 app 名，
// 用户数据会落在 ~/Library/Application Support/bytedance-agenthub/。覆盖成 productName 'AChat'，
// 让 userData 路径更友好；必须在任何 app.getPath('userData') 调用之前完成。
app.setName('AChat')

// 单实例锁：保证不会与自己的另一个实例争抢 sidecar 的 8000 端口
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const wins = BrowserWindow.getAllWindows()
    if (wins.length > 0) {
      const w = wins[0]
      if (w.isMinimized()) w.restore()
      w.focus()
    }
  })

  // 关键时序：DATA_DIR 必须在 sidecar spawn / 业务代码 require 之前注入
  setupDataDir()

  let win: BrowserWindow | null = null

  app.whenReady().then(async () => {
    // 5.1 原生目录选择对话框：preload 白名单 electronAPI.pickDirectory() 的后端
    ipcMain.handle('dialog:pick-directory', async () => {
      const target = win ?? BrowserWindow.getAllWindows()[0]
      if (!target) return null
      const result = await dialog.showOpenDialog(target, {
        properties: ['openDirectory', 'createDirectory'],
        title: '选择要绑定的工作目录',
      })
      if (result.canceled || result.filePaths.length === 0) return null
      return result.filePaths[0]
    })

    // 用户 shell 里若设了 http_proxy / HTTPS_PROXY，Chromium 会继承它去代理 localhost 请求，
    // 导致 BrowserWindow 加载本地 URL 时被代理拦截。强制 direct（proxyRules 空）并显式 bypass 本地。
    await session.defaultSession
      .setProxy({ proxyRules: 'direct://', proxyBypassRules: '<local>' })
      .catch((err) => console.error('[AChat] setProxy failed', err))

    win = createWindow()

    let url: string
    try {
      setSidecarRecoveredListener(() => {
        // sidecar 崩溃后恢复成功 → 重载窗口回到主界面（而非停留在报错状态）
        void win?.webContents.loadURL(lastLoadedUrl)
      })
      url = await startDesktopRuntime()
      lastLoadedUrl = url
      await win.loadURL(url)
    } catch (err) {
      const startup = err instanceof StartupError ? err : null
      console.error('[AChat] failed to start desktop runtime', err)
      await win.loadURL(
        `data:text/html;charset=utf-8,${encodeURIComponent(
          startupErrorHtml(startup?.kind ?? 'frontend-failed'),
        )}`,
      )
    }
  })

  let lastLoadedUrl = ''

  function createWindow(): BrowserWindow {
    const w = new BrowserWindow({
      width: 1280,
      height: 800,
      minWidth: 980,
      minHeight: 600,
      title: 'AChat',
      backgroundColor: '#0a0a0a',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        preload: path.join(__dirname, 'preload.js'),
        devTools: !app.isPackaged,
      },
    })

    // a) 外链交给 OS 默认浏览器，不在窗口里新开
    w.webContents.setWindowOpenHandler(({ url: target }) => {
      shell.openExternal(target).catch(() => {})
      return { action: 'deny' }
    })

    // b) 拦截站外导航；本地 server origin / 错误页(data:)放行。
    // file: 导航一律拒绝（拖拽文件进窗口的误导航防护，任务 5.3）。
    w.webContents.on('will-navigate', (event, target) => {
      if (target.startsWith('data:')) return
      if (target.startsWith('file:')) {
        event.preventDefault()
        return
      }
      const origin = new URL(target).origin
      if (lastLoadedUrl && origin !== new URL(lastLoadedUrl).origin) {
        event.preventDefault()
        shell.openExternal(target).catch(() => {})
      }
    })

    return w
  }

  function startupErrorHtml(kind: StartupError['kind']): string {
    const body =
      kind === 'port-occupied'
        ? `AChat 的本地服务端口 <b>8000</b> 被其他程序占用，无法启动。<br/>
请关闭占用该端口的程序（可运行 <code>netstat -ano | findstr :8000</code> 查找，
任务管理器结束对应进程），然后重新启动 AChat。`
        : kind === 'runtime-missing'
          ? `AChat 运行时组件缺失或未解压完整。<br/>
请重新安装 AChat；若问题仍存在，请联系支持并提供日志。`
          : `AChat 后端服务未能正常启动（可能已多次崩溃）。<br/>
请重启 AChat 重试；若反复出现，请重启电脑后再次尝试。`
    return `<!doctype html>
<html><head><meta charset="utf-8"><title>AChat 启动失败</title>
<style>
  body { background:#0a0a0a; color:#e5e5e5; font-family: system-ui, sans-serif;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
  .card { max-width:520px; padding:32px; border:1px solid #262626; border-radius:12px;
          background:#171717; line-height:1.7; }
  h1 { font-size:18px; margin:0 0 12px; color:#fbbf24; }
  code { background:#262626; padding:2px 6px; border-radius:4px; }
</style></head>
<body><div class="card"><h1>AChat 启动失败</h1>${body}</div></body></html>`
  }

  app.on('window-all-closed', () => {
    app.quit()
  })

  // 优雅退出：先停 sidecar（温和终止 → 5s 宽限 → 强杀进程树），再真正退出
  let quitting = false
  app.on('before-quit', (event) => {
    if (quitting) return
    quitting = true
    event.preventDefault()
    shutdownSidecar()
      .catch((err) => console.error('[AChat] sidecar shutdown failed', err))
      .finally(() => app.quit())
  })
}
