import fs from 'node:fs'
import path from 'node:path'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type Plugin } from 'vite'

/**
 * Serve local VRM models and .vrma clips to vrm-lab.html during development.
 *
 * These files are large and licensed to whoever downloaded them, so they must
 * not sit in `public/`: everything there is copied verbatim into `dist/` and
 * would ship inside the app bundle. This route exists only in the dev server.
 */
function vrmLabAssets(): Plugin {
  const root = path.resolve(__dirname, '../../.vrm-lab-assets')
  return {
    name: 'joi-vrm-lab-assets',
    apply: 'serve',
    configureServer(server) {
      const TYPES: Record<string, string> = {
        '.json': 'application/json',
        '.png': 'image/png',
        '.moc3': 'application/octet-stream',
      }
      server.middlewares.use('/vrm-lab', (request, response, next) => {
        // Live2D models load textures and motions from subdirectories, so
        // nested paths are served -- but only after resolving proves they are
        // still inside the lab directory.
        const requested = decodeURIComponent((request.url || '').split('?')[0]).replace(/^\/+/, '')
        if (!requested) return next()
        const file = path.resolve(root, requested)
        if (file !== root && !file.startsWith(root + path.sep)) return next()
        if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return next()
        response.setHeader('Content-Type', TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream')
        fs.createReadStream(file).pipe(response)
      })
    },
  }
}

/**
 * Save a layout digest straight from the page to `tests/baseline/`.
 *
 * The refactor's zero-render-change stages are only checkable against a
 * "before" capture, and a capture is only useful if taking one is free --
 * anything that requires copying 200 lines out of a console by hand gets
 * skipped exactly when it matters. Dev server only, like the lab assets route
 * above: nothing here exists in a build.
 */
function layoutDigest(): Plugin {
  const root = path.resolve(__dirname, 'tests/baseline')
  return {
    name: 'joi-layout-digest',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__digest', (request, response) => {
        const name = new URL(request.url || '', 'http://localhost').searchParams.get('name') || ''
        // The name becomes a filename, so it may not climb out of the directory.
        if (!/^[\w.@-]+$/.test(name)) {
          response.statusCode = 400
          return response.end('bad name')
        }
        const chunks: Buffer[] = []
        request.on('data', (chunk) => chunks.push(chunk))
        request.on('end', () => {
          fs.mkdirSync(root, { recursive: true })
          fs.writeFileSync(path.join(root, `${name}.digest.txt`), Buffer.concat(chunks))
          response.end('saved')
        })
      })
    },
  }
}

/**
 * The version and identifier the About panel shows come from the same file the
 * packaged app is built from, so the two cannot drift: `packaging_smoke`
 * already fails when tauri.conf.json, both package.json files and Cargo.toml
 * disagree, and reading any other copy here would put a fifth one in play.
 */
const tauriConfig = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, 'src-tauri/tauri.conf.json'), 'utf8'),
) as { version: string; identifier: string }

export default defineConfig({
  // Tauri needs relative assets; the website build is intentionally mounted
  // at one stable path and opts in through `npm run build:web`.
  base: process.env.JOI_SHELL_BASE || './',
  define: {
    __JOI_VERSION__: JSON.stringify(tauriConfig.version),
    __JOI_BUNDLE_ID__: JSON.stringify(tauriConfig.identifier),
  },
  plugins: [vue(), vrmLabAssets(), layoutDigest()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
  },
})
