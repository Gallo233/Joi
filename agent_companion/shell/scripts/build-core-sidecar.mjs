import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const shellRoot = resolve(import.meta.dirname, '..')
const workspace = resolve(shellRoot, '..', '..')
const candidates = [
  process.env.JOI_PYTHON,
  join(workspace, '.venv', 'bin', 'python'),
  join(workspace, '.venv', 'Scripts', 'python.exe'),
  'python3',
  'python',
].filter(Boolean)

for (const python of candidates) {
  if (python.includes('/') && !existsSync(python)) continue
  const result = spawnSync(
    python,
    [join(workspace, 'tools', 'build_core_sidecar.py'), '--workspace', workspace, ...process.argv.slice(2)],
    { cwd: workspace, stdio: 'inherit' },
  )
  if (result.error?.code === 'ENOENT') continue
  process.exit(result.status ?? 1)
}

console.error('No Python runtime was found for building the Joi Core sidecar.')
process.exit(1)
