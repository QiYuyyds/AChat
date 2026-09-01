import { app } from 'electron'
import { execFile, execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

/**
 * 桌面运行时装配（详见 openspec/changes/add-desktop-runtime design.md D1–D5）：
 *
 * - Next standalone 仅承担前端渲染与同源 /api/* rewrite（in-process require server.js）
 * - 业务后端是 Python sidecar：`python -m uvicorn app.main:app`，固定 127.0.0.1:8000
 * - 生命周期：spawn → 健康探活（30s）→ 崩溃自动重启（60s 窗口内上限 3 次）→ 优雅退出
 *   （先温和终止，5s 宽限期后 Windows taskkill /F /T 兜底），不遗留孤儿进程
 * - 启动失败一律抛 `StartupError`，由 main 呈现错误界面（绝不通透成白屏）
 */

const SIDECAR_PORT = 8000
const SIDECAR_PROBE_TIMEOUT_MS = 30_000
const NEXT_PROBE_TIMEOUT_MS = 15_000
const RESTART_WINDOW_MS = 60_000
const RESTART_LIMIT = 3
const QUIT_GRACE_MS = 5_000

export type StartupErrorKind =
  | 'port-occupied'
  | 'sidecar-timeout'
  | 'sidecar-crash-loop'
  | 'runtime-missing'
  | 'frontend-failed'

export class StartupError extends Error {
  constructor(
    readonly kind: StartupErrorKind,
    message: string,
  ) {
    super(message)
  }
}

let sidecar: ChildProcess | null = null
let shuttingDown = false
let restartTimes: number[] = []
let onRecovered: (() => void) | null = null

// ─── 对外入口 ─────────────────────────────────────────────────────────

/**
 * 启动完整桌面运行时：sidecar（dev/packaged）+ 打包模式下的 in-process Next 前端。
 * 返回前端 URL。dev 模式（AGENTHUB_DEV=1）前端复用 `pnpm dev` 的 :3000。
 */
export async function startDesktopRuntime(): Promise<string> {
  const isDev = process.env.AGENTHUB_DEV === '1'
  shuttingDown = false
  restartTimes = []

  await startSidecarSupervised()
  if (isDev) return 'http://localhost:3000'

  try {
    return `http://127.0.0.1:${await startEmbeddedServer()}`
  } catch (err) {
    throw new StartupError(
      'frontend-failed',
      `Next frontend failed to start: ${err instanceof Error ? err.message : String(err)}`,
    )
  }
}

/** 崩溃恢复后回调（main 用于 reload 窗口）。 */
export function setSidecarRecoveredListener(listener: (() => void) | null): void {
  onRecovered = listener
}

/** app 退出前调用：温和终止 → 5s 宽限 → 强杀进程树。 */
export async function shutdownSidecar(): Promise<void> {
  shuttingDown = true
  const child = sidecar
  if (!child || child.exitCode !== null || child.signalCode !== null) return

  const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()))
  await terminateProcessTree(child.pid, false)
  let timedOut = false
  await Promise.race([
    exited,
    new Promise<void>((r) => setTimeout(() => { timedOut = true; r() }, QUIT_GRACE_MS)),
  ])
  if (timedOut) {
    await terminateProcessTree(child.pid, true)
  }
  sidecar = null
}

// ─── sidecar 生命周期 ────────────────────────────────────────────────

async function startSidecarSupervised(): Promise<void> {
  try {
    await probePortFree()
  } catch {
    throw new StartupError(
      'port-occupied',
      `Port ${SIDECAR_PORT} is already in use by another application`,
    )
  }

  const runtime = resolveSidecarRuntime()
  await spawnAndProbe(runtime)
}

async function spawnAndProbe(runtime: SidecarRuntime): Promise<void> {
  const child = spawnSidecar(runtime)
  sidecar = child
  child.on('exit', (code, signal) => {
    if (shuttingDown) return
    console.error(`[sidecar] exited unexpectedly (code=${code} signal=${signal})`)
    void handleUnexpectedExit(runtime)
  })

  const crashed = new Promise<never>((_, reject) =>
    child.once('exit', () =>
      reject(new StartupError('sidecar-timeout', 'sidecar exited before becoming ready')),
    ),
  )
  const probe = waitUntilReady(`http://127.0.0.1:${SIDECAR_PORT}/health`, SIDECAR_PROBE_TIMEOUT_MS)
  try {
    await Promise.race([probe, crashed])
  } catch (err) {
    if (err instanceof StartupError && err.kind === 'sidecar-timeout') throw err
    if (err instanceof StartupError) throw err
    throw new StartupError(
      'sidecar-timeout',
      `sidecar not ready after ${SIDECAR_PROBE_TIMEOUT_MS}ms: ${err instanceof Error ? err.message : String(err)}`,
    )
  }
  console.log('[sidecar] ready on port', SIDECAR_PORT)
}

