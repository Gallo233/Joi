/**
 * A model's thumbnail, drawn by the renderer that would draw the model.
 *
 * A character library that lists file names is a list of file names. Packages
 * that ship an authored portrait already have one; the rest had nothing to show,
 * which is exactly the packages a user is least sure about. Every format already
 * mounts onto a canvas through one interface, so a thumbnail is that same mount,
 * offscreen, held for a frame, read back as a data URL, and torn down.
 *
 * Deliberately not a screenshot of the live stage: the library must be able to
 * preview a model that is not the active character.
 */

import { canRenderThumbnail, type StageModelFormat, type StageRuntimeMapping } from './stage'

export interface ThumbnailOptions {
  /** Square edge in CSS pixels. */
  size?: number
  /** How long to let the renderer settle before reading the canvas. */
  settleMs?: number
  /** Give up rather than hold a WebGL context open on a model that will not load. */
  timeoutMs?: number
}

const DEFAULT_SIZE = 192
const DEFAULT_SETTLE_MS = 220
const DEFAULT_TIMEOUT_MS = 8000

/**
 * Render one frame of a character model and return it as a PNG data URL.
 *
 * Returns "" rather than throwing when the model cannot be drawn: a missing
 * thumbnail is a cosmetic outcome, and the import report is where a broken
 * package gets explained.
 */
export async function renderModelThumbnail(
  format: StageModelFormat,
  modelUrl: string,
  mapping: StageRuntimeMapping = {},
  options: ThumbnailOptions = {},
): Promise<string> {
  if (!canRenderThumbnail(format, modelUrl)) return ''

  const size = Math.max(32, Math.min(Math.round(options.size ?? DEFAULT_SIZE), 512))
  const settleMs = Math.max(0, Math.min(options.settleMs ?? DEFAULT_SETTLE_MS, 2000))
  const timeoutMs = Math.max(500, Math.min(options.timeoutMs ?? DEFAULT_TIMEOUT_MS, 30000))

  const canvas = document.createElement('canvas')
  // Offscreen but laid out: a renderer measures clientWidth/clientHeight to
  // frame the model, and both are zero for a canvas that was never in the DOM.
  canvas.width = size
  canvas.height = size
  canvas.style.position = 'fixed'
  canvas.style.left = '-10000px'
  canvas.style.top = '0'
  canvas.style.width = `${size}px`
  canvas.style.height = `${size}px`
  canvas.style.pointerEvents = 'none'
  document.body.appendChild(canvas)

  let controller: { destroy: () => void; resize: () => void; setCompact: (compact: boolean) => void } | null = null
  try {
    controller = await withTimeout(mountForThumbnail(format, canvas, modelUrl, mapping), timeoutMs)
    // Bust framing: a library tile is small, and a full body in 192 pixels is a
    // silhouette.
    controller.setCompact(true)
    controller.resize()
    await settle(settleMs)
    return canvas.toDataURL('image/png')
  } catch {
    return ''
  } finally {
    try {
      controller?.destroy()
    } catch {
      /* a renderer that failed to mount has nothing to tear down */
    }
    canvas.remove()
  }
}

async function mountForThumbnail(
  format: StageModelFormat,
  canvas: HTMLCanvasElement,
  modelUrl: string,
  mapping: StageRuntimeMapping,
) {
  switch (format) {
    case 'procedural3d':
      return (await import('../character3d/runtime')).mountProceduralCharacter3D(canvas, mapping)
    case 'vrm':
      return (await import('../vrm/runtime')).mountVRM(canvas, modelUrl, mapping)
    case 'mmd':
      return (await import('../mmd/runtime')).mountMMD(canvas, modelUrl, mapping)
    case 'tachie':
      return (await import('../tachie/runtime')).mountTachie(canvas, modelUrl, mapping)
    case 'static':
      return (await import('../tachie/runtime')).mountTachie(canvas, modelUrl, mapping)
    default:
      return (await import('../live2d/runtime')).mountLive2D(canvas, modelUrl, mapping)
  }
}

function settle(ms: number) {
  return new Promise<void>((resolve) => {
    // Two frames plus the delay: the first frame mounts, the second draws.
    requestAnimationFrame(() => requestAnimationFrame(() => window.setTimeout(resolve, ms)))
  })
}

function withTimeout<T>(work: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('thumbnail_timeout')), ms)
    work.then(
      (value) => {
        window.clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        window.clearTimeout(timer)
        reject(error)
      },
    )
  })
}
