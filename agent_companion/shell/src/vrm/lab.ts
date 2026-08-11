/**
 * Development harness for the character stage.
 *
 * Framing, lighting, expression and motion are judged by eye, so this mounts
 * the real runtimes against local assets. Both formats appear because they have
 * separate framing code that drifts apart otherwise. Not part of the app
 * bundle: only vrm-lab.html loads it.
 *
 * Gesture clips are mounted under the `idle` slot so they loop and can be
 * inspected at leisure; in the app they are one-shots triggered by
 * `character.perform`.
 */

import { mountLive2D, type Live2DController } from '../live2d/runtime'
import { attachLipSync, mouthSignal, type MouthSignal } from '../voiceLipSync'
import { mountVRM, type VrmController } from './runtime'

const VRM_MODEL = '/vrm-lab/avatar-a.vrm'
const VRM1_MODEL = '/vrm-lab/seed-san.vrm'
const LIVE2D_MODEL = '/vrm-lab/hiyori/hiyori_pro_t11.model3.json'

const controllers: Record<string, VrmController | Live2DController> = {}

function stage(id: string): HTMLCanvasElement | null {
  const host = document.getElementById(id)
  if (!host) return null
  const canvas = document.createElement('canvas')
  host.appendChild(canvas)
  return canvas
}

async function mountVrmStage(id: string, fullBody: boolean, animations?: Record<string, string>, model = VRM_MODEL) {
  const canvas = stage(id)
  if (!canvas) return
  try {
    const controller = await mountVRM(canvas, model, animations ? { animations } : {})
    controller.setCompact(fullBody)
    controllers[id] = controller
    window.addEventListener('resize', () => controller.resize())
  } catch (error) {
    canvas.parentElement!.textContent = String(error)
  }
}

async function mountLive2DStage(id: string, fullBody: boolean) {
  const canvas = stage(id)
  if (!canvas) return
  try {
    const controller = await mountLive2D(canvas, LIVE2D_MODEL, {})
    controller.setCompact(fullBody)
    controllers[id] = controller
    window.addEventListener('resize', () => controller.resize())
  } catch (error) {
    canvas.parentElement!.textContent = String(error)
  }
}

Object.assign(window, {
  labControllers: controllers,
  labEmotion: (name: string) => {
    for (const controller of Object.values(controllers)) controller.setEmotion(name as never)
  },
  labSeek: (seconds: number) => {
    for (const [id, c] of Object.entries(controllers)) {
      const d = (c as { debug?: () => { animations?: { seek?: (s: number) => void } } }).debug?.()
      if (d && d.animations && d.animations.seek) d.animations.seek(seconds)
      else void id
    }
  },
  // Play a real voice clip and sample what the analyser reports, so lip sync
  // is checked against audio rather than assumed. The shape is recorded
  // alongside the level: a mouth can track the audio's loudness perfectly and
  // still be making the same vowel throughout, which is what this used to do.
  labVoice: async () => {
    const audio = new Audio('/vrm-lab/voice-test.wav')
    attachLipSync(audio)
    await audio.play()
    const samples: MouthSignal[] = []
    ;(window as unknown as Record<string, unknown>).__voiceSamples = samples
    const tick = () => {
      samples.push(mouthSignal())
      if (!audio.ended && samples.length < 900) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
    return 'playing'
  },
  labMotion: (name: string) => {
    for (const controller of Object.values(controllers)) {
      controller.playMotion({ motion: name, eventKey: `lab-${Date.now()}`, intensity: 1 } as never)
    }
  },
})

void (async () => {
  // No clip on either model: this is the procedural fallback, and the two
  // spec versions must now rest the same way.
  await mountVrmStage('stage-procedural', true)
  await mountVrmStage('stage-idle', true, { idle: '/vrm-lab/idle.vrma' })
  await mountVrmStage('stage-peace', true, { idle: '/vrm-lab/official-peace.vrma' })
  await mountLive2DStage('stage-wave', false)
})()
