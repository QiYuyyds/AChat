// next build 的 standalone 输出在 pnpm 项目里有两个洞需要堵：
//
//   (1) Next.js 的 file tracer 在 .next/standalone/.next/node_modules/<pkg>-<hash> 留下指向源仓
//       node_modules/.pnpm 真身的**绝对路径 symlink**。这些 symlink 在 dev 机能 stat 通过，但
//       打包后跟着 .exe 散落到用户机，绝对路径就废了 → 编译产物 require('<pkg>-<hash>') 直接崩。
//       修：把这些 symlink **物化**成真目录（dereference + 拷贝），运行时 / 打包都安心。
//
//   (2) pnpm 的依赖 hoist 跟 Next tracer 配合不好，导致 next build 漏拷少量 server runtime
//       依赖（@next/env / @swc/helpers / openai / drizzle-orm ...）以及它们的间接依赖。
//       修：以 Next 已 trace 到 standalone 的包 + 明确 server runtime allowlist 为种子补齐。
//       不从 root package.json 全量遍历，避免把 Mermaid / CodeMirror / UI 等纯前端依赖塞进
//       Electron Node runtime，撑大 .next/standalone 和 DMG。
//
// 另外保留：
//   - .next/static → standalone/.next/static / public → standalone/public 拷贝
//   - dangling symlink 清理（pnpm 的 .pnpm/node_modules/<pkg> hoist 入口可能指向未被
//     standalone 拷贝的旧版本，例如 ../semver@6.3.1/...，而 standalone 只带了 7.8.1）。
//
// 详见 Spec 12 §6 / §7。

import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const standaloneDir = path.join(root, '.next', 'standalone')
const standaloneNodeModules = path.join(standaloneDir, 'node_modules')
const pnpmDir = path.join(root, 'node_modules', '.pnpm')

const SERVER_RUNTIME_PACKAGE_ALLOWLIST = [
  'next',
  'react',
  'react-dom',
  '@next/env',
  '@swc/helpers',
  'better-sqlite3',
  'drizzle-orm',
  'openai',
  '@anthropic-ai/sdk',
  '@anthropic-ai/claude-agent-sdk',
  '@openai/codex-sdk',
  '@openai/codex',
  '@modelcontextprotocol/sdk',
  'jszip',
  'nanoid',
  'zod',
  'pptxgenjs',
  'pdf-parse',
  'pdfjs-dist',
]

if (!fs.existsSync(standaloneDir)) {
  console.error('✗ No .next/standalone — 先跑 `next build`')
  process.exit(1)
}

// Next tracer 在遇到非常动态的 server import 时可能把 repo 级生成物也带进 standalone。
// Electron Builder 会递归签名 .app/.framework，旧 release 一旦被嵌进去就会导致 codesign 崩。
for (const relativePath of ['release', '.agenthub-data', 'apps']) {
  const target = path.join(standaloneDir, relativePath)
  if (!fs.existsSync(target)) continue
  fs.rmSync(target, { recursive: true, force: true })
  console.log(`✓ removed traced ${relativePath} from standalone`)
}

// ─── A: 静态资源 ─────────────────────────────────────────
function copyIfExists(src, dest, label) {
  if (!fs.existsSync(src)) return
  fs.cpSync(src, dest, { recursive: true, force: true })
  console.log(`✓ ${label}`)
}
copyIfExists(
  path.join(root, '.next', 'static'),
  path.join(standaloneDir, '.next', 'static'),
  'copied .next/static → standalone/.next/',
)
copyIfExists(
  path.join(root, 'public'),
  path.join(standaloneDir, 'public'),
  'copied public → standalone/',
)

// ─── C: 补齐 standalone/node_modules 漏拷 ─────────────────
const isWin = process.platform === 'win32'
const standaloneKey = isWin
  ? path.resolve(standaloneDir).toLowerCase()
  : path.resolve(standaloneDir)
function isWithinStandalone(absTarget) {
  const key = isWin ? path.resolve(absTarget).toLowerCase() : path.resolve(absTarget)
  return key === standaloneKey || key.startsWith(standaloneKey + path.sep)
}

