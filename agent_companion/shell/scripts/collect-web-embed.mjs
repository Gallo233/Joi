/**
 * Put the host-page embed next to the shell it embeds.
 *
 * `joi-embed.js` and the shell are one contract -- the postMessage types have
 * to match -- so shipping them from two places is how they drift. The build
 * that produces the shell also produces its embed, and a deployment is one
 * directory copy.
 *
 * The shell is built with Vite's default relative `base`, so this directory
 * works unchanged at `/joi-shell/`, at `/joi-doorway/joi-shell/`, or anywhere
 * else a host happens to mount it. Do not pin an absolute base here.
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const shellRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const source = join(dirname(shellRoot), 'web', 'static')
const target = join(shellRoot, 'dist')

if (!existsSync(target)) {
  console.error('dist/ does not exist; run the build first')
  process.exit(1)
}

mkdirSync(target, { recursive: true })
const copied = []
for (const name of readdirSync(source)) {
  if (!/\.(?:js|css)$/.test(name)) continue
  copyFileSync(join(source, name), join(target, name))
  copied.push(name)
}
console.log(`collected web embed: ${copied.join(', ')}`)
