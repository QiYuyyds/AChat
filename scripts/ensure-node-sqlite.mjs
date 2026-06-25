// 确保 better-sqlite3 是「当前 Node」的 ABI —— dev/test/e2e 跑在纯 Node 下。
//
// 为什么 dev 不再走 run-electron-node:在 ELECTRON_RUN_AS_NODE 下,Next dev server
// 的请求/渲染 worker 起不来,所有 HTTP 请求挂死(0 字节)。纯 Node 下 Next dev 正常。
// 但仓库为 Electron build/db 会把 better-sqlite3 钉在 Electron ABI;故 Node 命令启动时
// 检测 ABI,不符就为当前 Node 重新编译。Electron 命令走 ensure-electron-sqlite。
// 一份 .node 只能是一种 ABI,两模式切换时由 package scripts 自动按需 rebuild。
import { isNativeBindingLoadError, runPnpm, verifyBetterSqliteBinding } from './sqlite-abi-utils.mjs'

try {
  verifyBetterSqliteBinding()
} catch (err) {
  if (isNativeBindingLoadError(err)) {
    console.log('[node] better-sqlite3 ABI mismatch; rebuilding for current Node...')
    runPnpm(['rebuild', 'better-sqlite3'])
    verifyBetterSqliteBinding()
  } else {
    throw err
  }
}