function findInPnpm(pkgName) {
  if (!fs.existsSync(pnpmDir)) return null
  const dirs = fs.readdirSync(pnpmDir)
  // 优先返回 canonical 真目录（非 symlink）；peer-dep 变种里的 <pkg> 入口经常是 symlink，
  // 如果只拿到 symlink 作 fallback，下游 cpSync(dereference:true) 仍然能拿到正确内容
  let fallback = null
  for (const dir of dirs) {
    if (dir === 'node_modules') continue
    const candidate = path.join(pnpmDir, dir, 'node_modules', pkgName)
    if (!fs.existsSync(candidate)) continue
    try {
      const lstat = fs.lstatSync(candidate)
      if (lstat.isSymbolicLink()) {
        if (!fallback) fallback = candidate
        continue
      }
      if (lstat.isDirectory()) return candidate
    } catch {
      // ignore
    }
  }
  return fallback
}

function readDependencyEntries(pkgJsonPath) {
  try {
    const pkg = JSON.parse(fs.readFileSync(pkgJsonPath, 'utf8'))
    return [
      ...Object.keys(pkg.dependencies || {}).map((name) => ({ name, optional: false })),
      ...Object.keys(pkg.optionalDependencies || {}).map((name) => ({ name, optional: true })),
    ]
  } catch {
    return []
  }
}

function listTopLevelPackageNames(nodeModulesDir) {
  let entries
  try {
    entries = fs.readdirSync(nodeModulesDir, { withFileTypes: true })
  } catch {
    return []
  }

  const names = []
  for (const entry of entries) {
    if (entry.name === '.bin' || entry.name === '.pnpm') continue
    const p = path.join(nodeModulesDir, entry.name)
    if (entry.name.startsWith('@')) {
      let scoped
      try {
        scoped = fs.readdirSync(p, { withFileTypes: true })
      } catch {
        continue
      }
      for (const child of scoped) {
        names.push(`${entry.name}/${child.name}`)
      }
      continue
    }
    names.push(entry.name)
  }
  return names
}

function listExistingStandalonePackageJsons() {
  const packageJsons = []
  for (const name of listTopLevelPackageNames(standaloneNodeModules)) {
    packageJsons.push(path.join(standaloneNodeModules, ...name.split('/'), 'package.json'))
  }

  // Next may put hashed helper packages under .next/node_modules. They are not addressable by
  // package name, but their package.json dependency declarations still matter for runtime closure.
  const nextNodeModules = path.join(standaloneDir, '.next', 'node_modules')
  for (const name of listTopLevelPackageNames(nextNodeModules)) {
    packageJsons.push(path.join(nextNodeModules, ...name.split('/'), 'package.json'))
  }

  return packageJsons
}

// 拷完之后递归删除嵌套的 node_modules 目录（standalone 走平铺布局，nested deps 由我们的队列保证）。
// 走「先拷再删」不走 cpSync filter —— filter 只看路径串里有没有 'node_modules'，但 src 自己
// 就在 .pnpm/<pkg>@<ver>/node_modules/<pkg> 里，filter 会把 src 根本身拒掉，cpSync 返回成功
// 但什么都不拷。
function pruneNestedNodeModules(dir) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const p = path.join(dir, entry.name)
    if (entry.name === 'node_modules') {
      fs.rmSync(p, { recursive: true, force: true })
    } else {
      pruneNestedNodeModules(p)
    }
  }
}

function shouldSkipOptionalDependency(name) {
  if (name.startsWith('@openai/codex-')) {
    return !isCurrentPlatformPackage(name, '@openai/codex')
  }
  if (name.startsWith('@anthropic-ai/claude-agent-sdk-')) {
    return !isCurrentPlatformPackage(name, '@anthropic-ai/claude-agent-sdk')
  }
  if (name.startsWith('@napi-rs/canvas-')) {
    return !isCurrentPlatformPackage(name, '@napi-rs/canvas')
  }
  return false
}

function isCurrentPlatformPackage(name, baseName) {
  const suffix = name.slice(baseName.length + 1)
  const arch = process.arch === 'x64' ? 'x64' : process.arch === 'arm64' ? 'arm64' : process.arch
  const platform = process.platform

  if (platform === 'darwin') return suffix === `darwin-${arch}`
  if (platform === 'win32') return suffix === `win32-${arch}` || suffix === `win32-${arch}-msvc`
  if (platform === 'linux') {
    return (
      suffix === `linux-${arch}` ||
      suffix === `linux-${arch}-gnu` ||
      suffix === `linux-${arch}-musl`
    )
  }
  return false
}

function directorySize(dir) {
  let total = 0
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return 0
  }
  for (const entry of entries) {
    const p = path.join(dir, entry.name)
    try {
      if (entry.isSymbolicLink()) continue
      if (entry.isDirectory()) total += directorySize(p)
      else if (entry.isFile()) total += fs.statSync(p).size
    } catch {
      // ignore best-effort diagnostics
    }
  }
  return total
}

