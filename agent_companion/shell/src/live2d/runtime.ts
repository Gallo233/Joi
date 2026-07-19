export type Live2DEmotion = 'happy' | 'thinking' | 'alert' | 'worried' | 'serious' | 'neutral'

interface PointLike {
  set?: (x: number, y?: number) => void
}

interface CoreModelLike {
  getParameterCount?: () => number
  getParameterId?: (index: number) => unknown
  setParameterValueByIndex?: (index: number, value: number, weight?: number) => void
  update?: () => void
}

interface Live2DModelLike {
  width?: number
  height?: number
  rotation?: number
  anchor?: PointLike
  position?: PointLike
  scale?: PointLike
  skew?: PointLike & { x?: number; y?: number }
  internalModel?: { coreModel?: CoreModelLike }
  getLocalBounds?: () => { width?: number; height?: number }
  update?: (delta: number) => void
  expression?: (id?: string) => Promise<boolean> | boolean
  motion?: (group: string, index?: number, priority?: number) => Promise<boolean> | boolean
  destroy?: (options?: Record<string, boolean>) => void
}

interface PixiApplicationLike {
  stage: { addChild: (model: Live2DModelLike) => void }
  renderer: {
    resize: (width: number, height: number) => void
    gl?: { isContextLost?: () => boolean }
    context?: { gl?: { isContextLost?: () => boolean } }
  }
  ticker?: { stop?: () => void }
  init: (options: Record<string, unknown>) => Promise<void>
  render?: () => void
  stop?: () => void
  destroy?: (removeView?: boolean) => void
}

interface Live2DNamespaceLike {
  Live2DModel?: {
    from: (url: string, options: Record<string, unknown>) => Promise<Live2DModelLike>
  }
  Live2DPlugin?: unknown
  MotionPreloadStrategy?: { NONE?: unknown }
  configureCubismSDK?: (options: { memorySizeMB: number }) => void
}

interface PixiLike {
  Application?: new () => PixiApplicationLike
  extensions?: { add: (plugin: unknown) => void }
  live2d?: Live2DNamespaceLike
}

declare global {
  interface Window {
    Live2DCubismCore?: { Version?: unknown; Moc?: unknown; Model?: unknown }
    PIXI?: PixiLike
  }
}

const runtimeScripts = [
  '/vendor/live2d/live2dcubismcore.min.js',
  '/vendor/live2d/pixi.min.js',
  '/vendor/live2d/cubism.min.js',
]
const loadedScripts = new Map<string, Promise<void>>()
let cubismConfigured = false
let pluginRegistered = false

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))
const damp = (current: number, target: number, lambda: number, deltaSeconds: number) =>
  current + (target - current) * (1 - Math.exp(-lambda * deltaSeconds))

