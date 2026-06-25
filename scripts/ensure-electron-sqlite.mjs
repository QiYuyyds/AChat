// Ensure better-sqlite3 is compiled for Electron's embedded Node ABI.
import {
  isNativeBindingLoadError,
  rebuildBetterSqliteForElectron,
  runElectronSqliteCheck,
} from './sqlite-abi-utils.mjs'

function printResult(result) {
  if (result.stdout) process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
}

const first = runElectronSqliteCheck()
if (first.error) throw first.error
if (first.status === 0) process.exit(0)

const output = `${first.stdout ?? ''}\n${first.stderr ?? ''}`
if (!isNativeBindingLoadError(output)) {
  printResult(first)
  process.exit(first.status ?? 1)
}

console.log('[electron] better-sqlite3 ABI mismatch; rebuilding for Electron...')
rebuildBetterSqliteForElectron()

const retry = runElectronSqliteCheck()
if (retry.error) throw retry.error
if (retry.status !== 0) {
  printResult(retry)
  process.exit(retry.status ?? 1)
}
