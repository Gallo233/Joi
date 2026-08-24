/**
 * MMD (PMX/PMD) characters on the shared stage.
 *
 * three dropped its own MMDLoader before r185, so the loader here is
 * `@moeru/three-mmd` (MIT). Physics is deliberately not wired: it needs a
 * separate ammo/WASM payload, and a character standing in a chat window gains
 * very little from skirt simulation compared with what that payload costs to
 * ship.
 *
 * Emotions come from the stage table, never from this file: an MMD model
 * expresses a mood through named morphs, and which morph means "worried" is a
 * stage decision. A model missing a named morph simply shows nothing for that
 * mood rather than failing.
 */

import * as THREE from 'three'

import { mmdFraming } from './framing'

import { emotionWeight } from '../characterExpression'
import { STAGE_EMOTION_TABLE, stageEmotionShape, type StageController, type StageEmotion, type StageRuntimeMapping } from '../character/stage'
import { motionEnvelope, motionExpired, resolveCharacterMotion, type CharacterMotionRequest, type ResolvedCharacterMotion } from '../characterMotion'
import { mouthSignal, voiceDrivenMouthLevel, VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE } from '../voiceLipSync'

/**
 * The mouth morphs Japanese MMD models conventionally ship, most open first.
 * Lip sync drives whichever one the model actually has.
 */
const MOUTH_MORPHS = ['あ', 'a', 'A'] as const

/**
 * The standard MMD skeleton, by the names Japanese models ship.
 *
 * An MMD model is authored in its bind pose -- arms straight out -- and relies
 * entirely on motion data to look like anything else. No VMD is loaded here, so
 * without this the character stands with its arms out like a mannequin, which
 * is what "MMD support" looked like before: loaded, textured, and obviously
 * lifeless.
 */
const BONES = {
  upperBody: '上半身',
  neck: '首',
  head: '頭',
  leftArm: '左腕',
  rightArm: '右腕',
  leftElbow: '左ひじ',
  rightElbow: '右ひじ',
} as const

/**
 * How far the arms come down from the bind pose, in radians.
 *
 * Mirrored between the sides. MMD's convention rotates the left arm negatively
 * about Z to lower it; if a model lifts its arms instead, this is the sign to
 * flip.
 */
const ARM_REST = 0.52
const ELBOW_REST = 0.18

/** The blink morph, most common spelling first. */
const BLINK_MORPHS = ['まばたき', 'ウィンク', 'blink'] as const

/**
 * The clip name a package binds to loop as the character's resting state.
 *
 * Everything else in `mapping.animations` is a semantic motion played once when
 * Core asks for it; this one replaces the procedural idle below, because an
 * authored idle is always better than a sine wave on three bones.
 */
const IDLE_CLIP = 'idle'