async function handleUnexpectedExit(runtime: SidecarRuntime): Promise<void> {
  const now = Date.now()
  restartTimes = restartTimes.filter((t) => now - t < RESTART_WINDOW_MS)
  restartTimes.push(now)
  if (restartTimes.length > RESTART_LIMIT) {
    throw new StartupError(
      'sidecar-crash-loop',
      `sidecar restarted more than ${RESTART_LIMIT} times within ${RESTART_WINDOW_MS / 1000}s`,
    )
  }
  console.error(`[sidecar] restarting (${restartTimes.length}/${RESTART_LIMIT})`)
  try {
    await spawnAndProbe(runtime)
    onRecovered?.()
  } catch (err) {
    // 重启失败按崩溃循环上限处理；StartupError 由 main 呈现错误界面
    console.error('[sidecar] restart failed', err)
  }
}

interface SidecarRuntime {
  pythonBin: string
  appRoot: string
  cwd: string
}

function spawnSidecar(runtime: SidecarRuntime): ChildProcess {
  const dataDir = requireDataDir()
  const child = spawn(
    runtime.pythonBin,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(SIDECAR_PORT)],
    {
      cwd: runtime.cwd,
      env: {
        ...process.env,
        ...buildSidecarEnv(dataDir),
        PYTHONNOUSERSITE: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  )
  child.stdout?.on('data', (chunk: Buffer) => forwardSidecarLog('[sidecar]', chunk))
  child.stderr?.on('data', (chunk: Buffer) => forwardSidecarLog('[sidecar:err]', chunk))
  return child
}

function buildSidecarEnv(dataDir: string): Record<string, string> {
  const env: Record<string, string> = {
    // 桌面模式开关 + 单库 SQLite（design D5；get_current_user 桌面例外见 C2）
    AGENTHUB_DESKTOP: '1',
    AGENTHUB_DATA_DIR: dataDir,
    DATABASE_URL: `sqlite+aiosqlite:///${toPosixPath(path.join(dataDir, 'agenthub.db'))}`,
    DATABASE_LOCAL_URL: '',
    // 桌面默认无基础设施（独立降级）；dev 下屏蔽 .env.local 的 Milvus/Neo4j 重试
    MILVUS_HOST: '',
    NEO4J_URI: '',
    KAFKA_BROKERS: '',
    // 只读安装目录防 __pycache__；unbuffered 保证日志实时
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONUNBUFFERED: '1',
  }
  // 云端部署地址：dev 用 shell env 覆盖；打包构建期经 resources/cloud-config.json 注入
  const cloudUrl = readCloudApiUrl()
  if (cloudUrl) env.AGENTHUB_CLOUD_API_URL = cloudUrl
  // JWT 签名密钥：打包环境无 .env，首启生成并持久化在数据目录（重启后会话不失效）
  env.JWT_SECRET = ensureDesktopJwtSecret(dataDir)
  return env
}

function resolveSidecarRuntime(): SidecarRuntime {
  const isDev = process.env.AGENTHUB_DEV === '1'
  if (isDev) {
    const repoRoot = path.resolve(__dirname, '..')
    const backendDir = path.join(repoRoot, 'backend')
    const pythonBin =
      process.platform === 'win32'
        ? path.join(backendDir, '.venv', 'Scripts', 'python.exe')
        : path.join(backendDir, '.venv', 'bin', 'python')
    if (!fs.existsSync(pythonBin)) {
      throw new StartupError(
        'runtime-missing',
        'backend/.venv not found — create it before electron:dev (python -m venv .venv)',
      )
    }
    return { pythonBin, appRoot: path.join(backendDir, 'app'), cwd: backendDir }
  }

  // 打包：runtime 归档在 resources/python-runtime/（只读），首启解压到 userData（可写）
  const resourcesPath = process.resourcesPath
  const isWin = process.platform === 'win32'
  const archiveName = isWin ? 'python-runtime.zip' : 'python-runtime.tar.gz'
  const archivePath = path.join(resourcesPath, 'python-runtime', archiveName)
  if (!fs.existsSync(archivePath)) {
    throw new StartupError('runtime-missing', `Python runtime archive missing: ${archivePath}`)
  }
  const runtimeDir = path.join(app.getPath('userData'), 'python-runtime')
  extractRuntime(archivePath, runtimeDir)
  const pythonBin = isWin
    ? path.join(runtimeDir, 'python', 'python.exe')
    : path.join(runtimeDir, 'python', 'bin', 'python3')
  if (!fs.existsSync(pythonBin)) {
    throw new StartupError('runtime-missing', `Python runtime incomplete: ${pythonBin} not found`)
  }
  const backendRoot = path.join(resourcesPath, 'backend')
  if (!fs.existsSync(path.join(backendRoot, 'app', 'main.py'))) {
    throw new StartupError('runtime-missing', `Backend source missing: ${backendRoot}`)
  }
  return { pythonBin, appRoot: path.join(backendRoot, 'app'), cwd: backendRoot }
}

/** 归档 sha256 与 stamp 不一致（版本升级 / 解压中断）时重新解压。 */
function extractRuntime(archivePath: string, runtimeDir: string): void {
  const stampPath = path.join(runtimeDir, 'stamp.txt')
  const archiveHash = sha256File(archivePath)
  if (fs.existsSync(stampPath) && fs.readFileSync(stampPath, 'utf8').trim() === archiveHash) {
    return
  }
  fs.rmSync(runtimeDir, { recursive: true, force: true })
  fs.mkdirSync(runtimeDir, { recursive: true })
  const tarBin =
    process.platform === 'win32'
      ? path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'tar.exe')
      : 'tar'
  execFileSync(tarBin, ['-xf', archivePath, '-C', runtimeDir])
  fs.writeFileSync(stampPath, archiveHash)
  console.log('[sidecar] python runtime extracted to', runtimeDir)
}

