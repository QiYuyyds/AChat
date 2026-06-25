// 跨平台启动 electron 主进程的 dev 入口。
// 用 child_process.spawn 注入 AGENTHUB_DEV=1，避免引入 cross-env 依赖。
// 详见 Spec 12 §7.2。

import { spawn } from 'node:child_process'

const child = spawn('electron', ['dist-electron/main.js'], {
  stdio: 'inherit',
  env: { ...process.env, AGENTHUB_DEV: '1' },
  shell: process.platform === 'win32',
})

child.on('exit', (code, signal) => {
  if (signal) {
    process.exit(0)
  } else {
    process.exit(code ?? 0)
  }
})
