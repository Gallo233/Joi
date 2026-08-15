import { chmodSync, copyFileSync, cpSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const nodeMajor = Number.parseInt(process.versions.node.split('.')[0], 10)
if (!Number.isInteger(nodeMajor) || nodeMajor < 22) {
  throw new Error('Minecraft bridge packaging requires the reviewed Node.js 22+ runtime')
}
const source = join(root, 'node_modules', 'minecraft-data')
const destination = join(root, 'dist', 'vendor', 'minecraft-data')
const manifest = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8'))
if (manifest.name !== 'minecraft-data' || manifest.version !== '3.113.1' || Object.keys(manifest.dependencies || {}).length) {
  throw new Error('minecraft-data runtime does not match the reviewed lock')
}
rmSync(destination, { recursive: true, force: true })
mkdirSync(dirname(destination), { recursive: true })
cpSync(source, destination, { recursive: true, dereference: false })
const runtimeDir = join(root, 'dist', 'runtime')
mkdirSync(runtimeDir, { recursive: true })
const nodeName = process.platform === 'win32' ? 'node.exe' : 'node'
const nodeDestination = join(runtimeDir, nodeName)
copyFileSync(process.execPath, nodeDestination)
if (process.platform !== 'win32') chmodSync(nodeDestination, 0o755)
console.log(`Staged reviewed minecraft-data ${manifest.version} and Node ${process.versions.node}`)
