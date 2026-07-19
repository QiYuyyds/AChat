/**
 * Build the full AChat frontend as static files for the desktop installer.
 *
 * Output: repo `out/` (Next `output: 'export'`).
 * Then run `apps/desktop` `pnpm build:ui` to copy into Tauri resources.
 *
 * Usage (repo root):
 *   node scripts/build-desktop-static-ui.mjs
 *   pnpm desktop:build-ui
 */
import { existsSync, rmSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const repoRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))
const outDir = join(repoRoot, 'out')
const nextBin = join(repoRoot, 'node_modules', 'next', 'dist', 'bin', 'next')

function fail(msg) {
  console.error(`[desktop-static-ui] ${msg}`)
  process.exit(1)
}

if (!existsSync(nextBin)) {
  fail(`Next not found at ${nextBin}. Run pnpm install at repo root first.`)
}

console.log('[desktop-static-ui] ACHAT_DESKTOP_STATIC=1 next build (static export)')
console.log(`[desktop-static-ui] root=${repoRoot}`)

// Clean previous export so we never ship a stale mix
if (existsSync(outDir)) {
  rmSync(outDir, { recursive: true, force: true })
}

const env = {
  ...process.env,
  ACHAT_DESKTOP_STATIC: '1',
  // Desktop UI talks to local engine via window.achatDesktop; no fixed remote API base.
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL ?? '',
}

const result = spawnSync(process.execPath, [nextBin, 'build'], {
  cwd: repoRoot,
  env,
  stdio: 'inherit',
  shell: false,
})

if (result.status !== 0) {
  fail(`next build failed with exit ${result.status ?? 'unknown'}`)
}

const indexHtml = join(outDir, 'index.html')
if (!existsSync(indexHtml)) {
  fail(`expected ${indexHtml} after export — static export did not produce out/`)
}

// Placeholder page from apps/desktop/ui contains this marker; full app must not.
const sample = readFileSync(indexHtml, 'utf8')
if (sample.includes('引擎占位页') || sample.includes('不是完整聊天 UI')) {
  fail('out/index.html still looks like the desktop placeholder — export source is wrong')
}

console.log('[desktop-static-ui] OK')
console.log(`[desktop-static-ui] static tree: ${outDir}`)
console.log('[desktop-static-ui] next: cd apps/desktop && pnpm build:ui && pnpm build')
