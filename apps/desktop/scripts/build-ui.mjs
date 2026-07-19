/**
 * Copy full AChat static UI into Tauri + engine resource dirs.
 *
 * Primary output: **src-tauri/resources/ui** (Tauri bundle.resources).
 * Mirror: apps/desktop/resources/ui (docs / engine discovery).
 *
 * Source priority:
 *  1. ACHAT_UI_SOURCE
 *  2. repo `out/` (from `pnpm desktop:build-ui` / Next static export)
 *  3. repo `dist/`
 *  4. apps/desktop/ui placeholder — ONLY if ACHAT_UI_ALLOW_PLACEHOLDER=1
 *
 * Usage:
 *   # product (recommended)
 *   pnpm desktop:build-ui          # at repo root → fills out/
 *   cd apps/desktop && pnpm build:ui
 *
 *   # explicit
 *   ACHAT_UI_SOURCE=../../out node ./scripts/build-ui.mjs
 */
import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopRoot = resolve(__dirname, '..')
const repoRoot = resolve(desktopRoot, '../..')
const outDir = join(desktopRoot, 'src-tauri', 'resources', 'ui')
const mirrorDir = join(desktopRoot, 'resources', 'ui')
const fallbackUi = join(desktopRoot, 'ui')
const allowPlaceholder = process.env.ACHAT_UI_ALLOW_PLACEHOLDER === '1'

function hasFiles(p) {
  try {
    return existsSync(p) && readdirSync(p).length > 0
  } catch {
    return false
  }
}

function looksLikePlaceholder(dir) {
  try {
    const html = readFileSync(join(dir, 'index.html'), 'utf8')
    return (
      html.includes('引擎占位页') ||
      html.includes('不是完整聊天 UI') ||
      html.includes('UI assets incomplete')
    )
  } catch {
    return true
  }
}

function looksLikeFullApp(dir) {
  if (!existsSync(join(dir, 'index.html'))) return false
  if (looksLikePlaceholder(dir)) return false
  // Next export usually has _next; custom trees may not — index without placeholder is ok
  return true
}

const envSource = process.env.ACHAT_UI_SOURCE?.trim()
const candidates = [
  envSource || null,
  join(repoRoot, 'out'),
  join(repoRoot, 'dist'),
  allowPlaceholder ? fallbackUi : null,
  allowPlaceholder ? join(repoRoot, 'public') : null,
].filter(Boolean)

const source = candidates.find((p) => hasFiles(p)) || null

if (!source) {
  console.error('[desktop-ui] No full static UI found.')
  console.error('[desktop-ui] Build the product UI first from repo root:')
  console.error('    pnpm desktop:build-ui')
  console.error('  then re-run:  pnpm build:ui   (in apps/desktop)')
  console.error('[desktop-ui] For internal placeholder-only builds set ACHAT_UI_ALLOW_PLACEHOLDER=1')
  process.exit(1)
}

if (!looksLikeFullApp(source) && !allowPlaceholder) {
  console.error(`[desktop-ui] Source looks like placeholder/incomplete UI: ${source}`)
  console.error('[desktop-ui] Run from repo root:  pnpm desktop:build-ui')
  console.error('[desktop-ui] Or set ACHAT_UI_ALLOW_PLACEHOLDER=1 for engine smoke only')
  process.exit(1)
}

if (looksLikePlaceholder(source)) {
  console.warn('[desktop-ui] WARNING: packaging PLACEHOLDER UI (not full AChat chat UI)')
}

function copyDir(src, dest) {
  mkdirSync(dest, { recursive: true })
  cpSync(src, dest, { recursive: true })
}

function ensureIndex(dir) {
  if (existsSync(join(dir, 'index.html'))) return
  if (existsSync(join(fallbackUi, 'index.html'))) {
    cpSync(join(fallbackUi, 'index.html'), join(dir, 'index.html'))
    return
  }
  writeFileSync(
    join(dir, 'index.html'),
    `<!doctype html><html><body><h1>AChat</h1><p>UI assets incomplete</p></body></html>\n`,
    'utf8',
  )
}

function writeMarker(dir) {
  writeFileSync(
    join(dir, '.achat-ui-build.json'),
    JSON.stringify(
      {
        builtAt: new Date().toISOString(),
        source,
        fullApp: looksLikeFullApp(source),
        placeholder: looksLikePlaceholder(source),
        note: 'Desktop static UI tree for local engine mount + Tauri resources',
      },
      null,
      2,
    ) + '\n',
    'utf8',
  )
}

function materialize(dest) {
  if (existsSync(dest)) {
    rmSync(dest, { recursive: true, force: true })
  }
  mkdirSync(dest, { recursive: true })
  copyDir(source, dest)
  ensureIndex(dest)
  writeMarker(dest)
}

console.log(`[desktop-ui] source=${source}`)
console.log(`[desktop-ui] fullApp=${looksLikeFullApp(source)}`)
console.log(`[desktop-ui] out=${outDir}`)
materialize(outDir)
console.log(`[desktop-ui] mirror=${mirrorDir}`)
materialize(mirrorDir)
console.log('[desktop-ui] done')
