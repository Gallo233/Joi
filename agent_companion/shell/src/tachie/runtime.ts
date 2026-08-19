/**
 * Tachie (立绘) characters: authored artwork, one image per mood.
 *
 * This is the format for a character who was drawn rather than rigged. It is
 * deliberately not the same thing as the existing `static` fallback: that shows
 * one sprite when a model fails to load, while a tachie package is a real
 * choice with a picture per emotion, breathing, and a mouth that opens when
 * audio plays.
 *
 * Which image belongs to which mood comes from the package's own mapping, so a
 * pack that only ships `neutral` still works -- every mood falls back to the
 * base image rather than to nothing.
 */

import { emotionWeight } from '../characterExpression'
import type { StageController, StageEmotion, StageRuntimeMapping } from '../character/stage'
import { motionEnvelope, motionExpired, resolveCharacterMotion, type CharacterMotionRequest, type ResolvedCharacterMotion } from '../characterMotion'
import { mouthSignal, voiceDrivenMouthLevel } from '../voiceLipSync'

/** How much of the canvas height the artwork fills before zoom. */
const ART_FRACTION = 0.94

/**
 * Resolve `emotion -> image URL` from the package mapping.
 *
 * A tachie package reuses the expression mapping every other format uses. Core
 * publishes each mood's artwork as `image_url`, because only Core knows where
 * the package lives; a row carrying just a relative `image` is resolved against
 * the base image so a hand-written mapping still works.
 */
function resolveImageSources(baseUrl: string, mapping: StageRuntimeMapping): Map<StageEmotion, string> {
  const sources = new Map<StageEmotion, string>()
  for (const row of mapping.expressions || []) {
    const emotion = String(row?.emotion || '').trim() as StageEmotion
    const url = String(row?.image_url || '').trim()
    if (emotion && url) {
      sources.set(emotion, url)
      continue
    }
    const file = String(row?.image || '').trim()
    if (!emotion || !file) continue
    try {
      sources.set(emotion, new URL(file, baseUrl).href)
    } catch {
      /* a name that is not resolvable leaves this mood on the base image */
    }
  }
  return sources
}

export async function mountTachie(
  canvas: HTMLCanvasElement,
  modelUrl: string,
  mapping: StageRuntimeMapping = {},
): Promise<StageController> {
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Tachie stage needs a 2D canvas context')

  const sources = resolveImageSources(modelUrl, mapping)
  const cache = new Map<string, HTMLImageElement>()

  const load = (url: string) =>
    new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image()
      image.decoding = 'async'
      image.onload = () => resolve(image)
      image.onerror = () => reject(new Error('tachie_image_failed'))
      image.src = url
    })

  const base = await load(modelUrl)
  cache.set(modelUrl, base)

  let emotion: StageEmotion = 'neutral'
  let emotionStartedAt = 0
  let current = base
  let compact = false
  let zoom = 1
  let motion: ResolvedCharacterMotion | null = null
  let motionStartedAt = 0
  let frame = 0
  let disposed = false

  const selectImage = (value: StageEmotion) => {
    const url = sources.get(value)
    if (!url) {
      current = base
      return
    }
    const cached = cache.get(url)
    if (cached) {
      current = cached
      return
    }
    // Load in the background and swap when it arrives; a mood must never block
    // a frame, and a missing file leaves the base art in place.
    void load(url)
      .then((image) => {
        if (disposed) return
        cache.set(url, image)
        if (emotion === value) current = image
      })
      .catch(() => undefined)
  }

  const draw = (now: number) => {
    frame = requestAnimationFrame(draw)
    const width = Math.max(canvas.clientWidth || canvas.width || 1, 1)
    const height = Math.max(canvas.clientHeight || canvas.height || 1, 1)
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio)
      canvas.height = Math.round(height * ratio)
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, width, height)

    const settled = emotionWeight(emotionStartedAt, now)
    // Breathing: a slow vertical drift plus a fraction of a percent of scale.
    // Enough that the artwork is not a frozen picture, small enough that it
    // never reads as bobbing.
    const breath = Math.sin(now / 2600)
    const voice = voiceDrivenMouthLevel(mouthSignal(now))
    let offsetX = 0
    let offsetY = breath * height * 0.004
    let tilt = 0

    if (motion) {
      if (motionExpired(motion, motionStartedAt, now)) {
        motion = null
      } else {
        const envelope = motionEnvelope(motion, motionStartedAt, now)
        const swing = Math.sin((now - motionStartedAt) / 200) * envelope * motion.intensity
        offsetX = swing * width * 0.02
        offsetY += Math.abs(swing) * height * 0.012
        tilt = swing * 0.05
      }
    }

    // Audio nudges the artwork very slightly, so a speaking character has some
    // life without a mouth layer to animate. It is scaled by the settled
    // emotion weight so a mood change does not jump.
    offsetY -= voice * height * 0.004 * (0.6 + settled * 0.4)

    const scale =
      ((compact ? height * 0.72 : height * ART_FRACTION) / Math.max(current.naturalHeight || current.height || 1, 1)) *
      Math.max(zoom, 0.2)
    const drawWidth = (current.naturalWidth || current.width || 1) * scale
    const drawHeight = (current.naturalHeight || current.height || 1) * scale

    context.save()
    context.translate(width / 2 + offsetX, height - offsetY)
    context.rotate(tilt)
    context.drawImage(current, -drawWidth / 2, -drawHeight, drawWidth, drawHeight)
    context.restore()
  }

  const resize = () => {
    // The draw loop measures the canvas every frame, so a resize needs no work
    // beyond letting the next frame run.
  }

  frame = requestAnimationFrame(draw)

  return {
    resize,
    setCompact(next) {
      compact = next
    },
    setZoom(next) {
      zoom = Number.isFinite(next) && next > 0 ? next : 1
    },
    setEmotion(value) {
      if (value === emotion) return
      emotion = value
      emotionStartedAt = performance.now()
      selectImage(value)
    },
    playMotion(request: CharacterMotionRequest) {
      const resolved = resolveCharacterMotion(request, mapping.motions)
      if (!resolved) return
      motion = resolved
      motionStartedAt = performance.now()
    },
    destroy() {
      disposed = true
      cancelAnimationFrame(frame)
      cache.clear()
    },
  }
}