export async function mountMMD(
  canvas: HTMLCanvasElement,
  modelUrl: string,
  mapping: StageRuntimeMapping = {},
): Promise<StageController> {
  const { MMDLoader, VMDLoader, buildAnimation } = await import('@moeru/three-mmd')

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setClearColor(0x000000, 0)
  // MMD toon materials are authored flat and bright. Without a tone map the sum
  // of the lights below clips white fabric and skin to a single value, which is
  // the same failure the VRM stage had.
  renderer.toneMapping = THREE.NeutralToneMapping
  renderer.toneMappingExposure = 1

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 200)
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9fb2cc, Math.PI * 0.3))
  const key = new THREE.DirectionalLight(0xffffff, Math.PI * 0.48)
  key.position.set(1.6, 2.6, 3.2)
  scene.add(key)
  const rim = new THREE.DirectionalLight(0xbcd4ff, Math.PI * 0.14)
  rim.position.set(-2.4, 1.8, -1.5)
  scene.add(rim)

  let model: Awaited<ReturnType<InstanceType<typeof MMDLoader>['loadAsync']>> | null = null
  try {
    model = await new MMDLoader().loadAsync(modelUrl)
  } catch (error) {
    renderer.dispose()
    throw error instanceof Error ? error : new Error('MMD model could not be loaded')
  }
  const mesh = model.mesh
  scene.add(mesh)
  mesh.traverse((object) => {
    object.frustumCulled = false
  })

  // PMX units are roughly 8 per metre, and models vary. Normalize on the
  // measured height so framing does not depend on how the author exported.
  const box = new THREE.Box3().setFromObject(mesh)
  const size = box.getSize(new THREE.Vector3())
  const measuredHeight = Math.max(size.y, 0.001)
  model.setScalar(1.6 / measuredHeight)

  const framed = new THREE.Box3().setFromObject(mesh)
  const framedSize = framed.getSize(new THREE.Vector3())
  const framedCenter = framed.getCenter(new THREE.Vector3())
  mesh.position.x -= framedCenter.x
  mesh.position.y -= framed.min.y
  mesh.position.z -= framedCenter.z

  const height = Math.max(framedSize.y, 0.001)
  const headY = height * 0.92
  let zoom = 1
  let compact = false

  const applyCamera = () => {
    // `compact` is `isCompactMode || characterFullBody`: it means "show the
    // whole body", which is what `fullBody` says here. See mmd/framing.ts.
    const { target, distance } = mmdFraming({
      height,
      headY,
      fullBody: compact,
      zoom,
      aspect: camera.aspect,
      fov: camera.fov,
    })
    camera.position.set(0, target, distance)
    camera.lookAt(0, target, 0)
  }

  const morphIndex = (name: string): number => {
    const dictionary = (mesh as THREE.SkinnedMesh & { morphTargetDictionary?: Record<string, number> }).morphTargetDictionary
    const index = dictionary?.[name]
    return typeof index === 'number' ? index : -1
  }

  const setMorph = (name: string, value: number) => {
    const index = morphIndex(name)
    if (index < 0) return
    const influences = (mesh as THREE.SkinnedMesh & { morphTargetInfluences?: number[] }).morphTargetInfluences
    if (influences) influences[index] = Math.max(0, Math.min(1, value))
  }

  const mouthMorph = MOUTH_MORPHS.find((name) => morphIndex(name) >= 0) || ''
  const blinkMorph = BLINK_MORPHS.find((name) => morphIndex(name) >= 0) || ''

  const bones = new Map<string, THREE.Bone>()
  for (const bone of mesh.skeleton?.bones || []) {
    if (!bones.has(bone.name)) bones.set(bone.name, bone)
  }
  const bone = (name: string) => bones.get(name) || null

  // Lower the arms out of the bind pose once. Everything after this is a small
  // offset from the rest pose rather than a fight with it.
  const restPose: Array<[THREE.Bone, THREE.Euler]> = []
  for (const [name, z] of [
    [BONES.leftArm, -ARM_REST],
    [BONES.rightArm, ARM_REST],
    [BONES.leftElbow, -ELBOW_REST],
    [BONES.rightElbow, ELBOW_REST],
  ] as const) {
    const target = bone(name)
    if (!target) continue
    target.rotation.z += z
    restPose.push([target, target.rotation.clone()])
  }
  for (const name of [BONES.upperBody, BONES.neck, BONES.head] as const) {
    const target = bone(name)
    if (target) restPose.push([target, target.rotation.clone()])
  }
  const restOf = (target: THREE.Bone | null) => restPose.find(([candidate]) => candidate === target)?.[1] || null

  /**
   * The whole skeleton as it stands once the arms are down.
   *
   * `restPose` above covers only the bones the procedural idle drives. That is
   * enough while a clip is playing, and not enough after one ends: a dance
   * moves legs, hips and fingers, and three.js leaves every bone wherever the
   * last evaluated frame put it. Without this the character kept her closing
   * pose from the waist down for the rest of the session.
   */
  const skeletonRest = mesh.skeleton.bones.map((target) => ({
    target,
    rotation: target.rotation.clone(),
    position: target.position.clone(),
  }))

  const restoreRestPose = () => {
    for (const entry of skeletonRest) {
      entry.target.rotation.copy(entry.rotation)
      entry.target.position.copy(entry.position)
    }
  }

  let emotion: StageEmotion = 'neutral'
  let emotionStartedAt = 0
  let motion: ResolvedCharacterMotion | null = null
  let motionStartedAt = 0
  let frame = 0
  const clock = new THREE.Clock()

  // Authored VMD clips the package bound to semantic motions. Loaded lazily and
  // cached: a character may ship several, and none of them are needed until the
  // motion they belong to is actually asked for.
  const mixer = new THREE.AnimationMixer(mesh)
  const clips = new Map<string, THREE.AnimationClip>()
  let activeAction: THREE.AnimationAction | null = null
  let idleAction: THREE.AnimationAction | null = null
  // A finished clip is clamped on its last frame at full weight, so it has to
  // be faded down and stopped. It stays here until the fade lands, because the
  // mixer is only advanced while something is driving the skeleton -- clearing
  // it immediately froze the fade mid-way through.
  let retiringAction: THREE.AnimationAction | null = null

  const loadClip = async (name: string): Promise<THREE.AnimationClip | null> => {
    const cached = clips.get(name)
    if (cached) return cached
    const url = mapping.animations?.[name]
    if (!url) return null
    try {
      const vmd = await new VMDLoader().loadAsync(url)
      const clip = buildAnimation(vmd, mesh)
      clips.set(name, clip)
      return clip
    } catch {
      // A clip that will not parse leaves the character on procedural motion
      // rather than failing the whole stage.
      return null
    }
  }

  const playClip = (clip: THREE.AnimationClip, loop: boolean) => {
    const action = mixer.clipAction(clip)
    action.reset()
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1)
    action.clampWhenFinished = !loop
    action.fadeIn(0.25).play()
    return action
  }

  /**
   * Drop a faded-out clip once its weight has actually reached zero.
   *
   * Checked from the render loop rather than on a timer: the mixer only
   * advances while something is driving the skeleton, so wall-clock time and
   * animation time are not the same thing here -- a tab in the background
   * would have retired the clip while the fade had not moved at all.
   */
  const retireFadedAction = () => {
    const action = retiringAction
    if (!action || action.getEffectiveWeight() > 0.01) return
    retiringAction = null
    action.stop()
    mixer.uncacheAction(action.getClip(), mesh)
    // With no authored idle to take over, the procedural one drives a handful
    // of bones and nothing restores the rest.
    if (!idleAction && !activeAction) restoreRestPose()
  }

  void loadClip(IDLE_CLIP).then((clip) => {
    if (clip) idleAction = playClip(clip, true)
  })

  const applyEmotion = (now: number) => {
    // Clear every morph the table can drive first, or a previous mood stays
    // blended into the new one.
    for (const row of Object.values(STAGE_EMOTION_TABLE)) {
      if (row.mmd) setMorph(row.mmd[0], 0)
    }
    const shape = stageEmotionShape('mmd', emotion)
    if (shape) setMorph(shape[0], shape[1] * emotionWeight(emotionStartedAt, now))
  }

  const applyMouth = (now: number) => {
    if (!mouthMorph) return
    // Only audio that is actually playing opens the mouth. The shared gate is
    // what stops a text event from producing a second, silent lip-sync.
    setMorph(mouthMorph, voiceDrivenMouthLevel(mouthSignal(now), VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE))
  }

  const applyMotion = (now: number) => {
    if (!motion) return
    if (motionExpired(motion, motionStartedAt, now)) {
      motion = null
      mesh.rotation.set(0, 0, 0)
      mesh.position.y = 0
      return
    }
    // No authored clips are loaded, so a semantic motion is expressed as a
    // bounded pose offset. It reads as movement without pretending to be
    // choreography the model does not carry.
    const envelope = motionEnvelope(motion, motionStartedAt, now)
    const swing = Math.sin((now - motionStartedAt) / 220) * envelope * motion.intensity
    mesh.rotation.y = swing * 0.28
    mesh.position.y = Math.abs(swing) * 0.06 * height
  }

  let blinkUntil = 0
  let nextBlinkAt = 0

  const applyIdle = (now: number) => {
    // An authored idle clip drives the whole skeleton, so the procedural one
    // stands down rather than fighting it bone by bone. The blink still runs:
    // most idle VMDs animate the body and leave the face alone.
    if (idleAction || activeAction || retiringAction) {
      applyBlink(now)
      return
    }
    // Breathing through the spine, a slow look around, and a blink. Small
    // amounts on purpose: this is a character standing in a chat window, not a
    // performance, and anything larger reads as swaying.
    const breath = Math.sin(now / 2600)
    const sway = Math.sin(now / 5200)
    const nod = Math.sin(now / 3900)
    for (const [target, axis, value] of [
      [bone(BONES.upperBody), 'x', breath * 0.018],
      [bone(BONES.neck), 'y', sway * 0.06],
      [bone(BONES.head), 'y', sway * 0.05],
      [bone(BONES.head), 'x', nod * 0.03],
    ] as const) {
      if (!target) continue
      const rest = restOf(target)
      if (!rest) continue
      target.rotation[axis] = rest[axis] + value
    }
    applyBlink(now)
  }

  const applyBlink = (now: number) => {
    if (!blinkMorph) return
    if (now >= nextBlinkAt) {
      blinkUntil = now + 110
      // Irregular on purpose: a blink on a fixed beat reads as a metronome.
      nextBlinkAt = now + 2400 + ((now * 7919) % 3600)
    }
    setMorph(blinkMorph, now < blinkUntil ? 1 : 0)
  }

  const render = () => {
    frame = requestAnimationFrame(render)
    const delta = clock.getDelta()
    const now = performance.now()
    applyEmotion(now)
    applyMouth(now)
    applyIdle(now)
    applyMotion(now)
    // updateWithMixer applies IK and append transforms *after* the mixer poses
    // the bones, which is the ordering MMD rigs are authored against.
    if (idleAction || activeAction || retiringAction) model?.updateWithMixer(delta, mixer)
    else model?.update(delta)
    retireFadedAction()
    renderer.render(scene, camera)
  }

  const resize = () => {
    const width = Math.max(canvas.clientWidth || canvas.width || 1, 1)
    const canvasHeight = Math.max(canvas.clientHeight || canvas.height || 1, 1)
    renderer.setSize(width, canvasHeight, false)
    camera.aspect = width / canvasHeight
    camera.updateProjectionMatrix()
    applyCamera()
  }

  resize()
  frame = requestAnimationFrame(render)

  return {
    resize,
    setCompact(next) {
      compact = next
      applyCamera()
    },
    setZoom(next) {
      zoom = Number.isFinite(next) && next > 0 ? next : 1
      applyCamera()
    },
    setEmotion(value) {
      if (value === emotion) return
      emotion = value
      emotionStartedAt = performance.now()
    },
    playMotion(request: CharacterMotionRequest) {
      const resolved = resolveCharacterMotion(request, mapping.motions)
      if (!resolved) return
      // An authored clip wins over the procedural swing, and only for the
      // motion it was bound to: a package that ships a wave keeps procedural
      // motion for everything else.
      void loadClip(resolved.name).then((clip) => {
        if (!clip) {
          motion = resolved
          motionStartedAt = performance.now()
          return
        }
        motion = null
        activeAction?.fadeOut(0.2)
        const action = playClip(clip, false)
        activeAction = action
        // `finished` is a mixer-wide event, so a second motion started before
        // the first one ends would otherwise be retired by the first one's
        // listener. Only act on the action that actually finished.
        mixer.addEventListener('finished', function done(event: { action?: THREE.AnimationAction }) {
          if (event.action !== action) return
          mixer.removeEventListener('finished', done)
          // `clampWhenFinished` holds the last frame at full weight. Fading the
          // idle in *underneath* that left the two blended with the dance still
          // at full strength, which is why she stayed in her closing pose: the
          // finished clip has to be faded out and stopped, not just covered.
          if (activeAction === action) activeAction = null
          action.fadeOut(0.25)
          retiringAction = action
          idleAction?.reset().fadeIn(0.25).play()
        })
      })
    },
    destroy() {
      cancelAnimationFrame(frame)
      scene.remove(mesh)
      model?.dispose()
      renderer.dispose()
    },
  }
}
