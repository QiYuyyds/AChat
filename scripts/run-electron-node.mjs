// 跨平台用 Electron 内嵌 Node（ABI 与 packaged app 一致）跑 npm CLI 入口。
//
// 用法：node scripts/run-electron-node.mjs <script-path> [args...]
//
// 为什么：better-sqlite3 .node 文件绑定特定 ABI。Electron 33 用 ABI 130（私有），
// 系统 Node 24 用 ABI 137。build/start/db script 通过这个 wrapper 跑在 Electron
// Node 下；对应 package script 会先跑 scripts/ensure-electron-sqlite.mjs，把
// better-sqlite3 按需切到 Electron ABI。
//
// 详见 Spec 12 §6。

import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

const [, , scriptPath, ...args] = process.argv

if (!scriptPath) {
  console.error('Usage: node run-electron-node.mjs <script-path> [args...]')
  process.exit(1)
}

const electronPath = require('electron')
if (typeof electronPath !== 'string') {
  console.error('Unable to resolve Electron binary path')
  process.exit(1)
}

const child = spawn(electronPath, [scriptPath, ...args], {
  stdio: 'inherit',
  env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
})

child.on('exit', (code, signal) => {
  if (signal) {
    process.exit(0)
  } else {
    process.exit(code ?? 0)
  }
})