function printLargestAddedPackages(packages) {
  const largest = packages
    .filter((pkg) => pkg.bytes > 0)
    .sort((a, b) => b.bytes - a.bytes)
    .slice(0, 8)
  if (largest.length === 0) return
  console.log(
    `✓ deps: largest added package(s): ${largest
      .map((pkg) => `${pkg.name} ${formatBytes(pkg.bytes)}`)
      .join(', ')}`,
  )
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes}B`
  const kib = bytes / 1024
  if (kib < 1024) return `${kib.toFixed(1)}KB`
  const mib = kib / 1024
  if (mib < 1024) return `${mib.toFixed(1)}MB`
  return `${(mib / 1024).toFixed(1)}GB`
}

const seen = new Map()
const queue = []
const skippedPlatformOptional = new Set()
function enqueueDependency(entry) {
  if (entry.optional && shouldSkipOptionalDependency(entry.name)) {
    skippedPlatformOptional.add(entry.name)
    return
  }

  const existingOptional = seen.get(entry.name)
  if (existingOptional === undefined) {
    seen.set(entry.name, entry.optional)
    queue.push(entry)
    return
  }
  // If a dependency was first seen as optional and later as required, process it again
  // so missing required packages are not hidden by an earlier optional traversal.
  if (existingOptional && !entry.optional) {
    seen.set(entry.name, false)
    queue.push(entry)
  }
}

const tracedPackageNames = listTopLevelPackageNames(standaloneNodeModules)
for (const name of tracedPackageNames) {
  enqueueDependency({ name, optional: false })
}
for (const pkgJsonPath of listExistingStandalonePackageJsons()) {
  for (const entry of readDependencyEntries(pkgJsonPath)) {
    enqueueDependency(entry)
  }
}
for (const name of SERVER_RUNTIME_PACKAGE_ALLOWLIST) {
  enqueueDependency({ name, optional: false })
}

console.log(
  `✓ deps: seeded ${tracedPackageNames.length} traced package(s) + ${SERVER_RUNTIME_PACKAGE_ALLOWLIST.length} server runtime package(s)`,
)

let addedDeps = 0
let addedPackages = []
let unresolvedRequired = []
let unresolvedOptional = []
let cleanedBrokenSymlinks = 0
while (queue.length > 0) {
  const { name } = queue.shift()
  const optional = seen.get(name) ?? false
  const dest = path.join(standaloneNodeModules, name)
  // existsSync 在 Windows 上对某些 next-build 留下的 SYMLINKD 会返回 false（即便 target 真存在），
  // 因为 Node 跟随 symlink 时 statSync 报 EPERM。直接 statSync 兜底，并把这种「不可跟随的 symlink」
  // 当 broken 处理，删掉之后下面再 cpSync 拷真内容。
  let destOk = false
  let destIsBroken = false
  try {
    fs.statSync(dest)
    destOk = true
  } catch {
    try {
      if (fs.lstatSync(dest).isSymbolicLink()) destIsBroken = true
    } catch {
      // 真不存在
    }
  }
  if (destOk) {
    for (const childDep of readDependencyEntries(path.join(dest, 'package.json'))) {
      enqueueDependency(childDep)
    }
    continue
  }
  if (destIsBroken) {
    try {
      fs.unlinkSync(dest)
      cleanedBrokenSymlinks++
    } catch {
      // ignore
    }
  }
  const src = findInPnpm(name)
  if (!src) {
    if (optional) unresolvedOptional.push(name)
    else unresolvedRequired.push(name)
    continue
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true })
  // dereference: 跟到真内容，避免 peer-dep 变种里的 <pkg> 是 symlink 时被原样拷成绝对 symlink
  fs.cpSync(src, dest, { recursive: true, dereference: true })
  // 拷完后剪掉 nested node_modules（pnpm 的依赖通过 symlink 接通，平铺布局下这些都是废链接）
  pruneNestedNodeModules(dest)
  addedPackages.push({ name, bytes: directorySize(dest) })
  addedDeps++
  for (const childDep of readDependencyEntries(path.join(src, 'package.json'))) {
    enqueueDependency(childDep)
  }
}
console.log(
  `✓ deps: added ${addedDeps} missing package(s) to standalone/node_modules` +
    (cleanedBrokenSymlinks > 0 ? ` (cleaned ${cleanedBrokenSymlinks} unfollowable symlink(s) en route)` : ''),
)
printLargestAddedPackages(addedPackages)
if (unresolvedRequired.length > 0) {
  console.warn(
    `  ! missing required package(s) in .pnpm (${unresolvedRequired.length}): ${unresolvedRequired.slice(0, 5).join(', ')}${unresolvedRequired.length > 5 ? ' ...' : ''}`,
  )
}
const optionalOnly = unresolvedOptional.filter((name) => !unresolvedRequired.includes(name))
if (optionalOnly.length > 0) {
  console.log(
    `✓ optional deps: skipped ${optionalOnly.length} platform-specific package(s) not installed for ${process.platform}/${process.arch}` +
      ` (${optionalOnly.slice(0, 5).join(', ')}${optionalOnly.length > 5 ? ' ...' : ''})`,
  )
}
if (skippedPlatformOptional.size > 0) {
  const names = Array.from(skippedPlatformOptional)
  console.log(
    `✓ optional deps: ignored ${names.length} non-current platform package(s) while seeding` +
      ` (${names.slice(0, 5).join(', ')}${names.length > 5 ? ' ...' : ''})`,
  )
}

// @openai/codex 的平台 runtime 是 npm alias 包：
//   @openai/codex-darwin-arm64 -> npm:@openai/codex@0.136.0-darwin-arm64
// Next standalone 可能已经 trace 进 `.pnpm/@openai+codex@...-darwin-arm64`，
// 下面补齐逻辑又会把 alias 包拷到顶层 `node_modules/@openai/codex-darwin-arm64`。
// 运行时 SDK 通过顶层 alias 包解析 binary，.pnpm 那份会变成 190MB+ 的重复 vendor。
function removeDuplicatedCodexRuntimeStores() {
  const codexPackageJson = path.join(standaloneNodeModules, '@openai', 'codex', 'package.json')
  if (!fs.existsSync(codexPackageJson)) return 0

  let optionalDependencies
  try {
    optionalDependencies = JSON.parse(fs.readFileSync(codexPackageJson, 'utf8')).optionalDependencies
  } catch {
    return 0
  }
  if (!optionalDependencies || typeof optionalDependencies !== 'object') return 0

  let removed = 0
  for (const [aliasName, targetSpec] of Object.entries(optionalDependencies)) {
    if (typeof targetSpec !== 'string') continue
    if (!fs.existsSync(path.join(standaloneNodeModules, ...aliasName.split('/')))) continue

    const match = /^npm:(@[^/]+)\/([^@]+)@(.+)$/.exec(targetSpec)
    if (!match) continue
    const [, scope, name, version] = match
    const storeDir = path.join(standaloneNodeModules, '.pnpm', `${scope}+${name}@${version}`)
    if (!fs.existsSync(storeDir)) continue

    fs.rmSync(storeDir, { recursive: true, force: true })
    removed++
  }
  return removed
}

const removedCodexRuntimeStores = removeDuplicatedCodexRuntimeStores()
if (removedCodexRuntimeStores > 0) {
  console.log(`✓ dedupe: removed ${removedCodexRuntimeStores} duplicate Codex runtime store package(s)`)
}

// ─── D: 走查 symlinks（dep-copy 之后再做，防止过程中又混入 symlink）────
let droppedDangling = 0
let materialized = 0
function walk(dir) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    const p = path.join(dir, entry.name)
    if (entry.isSymbolicLink()) {
      // (a) dangling → 删
      try {
        fs.statSync(p)
      } catch {
        try {
          fs.unlinkSync(p)
          droppedDangling++
        } catch {
          // ignore
        }
        continue
      }
      // (b) 绝对 / 解析后落在 standalone 外 → 物化成真目录
      try {
        const target = fs.readlinkSync(p)
        const resolved = path.isAbsolute(target)
          ? target
          : path.resolve(path.dirname(p), target)
        if (!isWithinStandalone(resolved)) {
          fs.unlinkSync(p)
          fs.cpSync(resolved, p, { recursive: true, dereference: true })
          materialized++
        }
      } catch {
        // readlink / cp 失败保守跳过；runtime 真用到时再炸
      }
    } else if (entry.isDirectory()) {
      walk(p)
    }
  }
}
walk(standaloneDir)
console.log(
  `✓ symlinks: dropped ${droppedDangling} dangling, materialized ${materialized} out-of-tree`,
)