function readCloudApiUrl(): string | null {
  if (process.env.AGENTHUB_CLOUD_API_URL) return process.env.AGENTHUB_CLOUD_API_URL
  if (app.isPackaged) {
    const configPath = path.join(process.resourcesPath, 'cloud-config.json')
    try {
      const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8')) as { cloudApiUrl?: string }
      return parsed.cloudApiUrl ?? null
    } catch {
      return null
    }
  }
  return null
}

function ensureDesktopJwtSecret(dataDir: string): string {
  const secretPath = path.join(dataDir, 'jwt-secret')
  try {
    const existing = fs.readFileSync(secretPath, 'utf8').trim()
    if (existing.length >= 32) return existing
  } catch {
    // 首启，落到下面的生成逻辑
  }
  const secret = crypto.randomBytes(48).toString('base64url')
  fs.writeFileSync(secretPath, secret, { encoding: 'utf8', mode: 0o600 })
  return secret
}

// ─── in-process Next 前端（仅打包模式） ───────────────────────────────

/**
 * In-process 启动 Next.js standalone server。
 *
 * - PORT 走系统分配的空闲端口，避免与 3000 / 用户已开端口冲突
 * - require 触发 standalone server.js 启动；server.js 内 createServer().listen(PORT)
 * - 探活直到 HEAD / 返回 < 500，最多 15s
 */
export async function startEmbeddedServer(): Promise<number> {
  const companion = readCompanionConfig()
  const enabled = companion.companionMode !== 'off' && !!companion.mobileDeviceToken
  const hostname = enabled ? '0.0.0.0' : '127.0.0.1'
  const port = enabled ? companion.companionPort : await getFreePort('127.0.0.1')

  process.env.PORT = String(port)
  process.env.HOSTNAME = hostname
  process.env.NEXT_TELEMETRY_DISABLED = '1'

  // app.getAppPath() 在打包模式下指向 app.asar；但 Next standalone 的 server.js
  // 入口第一行就 process.chdir(__dirname)，chdir 是真实文件系统系统调用，跨不进 asar。
  // 我们已经把 .next/standalone 走 asarUnpack 解出来了，require 时直接走 .asar.unpacked
  // 路径，让 Electron asar layer 不介入 —— __dirname 才会是真实磁盘路径。
  const appPath = app.getAppPath()
  const standaloneRoot = appPath.endsWith('.asar')
    ? appPath + '.unpacked'
    : appPath
  const standaloneEntry = path.join(
    standaloneRoot,
    '.next',
    'standalone',
    'server.js',
  )

  // 用 require 触发 listen；server.js 是 Next 生成的 CommonJS 入口
  // 注意：在 ESM 上下文里要换 createRequire；当前 main 是 CJS（tsconfig module=commonjs），可直接用
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  require(standaloneEntry)

  await waitUntilReady(`http://127.0.0.1:${port}/`, NEXT_PROBE_TIMEOUT_MS)
  return port
}

