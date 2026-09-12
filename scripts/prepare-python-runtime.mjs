// 构建期组装桌面端 Python 运行时：embedded CPython + 预装 site-packages。
// 产物 resources/python-runtime/python-runtime-<platform>.<zip|tar.gz> 由 electron-builder
// 经 extraResources 打进安装包（只读），运行时由 electron/server-bootstrap.ts 首启解压到
// userData 并以 `python -m uvicorn app.main:app` 拉起 sidecar。
//
// 设计：add-desktop-runtime design.md D2（PyInstaller 否决：冷启动慢 / 杀软误报 / 无法裁剪）。
// 依赖清单：backend/requirements-desktop.txt（与 pyproject.toml dependencies 同步维护；
// OCR / milvus / neo4j / asyncpg 刻意不装，后端 lazy import + 独立降级）。
//
// 平台矩阵起步：win32-x64 与 darwin-arm64（CI 矩阵同范围）。
// 用法：node scripts/prepare-python-runtime.mjs [--force]

import crypto from 'node:crypto'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const force = process.argv.includes('--force')

// python-build-standalone（astral-sh），sha256 已对照官方 SHA256SUMS 核验后钉死。
// 升级 CPython 时：下载新 release → 更新 url + sha256（并跑一遍本脚本验证）。
const PBS_TAG = '20260901'
const CPYTHON_VERSION = '3.12.14'
const PBS_BASE = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}`
const PINNED = {
  'win32-x64': {
    asset: `cpython-${CPYTHON_VERSION}+${PBS_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz`,
    sha256: 'e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f',
  },
  'darwin-arm64': {
    asset: `cpython-${CPYTHON_VERSION}+${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz`,
    sha256: '3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76',
  },
}

const root = process.cwd()
const outputDir = path.join(root, 'resources', 'python-runtime')
const cacheDir = path.join(outputDir, '.cache')
const requirementsPath = path.join(root, 'backend', 'requirements-desktop.txt')

function platformKey() {
  if (process.platform === 'win32' && process.arch === 'x64') return 'win32-x64'
  if (process.platform === 'darwin' && process.arch === 'arm64') return 'darwin-arm64'
  throw new Error(
    `No pinned Python runtime for ${process.platform}-${process.arch} (matrix: win32-x64, darwin-arm64)`,
  )
}

function sha256(filePath) {
  const hash = crypto.createHash('sha256')
  hash.update(fs.readFileSync(filePath))
  return hash.digest('hex')
}

// 统一走系统 tar：Windows 10+ 自带 bsdtar（System32\tar.exe，支持 zip 与 tgz），macOS 自带 tar。
// Windows 必须显式定位 System32 —— PATH 里先遇到 Git Bash 的 GNU tar 时会把 `D:\...`
// 解析成远程主机（"Cannot connect to D"）。
function systemTar() {
  if (process.platform === 'win32') {
    const bsdtar = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'tar.exe')
    if (!fs.existsSync(bsdtar)) {
      throw new Error('System32\\tar.exe not found (requires Windows 10 1803+)')
    }
    return bsdtar
  }
  return 'tar'
}

function extract(archivePath, destDir, zip) {
  const args = zip ? ['-xf', archivePath, '-C', destDir] : ['-xzf', archivePath, '-C', destDir]
  execFileSync(systemTar(), args, { stdio: 'inherit' })
}

function pack(stageDir, archivePath, zip) {
  const args = zip
    ? ['-a', '-cf', archivePath, '-C', stageDir, '.']
    : ['-czf', archivePath, '-C', stageDir, '.']
  execFileSync(systemTar(), args, { stdio: 'inherit' })
}

function stripRuntimeNoise(pythonRoot) {
  // __pycache__ 由 --no-compile 避免；这里兜底清 test 语料与 pip 缓存（安装包体积预算）。
  const candidates = [
    path.join(pythonRoot, 'Lib', 'test'),
    path.join(pythonRoot, 'Lib', 'idlelib'),
    path.join(pythonRoot, 'lib', 'python3.12', 'test'),
    path.join(pythonRoot, 'lib', 'python3.12', 'idlelib'),
    path.join(pythonRoot, 'Scripts'),
    path.join(pythonRoot, 'bin', 'pip3'),
    path.join(pythonRoot, 'bin', 'pip3.12'),
  ]
  for (const dir of candidates) {
    if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true })
  }
  const walk = (dir) => {
    let entries
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const p = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === '__pycache__') fs.rmSync(p, { recursive: true, force: true })
        else walk(p)
      }
    }
  }
  walk(pythonRoot)
}

const key = platformKey()
const pin = PINNED[key]
const isWin = key.startsWith('win32')
const archiveName = `python-runtime-${key}.${isWin ? 'zip' : 'tar.gz'}`
const archivePath = path.join(outputDir, archiveName)
fs.mkdirSync(outputDir, { recursive: true })

if (fs.existsSync(archivePath) && !force) {
  console.log(`✓ Python runtime ready: ${archiveName} (${(fs.statSync(archivePath).size / 1024 / 1024).toFixed(1)}MB) — --force 重建`)
  process.exit(0)
}

// ─── 1. 下载 CPython（缓存 + sha 校验） ───────────────────────────
fs.mkdirSync(cacheDir, { recursive: true })
const cpythonCache = path.join(cacheDir, pin.asset)
if (fs.existsSync(cpythonCache) && sha256(cpythonCache) !== pin.sha256) {
  console.log('! cached CPython archive hash mismatch — re-downloading')
  fs.rmSync(cpythonCache, { force: true })
}
if (!fs.existsSync(cpythonCache)) {
  console.log(`… downloading ${pin.asset}`)
  execFileSync('curl', ['-L', '--fail', '-o', cpythonCache, `${PBS_BASE}/${encodeURIComponent(pin.asset)}`], { stdio: 'inherit' })
}
const actualHash = sha256(cpythonCache)
if (actualHash !== pin.sha256) {
  throw new Error(`CPython archive sha256 mismatch: expected ${pin.sha256}, got ${actualHash}`)
}

// ─── 2. 解出 CPython 并预装 site-packages ─────────────────────────
const buildDir = path.join(cacheDir, `build-${key}`)
fs.rmSync(buildDir, { recursive: true, force: true })
fs.mkdirSync(buildDir, { recursive: true })
console.log('… extracting CPython')
extract(cpythonCache, buildDir, false)
const pythonRoot = path.join(buildDir, 'python')
if (!fs.existsSync(pythonRoot)) {
  throw new Error(`unexpected install_only layout: no python/ under ${buildDir}`)
}
const pythonBin = isWin
  ? path.join(pythonRoot, 'python.exe')
  : path.join(pythonRoot, 'bin', 'python3')

console.log('… installing site-packages (--no-compile)')
execFileSync(
  pythonBin,
  ['-m', 'pip', 'install', '--no-compile', '--no-cache-dir', '-r', requirementsPath],
  { stdio: 'inherit', env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: '1' } },
)

// ─── 3. 裁剪 + 打包 ───────────────────────────────────────────────
stripRuntimeNoise(pythonRoot)
const stamp = [
  `cpython=${CPYTHON_VERSION}+${PBS_TAG}`,
  `platform=${key}`,
  `requirements=${sha256(requirementsPath).slice(0, 12)}`,
  '',
].join('\n')
fs.writeFileSync(path.join(pythonRoot, '..', 'RUNTIME-VERSION'), stamp)

fs.rmSync(archivePath, { force: true })
console.log(`… packing ${archiveName}`)
pack(buildDir, archivePath, isWin)
fs.rmSync(buildDir, { recursive: true, force: true })
console.log(`✓ Python runtime built: ${archiveName} (${(fs.statSync(archivePath).size / 1024 / 1024).toFixed(1)}MB)`)
