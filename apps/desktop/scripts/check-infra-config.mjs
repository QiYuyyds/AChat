/** Validate infra.default.json / official.json shape for packaging. */
import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..')
const candidates = [
  join(root, 'src-tauri', 'infra.default.json'),
  join(root, 'src-tauri', 'official.json'),
  join(root, 'configs', 'infra.default.dev.json'),
]

let ok = 0
for (const file of candidates) {
  if (!existsSync(file)) {
    console.warn(`[check-infra] skip missing ${file}`)
    continue
  }
  const raw = readFileSync(file, 'utf8')
  const data = JSON.parse(raw)
  if (typeof data !== 'object' || data === null) {
    console.error(`[check-infra] not an object: ${file}`)
    process.exit(1)
  }
  // Prefer infra block; legacy webUrl/apiUrl alone is no longer sufficient
  const hasInfra = data.infra && typeof data.infra === 'object'
  const hasLegacy = data.webUrl || data.apiUrl
  if (!hasInfra && !hasLegacy) {
    console.error(`[check-infra] missing infra or legacy urls: ${file}`)
    process.exit(1)
  }
  console.log(`[check-infra] ok ${file}`)
  ok += 1
}

if (ok === 0) {
  console.error('[check-infra] no config files found')
  process.exit(1)
}
