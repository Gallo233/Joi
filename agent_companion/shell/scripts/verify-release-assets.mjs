import { createHash } from 'node:crypto'
import { readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const shellRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifestPath = join(shellRoot, 'release-assets.json')
const publicRoot = join(shellRoot, 'public')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))

if (manifest.version !== 1 || !Array.isArray(manifest.files) || manifest.files.length === 0) {
  throw new Error('release-assets.json is invalid or empty')
}

const failures = []
for (const item of manifest.files) {
  const path = join(publicRoot, item.path)
  try {
    const size = statSync(path).size
    const sha256 = createHash('sha256').update(readFileSync(path)).digest('hex')
    if (size !== item.size || sha256 !== item.sha256) {
      failures.push(`${item.path}: expected ${item.size}/${item.sha256}, received ${size}/${sha256}`)
    }
  } catch (error) {
    failures.push(`${item.path}: ${error instanceof Error ? error.message : String(error)}`)
  }
}

if (failures.length) {
  throw new Error(`Joi release asset verification failed:\n${failures.join('\n')}`)
}

console.log(`Verified ${manifest.files.length} pinned Joi release assets.`)