// ─── 工具 ────────────────────────────────────────────────────────────

/** 探活：每 250ms 打一次 GET；就绪或超时才返回。 */
async function waitUntilReady(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(url, { method: 'GET' })
      if (resp.status < 500) return
    } catch {
      // server 尚未起来
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new StartupError('sidecar-timeout', `not ready at ${url} after ${timeoutMs}ms`)
}

/** 8000 端口预检：能 connect 就说明被其他程序占用（单实例锁已排除自冲突）。 */
function probePortFree(): Promise<void> {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.unref()
    probe.once('error', (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE') reject(new Error(`port ${SIDECAR_PORT} in use`))
      else reject(err)
    })
    probe.listen(SIDECAR_PORT, '127.0.0.1', () => probe.close(() => resolve()))
  })
}

/** 温和终止（Windows `taskkill /T` 无 /F 递归通知）；force=true 时强杀整棵进程树。 */
function terminateProcessTree(pid: number | undefined, force: boolean): Promise<void> {
  if (!pid) return Promise.resolve()
  if (process.platform === 'win32') {
    const args = ['/PID', String(pid), '/T']
    if (force) args.push('/F')
    return execFilePromise('taskkill', args).catch(() => {})
  }
  try {
    process.kill(-pid, force ? 'SIGKILL' : 'SIGTERM')
  } catch {
    try {
      process.kill(pid, force ? 'SIGKILL' : 'SIGTERM')
    } catch {
      // 已退出
    }
  }
  return Promise.resolve()
}

function execFilePromise(bin: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(bin, args, { windowsHide: true }, (err) => (err ? reject(err) : resolve()))
  })
}

function forwardSidecarLog(prefix: string, chunk: Buffer): void {
  for (const line of chunk.toString('utf8').split('\n')) {
    const trimmed = line.trimEnd()
    if (trimmed) console.log(prefix, trimmed)
  }
}

/** 从系统拿一个 ephemeral 端口（监听 :0 → 读 actual port → close）。 */
function getFreePort(host: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.unref()
    probe.on('error', reject)
    probe.listen(0, host, () => {
      const addr = probe.address()
      if (addr && typeof addr === 'object') {
        probe.close(() => resolve(addr.port))
      } else {
        probe.close()
        reject(new Error('Failed to allocate ephemeral port'))
      }
    })
  })
}

function requireDataDir(): string {
  const dataDir = process.env.AGENTHUB_DATA_DIR
  if (!dataDir) throw new StartupError('runtime-missing', 'AGENTHUB_DATA_DIR not set')
  fs.mkdirSync(dataDir, { recursive: true })
  return dataDir
}

function sha256File(filePath: string): string {
  const hash = crypto.createHash('sha256')
  hash.update(fs.readFileSync(filePath))
  return hash.digest('hex')
}

function toPosixPath(p: string): string {
  return p.replace(/\\/g, '/')
}

interface CompanionConfig {
  companionMode?: 'off' | 'lan' | 'tailnet'
  mobileDeviceToken?: string | null
  companionPort?: number
}

function readCompanionConfig(): Required<CompanionConfig> {
  const fallback: Required<CompanionConfig> = {
    companionMode: 'off',
    mobileDeviceToken: null,
    companionPort: 60646,
  }

  const dataDir = process.env.AGENTHUB_DATA_DIR
  if (!dataDir) return fallback

  try {
    const raw = fs.readFileSync(path.join(dataDir, 'companion.json'), 'utf8')
    const parsed = JSON.parse(raw) as CompanionConfig
    return {
      companionMode: parsed.companionMode ?? fallback.companionMode,
      mobileDeviceToken: parsed.mobileDeviceToken ?? null,
      companionPort: parsed.companionPort ?? fallback.companionPort,
    }
  } catch {
    return fallback
  }
}
