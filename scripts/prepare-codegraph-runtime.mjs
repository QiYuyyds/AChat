import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { pipeline } from 'node:stream/promises'
import { Readable } from 'node:stream'

const root = process.cwd()
const manifestPath = path.join(
  root,
  'backend',
  'app',
  'code_intelligence',
  'runtime-manifest.json',
)
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
const platformKey = `${process.platform}-${process.arch}`
const artifact = manifest.artifacts[platformKey]

if (!artifact) {
  throw new Error(`No pinned CodeGraph runtime for ${platformKey}`)
}

const archiveName = path.basename(new URL(artifact.url).pathname)
const outputDir = path.join(root, 'resources', 'codegraph')
const outputPath = path.join(outputDir, archiveName)

function sha256(filePath) {
  const hash = crypto.createHash('sha256')
  const data = fs.readFileSync(filePath)
  hash.update(data)
  return hash.digest('hex')
}

if (fs.existsSync(outputPath) && sha256(outputPath) === artifact.sha256) {
  console.log(`✓ CodeGraph ${manifest.version} runtime ready: ${archiveName}`)
  process.exit(0)
}

fs.mkdirSync(outputDir, { recursive: true })
const partialPath = `${outputPath}.partial-${process.pid}`
try {
  const response = await fetch(artifact.url, { redirect: 'follow' })
  if (!response.ok || !response.body) {
    throw new Error(`CodeGraph runtime download failed: HTTP ${response.status}`)
  }
  await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(partialPath))
  const actual = sha256(partialPath)
  if (actual !== artifact.sha256) {
    throw new Error(
      `CodeGraph runtime SHA256 mismatch: expected ${artifact.sha256}, got ${actual}`,
    )
  }
  fs.renameSync(partialPath, outputPath)
  console.log(`✓ CodeGraph ${manifest.version} runtime downloaded: ${archiveName}`)
} finally {
  fs.rmSync(partialPath, { force: true })
}