function loadScript(src: string) {
  const existingPromise = loadedScripts.get(src)
  if (existingPromise) return existingPromise

  const promise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[data-joi-live2d-runtime="${src}"]`)
    if (existing?.dataset.loaded === 'true') {
      resolve()
      return
    }
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true })
      existing.addEventListener('error', () => reject(new Error(`Live2D runtime failed to load: ${src}`)), { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = src
    script.async = false
    script.dataset.joiLive2dRuntime = src
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true'
      resolve()
    }, { once: true })
    script.addEventListener('error', () => reject(new Error(`Live2D runtime failed to load: ${src}`)), { once: true })
    document.head.append(script)
  })
  loadedScripts.set(src, promise)
  promise.catch(() => loadedScripts.delete(src))
  return promise
}

function supportsWebGL() {
  const canvas = document.createElement('canvas')
  return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'))
}

async function waitForCubismCore() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const core = window.Live2DCubismCore
    if (core?.Version && core.Moc && core.Model) return
    await new Promise((resolve) => window.setTimeout(resolve, 100))
  }
  throw new Error('Live2D Cubism Core did not become ready')
}

async function ensureRuntime() {
  if (!supportsWebGL()) throw new Error('WebGL is unavailable')
  await loadScript(runtimeScripts[0])
  await waitForCubismCore()
  for (const script of runtimeScripts.slice(1)) await loadScript(script)

  const pixi = window.PIXI
  const live2d = pixi?.live2d
  if (!pixi?.Application || !live2d?.Live2DModel) throw new Error('Live2D runtime was not registered')
  if (live2d.configureCubismSDK && !cubismConfigured) {
    live2d.configureCubismSDK({ memorySizeMB: 32 })
    cubismConfigured = true
  }
  if (pixi.extensions && live2d.Live2DPlugin && !pluginRegistered) {
    pixi.extensions.add(live2d.Live2DPlugin)
    pluginRegistered = true
  }
  return { pixi, live2d }
}

function idToString(handle: unknown) {
  if (typeof handle === 'string') return handle
  if (!handle || typeof handle !== 'object') return ''
  const candidate = handle as { getString?: () => unknown }
  const value = candidate.getString?.()
  if (typeof value === 'string') return value
  if (value && typeof value === 'object' && 's' in value && typeof value.s === 'string') return value.s
  return ''
}

export interface Live2DController {
  resize: () => void
  setCompact: (compact: boolean) => void
  setEmotion: (emotion: Live2DEmotion) => void
  speak: (text: string) => void
  destroy: () => void
}

export interface Live2DExpressionMapping {
  emotion?: string
  expression_id?: string
  motion_group?: string
  motion_index?: number | string
}

export interface Live2DRuntimeMapping {
  expressions?: Live2DExpressionMapping[]
  lipSync?: { parameter?: string }
}

export async function mountLive2D(canvas: HTMLCanvasElement, modelUrl: string, mapping: Live2DRuntimeMapping = {}): Promise<Live2DController> {
  const { pixi, live2d } = await ensureRuntime()
  const Application = pixi.Application
  const Live2DModel = live2d.Live2DModel
  if (!Application || !Live2DModel) throw new Error('Live2D runtime is incomplete')

  const app = new Application()
  let model: Live2DModelLike | null = null
  try {
    await app.init({
      canvas,
      autoStart: false,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      preference: 'webgl',
      width: 280,
      height: 320,
    })
    // A root-relative URL is sufficient in a browser, but Pixi's nested asset
    // resolver can otherwise treat the first path segment as the host under
    // Tauri's custom protocol (for example `tauri://live2d/...`).
    const resolvedModelUrl = new URL(modelUrl, window.location.href).href
    model = await Live2DModel.from(resolvedModelUrl, {
      autoUpdate: false,
      autoFocus: false,
      autoHitTest: false,
      motionPreload: live2d.MotionPreloadStrategy?.NONE,
      textureOptions: { lod: 'single-auto' },
    })
  } catch (error) {
    try {
      model?.destroy?.({ children: true, texture: false, textureSource: false })
    } catch {
      // Preserve the original model-loading error.
    }
    try {
      app.destroy?.(false)
    } catch {
      // Older Pixi builds can throw while cleaning a partially initialized app.
    }
    throw error
  }
  model.anchor?.set?.(0.5, 0.5)
  app.stage.addChild(model)
  app.stop?.()
  app.ticker?.stop?.()

  let frameId = 0
  let destroyed = false
  let previousFrame = performance.now()
  let emotion: Live2DEmotion = 'neutral'
  let compact = false
  let talkUntil = 0
  let nextBlinkAt = previousFrame + 1800 + Math.random() * 2600
  let blinkUntil = 0
  let lookX = 0
  let lookY = 0
  let targetLookX = 0
  let targetLookY = 0
  let lastPointerAt = 0
  let baseX = 0
  let baseY = 0
  let baseScale = 1
  const parameterIndexes = new Map<string, number>()
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const mouthParameter = String(mapping.lipSync?.parameter || 'ParamMouthOpenY')

  const resize = () => {
    if (destroyed) return
    const container = canvas.parentElement
    const width = Math.max(1, Math.round(container?.clientWidth || 280))
    const height = Math.max(1, Math.round(container?.clientHeight || 320))
    app.renderer.resize(width, height)
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    model.scale?.set?.(1)
    let modelWidth = Math.abs(model.width || 0)
    let modelHeight = Math.abs(model.height || 0)
    if ((!modelWidth || !modelHeight) && model.getLocalBounds) {
      const bounds = model.getLocalBounds()
      modelWidth = Math.abs(bounds.width || 1)
      modelHeight = Math.abs(bounds.height || 1)
    }
    baseScale = compact
      ? Math.min((width * 0.96) / Math.max(1, modelWidth), (height * 0.98) / Math.max(1, modelHeight))
      : Math.min((width * 0.78) / Math.max(1, modelWidth), (height * 2.2) / Math.max(1, modelHeight))
    baseX = width * 0.5
    baseY = compact ? height * 0.51 : height * 0.71
    model.scale?.set?.(baseScale)
    model.position?.set?.(baseX, baseY)
  }

  const resolveParameterIndex = (coreModel: CoreModelLike, id: string) => {
    const cached = parameterIndexes.get(id)
    if (cached !== undefined) return cached
    const count = coreModel.getParameterCount?.() ?? 0
    for (let index = 0; index < count; index += 1) {
      if (idToString(coreModel.getParameterId?.(index)) === id) {
        parameterIndexes.set(id, index)
        return index
      }
    }
    parameterIndexes.set(id, -1)
    return -1
  }

  const setParameter = (coreModel: CoreModelLike, id: string, value: number) => {
    const index = resolveParameterIndex(coreModel, id)
    if (index >= 0 && Number.isFinite(value)) coreModel.setParameterValueByIndex?.(index, value, 1)
  }

  const expressionPose = () => {
    switch (emotion) {
      case 'happy':
        return { eye: 0.88, eyeSmile: 0.42, brow: 0.12, mouthForm: 0.58, cheek: 0.25, tilt: -2 }
      case 'thinking':
        return { eye: 0.96, eyeSmile: 0, brow: 0.18, mouthForm: 0.06, cheek: 0, tilt: 4 }
      case 'alert':
        return { eye: 1.1, eyeSmile: 0, brow: 0.4, mouthForm: 0.02, cheek: 0.08, tilt: 1 }
      case 'worried':
        return { eye: 0.92, eyeSmile: 0, brow: -0.2, mouthForm: -0.3, cheek: 0, tilt: 2.5 }
      case 'serious':
        return { eye: 0.9, eyeSmile: 0, brow: -0.08, mouthForm: -0.08, cheek: 0, tilt: 0 }
      default:
        return { eye: 1, eyeSmile: 0, brow: 0, mouthForm: 0.18, cheek: 0.04, tilt: 0 }
    }
  }

  const applyParameters = (now: number) => {
    const coreModel = model.internalModel?.coreModel
    if (!coreModel) return
    const pose = expressionPose()
    const blink = now < blinkUntil ? 0 : 1
    const mouth = now < talkUntil ? 0.2 + Math.abs(Math.sin(now / 78)) * 0.58 : 0
    const breath = reducedMotion ? 0.5 : (Math.sin(now / 850) + 1) * 0.5
    const hair = reducedMotion ? 0 : Math.sin(now / 1050) * 0.16 - lookX * 0.18

    setParameter(coreModel, 'ParamAngleX', lookX * 25)
    setParameter(coreModel, 'ParamAngleY', lookY * 18)
    setParameter(coreModel, 'ParamAngleZ', -lookX * 5 + pose.tilt)
    setParameter(coreModel, 'ParamEyeBallX', lookX)
    setParameter(coreModel, 'ParamEyeBallY', -lookY)
    setParameter(coreModel, 'ParamEyeLOpen', pose.eye * blink)
    setParameter(coreModel, 'ParamEyeROpen', pose.eye * blink)
    setParameter(coreModel, 'ParamEyeLSmile', pose.eyeSmile)
    setParameter(coreModel, 'ParamEyeRSmile', pose.eyeSmile)
    setParameter(coreModel, 'ParamBrowLY', pose.brow)
    setParameter(coreModel, 'ParamBrowRY', pose.brow)
    setParameter(coreModel, 'ParamBodyAngleX', lookX * 7)
    setParameter(coreModel, 'ParamBodyAngleY', lookY * 5)
    setParameter(coreModel, 'ParamBodyAngleZ', -lookX * 4)
    setParameter(coreModel, 'ParamBreath', breath)
    setParameter(coreModel, 'ParamHairFront', hair)
    setParameter(coreModel, 'ParamHairSide', hair * 0.8)
    setParameter(coreModel, 'ParamHairBack', hair * 0.55)
    setParameter(coreModel, mouthParameter, mouth)
    setParameter(coreModel, 'ParamMouthForm', now < talkUntil ? Math.max(0.3, pose.mouthForm) : pose.mouthForm)
    setParameter(coreModel, 'ParamCheek', pose.cheek)
    coreModel.update?.()
  }

  const render = (now: number) => {
    if (destroyed) return
    const delta = clamp(now - previousFrame, 8, 42)
    const deltaSeconds = delta / 1000
    previousFrame = now
    if (lastPointerAt && now - lastPointerAt > 2600) {
      targetLookX = 0
      targetLookY = 0
    }
    lookX = damp(lookX, targetLookX, 6.5, deltaSeconds)
    lookY = damp(lookY, targetLookY, 6.5, deltaSeconds)
    if (!reducedMotion && now >= nextBlinkAt) {
      blinkUntil = now + 145
      nextBlinkAt = now + 2200 + Math.random() * 3600
    }

    model.update?.(delta)
    applyParameters(now)
    const floatY = reducedMotion ? 0 : Math.sin(now / 1250) * 1.6
    model.position?.set?.(baseX + lookX * 2.5, baseY + floatY - lookY * 2)
    model.scale?.set?.(baseScale)
    if (typeof model.rotation === 'number') model.rotation = lookX * 0.025
    app.render?.()
    frameId = window.requestAnimationFrame(render)
  }

  const handlePointerMove = (event: PointerEvent) => {
    const rect = canvas.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    targetLookX = clamp((event.clientX - (rect.left + rect.width / 2)) / Math.max(180, rect.width), -1, 1)
    targetLookY = clamp((event.clientY - (rect.top + rect.height / 2)) / Math.max(180, rect.height), -1, 1)
    lastPointerAt = performance.now()
  }

  resize()
  model.update?.(16)
  applyParameters(performance.now())
  const webgl = app.renderer.gl || app.renderer.context?.gl
  if (webgl?.isContextLost?.()) throw new Error('Live2D WebGL context was lost')
  app.render?.()
  window.addEventListener('pointermove', handlePointerMove, { passive: true })
  frameId = window.requestAnimationFrame(render)

  return {
    resize,
    setCompact(value) {
      compact = value
      resize()
    },
    setEmotion(value) {
      const changed = emotion !== value
      emotion = value
      if (changed) {
        const entry = (mapping.expressions || []).find((row) => String(row.emotion || '').toLocaleLowerCase() === value)
        if (entry?.expression_id) void model?.expression?.(String(entry.expression_id))
        if (entry?.motion_group) {
          const index = Number(entry.motion_index || 0)
          void model?.motion?.(String(entry.motion_group), Number.isFinite(index) ? index : 0)
        }
      }
    },
    speak(text) {
      const duration = clamp(text.trim().length * 85, 900, 6500)
      talkUntil = text.trim() ? performance.now() + duration : 0
    },
    destroy() {
      if (destroyed) return
      destroyed = true
      window.cancelAnimationFrame(frameId)
      window.removeEventListener('pointermove', handlePointerMove)
      model.destroy?.({ children: true, texture: false, textureSource: false })
      app.destroy?.(false)
    },
  }
}
