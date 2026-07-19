import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const required = ['webUrl', 'apiUrl', 'allowedOrigins']
const files = [
  'configs/official.dev.json',
  'configs/official.staging.json',
  'configs/official.prod.json',
  'src-tauri/official.json',
]

let failed = false
for (const rel of files) {
  const path = join(root, rel)
  const data = JSON.parse(readFileSync(path, 'utf8'))
  for (const key of required) {
    if (!(key in data)) {
      console.error(`[official-config] ${rel} missing ${key}`)
      failed = true
    }
  }
  if (!Array.isArray(data.allowedOrigins) || data.allowedOrigins.length === 0) {
    console.error(`[official-config] ${rel} allowedOrigins must be a non-empty array`)
    failed = true
  }
  console.log(`[official-config] ok ${rel} flavor=${data.flavor ?? 'n/a'}`)
}

if (failed) process.exit(1)
