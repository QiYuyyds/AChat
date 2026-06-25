import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const require = createRequire(import.meta.url)

export function verifyBetterSqliteBinding() {
  const Database = require('better-sqlite3')
  const db = new Database(':memory:')
  try {
    db.prepare('select 1 as ok').get()
  } finally {
    db.close()
  }
}

export function isNativeBindingLoadError(errorOrText) {
  const code =
    errorOrText && typeof errorOrText === 'object' && 'code' in errorOrText
      ? errorOrText.code
      : null
  const text =
    typeof errorOrText === 'string'
      ? errorOrText
      : errorOrText instanceof Error
        ? `${errorOrText.message}\n${errorOrText.stack ?? ''}`
        : String(errorOrText ?? '')

  return (
    code === 'ERR_DLOPEN_FAILED' ||
    text.includes('NODE_MODULE_VERSION') ||
    text.includes('ERR_DLOPEN_FAILED') ||
    text.includes('better_sqlite3.node')
  )
}

export function runPnpm(args) {
  const command = process.platform === 'win32' ? (process.env.ComSpec ?? 'cmd.exe') : 'pnpm'
  const commandArgs = process.platform === 'win32' ? ['/d', '/s', '/c', 'pnpm.cmd', ...args] : args
  const result = spawnSync(command, commandArgs, { stdio: 'inherit' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

export function rebuildBetterSqliteForElectron() {
  const betterSqliteDir = path.dirname(require.resolve('better-sqlite3/package.json'))
  const electronVersion = getElectronVersion()

  if (installElectronPrebuild(betterSqliteDir, electronVersion)) return

  const nodeGypScript = findNodeGypScript()
  const nodeGypDevDir = path.resolve('node_modules/.cache/node-gyp')
  const result = spawnSync(
    process.execPath,
    [
      nodeGypScript,
      'configure',
      'build',
      '--release',
      '--runtime=electron',
      `--target=${electronVersion}`,
      '--dist-url=https://electronjs.org/headers',
      `--devdir=${nodeGypDevDir}`,
    ],
    {
      cwd: betterSqliteDir,
      stdio: 'inherit',
    },
  )
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function installElectronPrebuild(moduleDir, electronVersion) {
  const prebuildInstall = findPrebuildInstallScript(moduleDir)
  const result = spawnSync(
    process.execPath,
    [prebuildInstall, '--runtime', 'electron', '--target', electronVersion],
    {
      cwd: moduleDir,
      stdio: 'inherit',
    },
  )
  if (result.error) throw result.error
  return result.status === 0
}

export function runElectronSqliteCheck() {
  return spawnSync(getElectronBinary(), ['scripts/check-sqlite-binding.mjs'], {
    encoding: 'utf8',
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
  })
}

function getElectronVersion() {
  const pkg = require('electron/package.json')
  return pkg.version
}

function getElectronBinary() {
  const electronPath = require('electron')
  if (typeof electronPath !== 'string') {
    throw new Error('Unable to resolve Electron binary path')
  }
  return electronPath
}

function findNodeGypScript() {
  const candidates = [
    path.resolve('node_modules/.pnpm/node_modules/node-gyp/bin/node-gyp.js'),
    path.resolve('node_modules/node-gyp/bin/node-gyp.js'),
  ]

  const found = candidates.find((candidate) => fs.existsSync(candidate))
  if (!found) {
    throw new Error('Unable to find node-gyp script for rebuilding better-sqlite3')
  }
  return found
}

function findPrebuildInstallScript(moduleDir) {
  try {
    return require.resolve('prebuild-install/bin.js', { paths: [moduleDir, process.cwd()] })
  } catch {
    // pnpm may keep transitive CLIs only in the virtual store; scan it below.
  }

  const candidates = [
    ...findPnpmPrebuildInstallScripts(),
    path.resolve('node_modules/.pnpm/node_modules/prebuild-install/bin.js'),
    path.resolve('node_modules/prebuild-install/bin.js'),
  ]

  const found = candidates.find((candidate) => fs.existsSync(candidate))
  if (!found) {
    throw new Error('Unable to find prebuild-install script for rebuilding better-sqlite3')
  }
  return found
}

function findPnpmPrebuildInstallScripts() {
  const pnpmStoreDir = path.resolve('node_modules/.pnpm')
  if (!fs.existsSync(pnpmStoreDir)) return []

  return fs
    .readdirSync(pnpmStoreDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.startsWith('prebuild-install@'))
    .map((entry) =>
      path.join(pnpmStoreDir, entry.name, 'node_modules/prebuild-install/bin.js'),
    )
}
