import { copyFileSync, existsSync, mkdirSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const shellRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const targetRoot = join(shellRoot, 'public')
const optional = process.argv.includes('--optional')
const sourceCandidates = [
  process.env.JOI_LIVE2D_SOURCE,
  join(homedir(), 'Documents', 'All Joi', 'public'),
].filter(Boolean)

const requiredFiles = [
  'live2d/joi/joi.model3.json',
  'live2d/joi/joi.moc3',
  'live2d/joi/joi.cdi3.json',
  'live2d/joi/joi.2048/texture_00.png',
  'vendor/live2d/live2dcubismcore.min.js',
  'vendor/live2d/pixi.min.js',
  'vendor/live2d/cubism.min.js',
]

const sourceRoot = sourceCandidates.find((candidate) =>
  requiredFiles.every((relativePath) => existsSync(join(candidate, relativePath))),
)

if (!sourceRoot) {
  const message = 'Joi Live2D assets were not found. Set JOI_LIVE2D_SOURCE to a public directory containing live2d/joi and vendor/live2d.'
  if (optional) {
    console.warn(`${message} The shell will use its static character fallback.`)
    process.exit(0)
  }
  throw new Error(message)
}

let copied = 0
for (const relativePath of requiredFiles) {
  const source = join(sourceRoot, relativePath)
  const target = join(targetRoot, relativePath)
  const targetExists = existsSync(target)
  const sameSize = targetExists && statSync(source).size === statSync(target).size
  if (sameSize) continue
  mkdirSync(dirname(target), { recursive: true })
  copyFileSync(source, target)
  copied += 1
}

console.log(copied ? `Synced ${copied} Joi Live2D asset files.` : 'Joi Live2D assets are already in sync.')
