import {
  VRMLoaderPlugin,
  VRMUtils,
  type VRM,
  type VRMExpressionManager,
} from '@pixiv/three-vrm'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import {
  motionEnvelope,
  motionExpired,
  resolveCharacterMotion,
  type CharacterMotionRequest,
  type ResolvedCharacterMotion,
} from '../characterMotion'
import { emotionWeight } from '../characterExpression'
import { mouthSignal, voiceDrivenMouthLevel, VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE } from '../voiceLipSync'
import { VISEMES } from '../voiceVisemes'

/**
 * VRM 1.0 preset names, and what a 0.x model calls the same thing.
 *
 * Ordered by preference where a spec offers no exact counterpart: 0.x has no
 * `relaxed`, and `Fun` is the nearest thing to it, but `happy` should claim
 * `Joy` first so the two moods do not collapse onto one morph.
 */
const VRM0_EXPRESSION_ALIASES: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['aa', ['a']],
  ['ih', ['i']],
  ['ou', ['u']],
  ['ee', ['e']],
  ['oh', ['o']],
  ['happy', ['joy', 'fun']],
  ['sad', ['sorrow']],
  ['relaxed', ['fun', 'joy']],
  ['surprised', ['surprised']],
  ['angry', ['angry']],
  ['blink', ['blink']],
]
import type { Live2DEmotion, Live2DRuntimeMapping } from '../live2d/runtime'
import { VrmAnimationPlayer, loadVrmAnimation } from './animation'
import {
  addVrmRotation,
  blendVrmMotionTarget,
  createNeutralVrmRotations,
  neutralVrmRotation,
  type VrmBoneRotation as BoneRotation,
  type VrmMotionBoneName as MotionBoneName,
} from './pose'

export interface VrmController {
  resize: () => void
  setCompact: (compact: boolean) => void
  /**
   * Stage zoom, applied to the camera rather than to the canvas. Optional:
   * the procedural 3D character shares this interface and is still scaled by
   * the stage, so callers must feature-check before using it.
   */
  setZoom?: (zoom: number) => void
  setEmotion: (emotion: Live2DEmotion) => void
  playMotion: (request: CharacterMotionRequest) => void
  destroy: () => void
  /** Loaded model, scene and clip player, for the development harness only. */
  debug?: () => { vrm: VRM; camera: THREE.PerspectiveCamera; scene: THREE.Scene; animations: VrmAnimationPlayer }
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

type MotionPose = {
  bones: Partial<Record<MotionBoneName, BoneRotation>>
  root: [number, number, number]
}

interface RunningMotion {
  motion: ResolvedCharacterMotion
  startedAt: number
  fadeStartedAt?: number
}

const MOTION_BONES: MotionBoneName[] = [
  'hips',
  'spine',
  'chest',
  'upperChest',
  'neck',
  'head',
  'leftShoulder',
  'leftUpperArm',
  'leftLowerArm',
  'leftHand',
  'rightShoulder',
  'rightUpperArm',
  'rightLowerArm',
  'rightHand',
  'leftUpperLeg',
  'leftLowerLeg',
  'rightUpperLeg',
  'rightLowerLeg',
]

export async function mountVRM(
  canvas: HTMLCanvasElement,
  modelUrl: string,
  mapping: Live2DRuntimeMapping = {},
): Promise<VrmController> {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setClearColor(0x000000, 0)
  // Three r155 made lighting physically correct, which changed what a given
  // intensity means. Without a tone map, the sum of these lights drives skin
  // and white fabric past 1.0 and they clip to flat white -- the character
  // loses every shading cue that reads as a face. Neutral tone mapping rolls
  // the highlights off instead of clipping them, and keeps hues where MToon's
  // flat anime shading put them.
  renderer.toneMapping = THREE.NeutralToneMapping
  renderer.toneMappingExposure = 1

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 100)
  camera.position.set(0, 1.25, 3.2)
  camera.lookAt(0, 1.15, 0)
  // three-vrm's own samples light a model with a single directional light at
  // intensity PI. This is that budget split three ways for shape, so the total
  // stays in the range MToon was authored against.
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9fb2cc, Math.PI * 0.28))
  const key = new THREE.DirectionalLight(0xffffff, Math.PI * 0.5)
  key.position.set(1.6, 2.6, 3.2)
  scene.add(key)
  const rim = new THREE.DirectionalLight(0xbcd4ff, Math.PI * 0.14)
  rim.position.set(-2.4, 1.8, -1.5)
  scene.add(rim)

  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.loadAsync(modelUrl)
  const vrm = gltf.userData.vrm as VRM | undefined
  if (!vrm) {
    renderer.dispose()
    throw new Error('VRM metadata was not found')
  }
  VRMUtils.rotateVRM0(vrm)
  scene.add(vrm.scene)
  vrm.scene.traverse((object) => {
    object.frustumCulled = false
  })

  const box = new THREE.Box3().setFromObject(vrm.scene)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  vrm.scene.position.x -= center.x
  vrm.scene.position.y -= box.min.y
  vrm.scene.position.z -= center.z

  // Frame against the skeleton rather than a fraction of total height: hair,
  // tails and props all inflate the bounding box, so "0.52 of the box" lands
  // somewhere different on every model. Eye and chest heights do not move.
  const modelHeight = Math.max(0.6, size.y || 1.7)
  const boneHeight = (name: MotionBoneName, fallback: number) => {
    const bone = vrm.humanoid?.getNormalizedBoneNode(name)
    if (!bone) return fallback
    vrm.scene.updateWorldMatrix(true, true)
    return bone.getWorldPosition(new THREE.Vector3()).y - vrm.scene.position.y
  }
  const eyeHeight = boneHeight('head', modelHeight * 0.92)

  let compact = false
  let zoom = 1
  let destroyed = false
  let emotion: Live2DEmotion = 'neutral'
  let emotionStartedAt = 0
  let frameId = 0
  let previous = performance.now()
  let pointerX = 0
  let pointerY = 0
  let targetPointerX = 0
  let targetPointerY = 0
  let nextBlinkAt = previous + 2200
  let activeMotion: RunningMotion | null = null
  let outgoingMotion: RunningMotion | null = null
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  // Authored .vrma clips, when the character package ships them. Loading is
  // best-effort and runs after the model is on screen, so a slow or missing
  // clip never delays the character appearing -- and never blocks the stage.
  const animations = new VrmAnimationPlayer(vrm)
  const loadAuthoredMotions = async () => {
    const declared = mapping.animations || {}
    for (const [name, url] of Object.entries(declared)) {
      if (!url) continue
      const loaded = await loadVrmAnimation(url, vrm, name)
      if (destroyed) return
      if (loaded) animations.add(loaded, { idle: name === 'idle' })
    }
    if (!destroyed && !reducedMotion && animations.has('idle')) animations.play('idle', { fade: 0.4 })
  }
  const baseScenePosition = vrm.scene.position.clone()
  const motionBones = new Map<MotionBoneName, THREE.Object3D>()
  const baseBoneRotations = new Map<MotionBoneName, THREE.Quaternion>()
  const motionQuaternion = new THREE.Quaternion()
  const motionEuler = new THREE.Euler()
  for (const name of MOTION_BONES) {
    const bone = vrm.humanoid?.getNormalizedBoneNode(name)
    if (!bone) continue
    motionBones.set(name, bone)
    baseBoneRotations.set(name, bone.quaternion.clone())
  }

  /**
   * Point the eyes at the pointer, not just the head.
   *
   * VRM carries its own look-at rig, and without a target the eyes stay fixed
   * forward while the head turns, which reads as staring past the user. The
   * target rides in front of the face at pointer offset, so the eyes track
   * whatever the head is already doing, clip or not.
   */
  const lookAtTarget = new THREE.Object3D()
  scene.add(lookAtTarget)
  if (vrm.lookAt) vrm.lookAt.target = lookAtTarget
  const applyLookAt = () => {
    if (!vrm.lookAt) return
    lookAtTarget.position.set(
      pointerX * 0.7,
      eyeHeight + pointerY * -0.45,
      // Far enough forward that the eyes converge naturally rather than
      // crossing on a point inside the head.
      1.6,
    )
  }

  /**
   * Apply a procedural pose, in the same bone space a `.vrma` uses.
   *
   * three-vrm reads a clip as VRM 1.0 and mirrors it for a 0.x model by
   * negating the quaternion's x and z -- the 180-degree turn between the two
   * conventions. Poses written here go straight onto the normalized bones with
   * no such step, and they were tuned against a 0.x model, so on a VRM 1.0
   * character every one of them came out mirrored: the resting arms lifted
   * instead of dropping.
   *
   * Conjugating a rotation by 180 degrees about Y negates the x and z of an
   * XYZ euler exactly as it negates those quaternion components, so one sign
   * flip puts both paths in the same space.
   */
  const mirrorPose = String(vrm.meta?.metaVersion ?? '1') === '0' ? 1 : -1
  const setPoseEuler = (x: number, y: number, z: number) => {
    motionEuler.set(x * mirrorPose, y, z * mirrorPose, 'XYZ')
    motionQuaternion.setFromEuler(motionEuler)
  }

  // VRM 1.0 fixed the preset names as lowercase, but 0.x models carry whatever
  // casing the author used -- this sample exposes `Surprised`. Resolving
  // through what the model actually declares means a mismatched name is a
  // no-op we can see, not a silently dropped expression.
  const expressionNames = new Map<string, string>()
  for (const expression of vrm.expressionManager?.expressions ?? []) {
    const name = String((expression as { expressionName?: string; name?: string }).expressionName ?? expression.name ?? '')
    if (name) expressionNames.set(name.toLowerCase(), name)
  }
  // 0.x did not merely use different casing, it used different words: the
  // vowels are A/I/U/E/O and the moods are Joy/Fun/Sorrow. Asking a 0.x model
  // for `aa` therefore matches nothing at all, and because a missing preset is
  // deliberately a no-op the failure is invisible -- the character simply
  // never opens its mouth. Aliases are registered under the 1.0 names this
  // code speaks, so the rest of the runtime never has to know which spec it
  // is driving.
  for (const [canonical, aliases] of VRM0_EXPRESSION_ALIASES) {
    if (expressionNames.has(canonical)) continue
    const found = aliases.find((alias) => expressionNames.has(alias))
    if (found) expressionNames.set(canonical, expressionNames.get(found) as string)
  }

  const setExpression = (manager: VRMExpressionManager | null | undefined, name: string, value: number) => {
    const resolved = expressionNames.get(name.toLowerCase())
    if (!resolved) return
    try {
      manager?.setValue(resolved, clamp(value, 0, 1))
    } catch {
      // Individual VRM files may omit optional expression presets.
    }
  }

  // How strongly each mood drives its blendshape at its peak, before the
  // shared envelope releases it. `happy` is deliberately mild: on VRoid rigs it
  // morphs the eyes shut, and a value in the middle leaves the lids half down,
  // which reads as a squint rather than a smile.
  const EMOTION_SHAPES: Partial<Record<Live2DEmotion, [string, number]>> = {
    happy: ['happy', 0.42],
    thinking: ['relaxed', 0.34],
    alert: ['surprised', 0.5],
    worried: ['sad', 0.45],
    serious: ['angry', 0.18],
  }
  const applyEmotion = (now: number) => {
    const manager = vrm.expressionManager
    for (const [shape] of Object.values(EMOTION_SHAPES)) setExpression(manager, shape, 0)
    const shape = EMOTION_SHAPES[emotion]
    if (shape) setExpression(manager, shape[0], shape[1] * emotionWeight(emotionStartedAt, now))
    // Only a voice that is actually playing can drive visemes. Text events and
    // synthesis failures deliberately leave the mouth closed.
    const voice = mouthSignal(now)
    const mouth = voiceDrivenMouthLevel(voice, VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE)
    // Each vowel preset takes its share of the opening. The shares sum to 1,
    // so the mouth opens as far as it always did -- it just does it with the
    // shape the audio is actually making. With no audio to read the shape is
    // all `aa`, which is the single morph this used to drive.
    for (const viseme of VISEMES) setExpression(manager, viseme, mouth * voice.visemes[viseme])
    const blinking = now > nextBlinkAt && now < nextBlinkAt + 135
    setExpression(manager, 'blink', blinking ? 1 : 0)
    if (now >= nextBlinkAt + 135) nextBlinkAt = now + 2200 + Math.random() * 3400
  }

  const resize = () => {
    if (destroyed) return
    const container = canvas.parentElement
    const width = Math.max(1, Math.round(container?.clientWidth || 280))
    const height = Math.max(1, Math.round(container?.clientHeight || 320))
    renderer.setSize(width, height, false)
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    camera.aspect = width / height
    // `compact` means "show the whole body"; the other mode is an upper-body
    // framing. They used to differ by 16% of height, which is why both read as
    // the same distant full shot.
    //
    // Vertical FOV only decides the visible height, so a narrow stage crops the
    // sides. Widening the view on a narrow canvas keeps the arms in frame.
    const aspect = Math.max(0.35, camera.aspect)
    const widthCorrection = aspect < 0.72 ? 0.72 / aspect : 1
    // Both framings are expressed as a span to keep in view, and the top edge
    // is the model's own bounding box rather than a multiple of bone spacing:
    // hair and headwear sit above the head bone, and cropping them is the one
    // mistake a viewer notices immediately.
    const top = modelHeight + modelHeight * 0.04
    // The lower edge is a fraction of height rather than the chest bone: how
    // high the chest sits varies enough between rigs that the same number
    // frames one model at the collarbone and another at the waist.
    const bottom = compact ? -modelHeight * 0.02 : modelHeight * 0.56
    const middle = (top + bottom) * 0.5
    const half = Math.max(0.1, ((top - bottom) * widthCorrection) / (2 * clamp(zoom, 0.5, 2.5)))
    const viewTop = middle + half
    const viewBottom = middle - half

    // Framing a standing figure by pointing the camera at the middle of it
    // puts the lens near the hips, and a face a metre above the lens is a face
    // seen from below: the chin widens, the eyes foreshorten into a squint.
    //
    // So the camera stays at eye level with a level optical axis, and the
    // frame is moved optically instead -- the same thing a shift lens does for
    // buildings. The face sits on the axis where there is no distortion, and
    // vertical lines stay vertical.
    const camY = eyeHeight
    const halfFrustum = Math.max(viewTop - camY, camY - viewBottom, 0.05)
    const distance = halfFrustum / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5))
    camera.position.set(0, camY, distance)
    camera.lookAt(0, camY, 0)

    // Show only the part of that frustum the framing asked for.
    const fullSpan = halfFrustum * 2
    const fullHeightPx = height * (fullSpan / (viewTop - viewBottom))
    const fullWidthPx = fullHeightPx * aspect
    camera.setViewOffset(
      fullWidthPx,
      fullHeightPx,
      (fullWidthPx - width) * 0.5,
      ((camY + halfFrustum - viewTop) / fullSpan) * fullHeightPx,
      width,
      height,
    )
    camera.updateProjectionMatrix()
  }

  const handlePointer = (event: PointerEvent) => {
    const rect = canvas.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    targetPointerX = clamp((event.clientX - rect.left) / rect.width * 2 - 1, -1, 1)
    targetPointerY = clamp((event.clientY - rect.top) / rect.height * 2 - 1, -1, 1)
  }

  const applyMotion = (now: number) => {
    if (activeMotion && motionExpired(activeMotion.motion, activeMotion.startedAt, now)) {
      activeMotion = null
      if (animations.active) animations.rest()
    }
    if (outgoingMotion?.fadeStartedAt && now - outgoingMotion.fadeStartedAt >= 180) outgoingMotion = null

    // An authored clip owns the bones it writes to, so the procedural pose
    // stays off those. It does not necessarily write to all of them: a gesture
    // clip may carry only upper-body tracks, and anything it skips would
    // otherwise sit at the rig's T-pose with the arms straight out. Those keep
    // the resting pose, and gaze still applies because a clip cannot know
    // where the pointer is.
    if (animations.active) {
      // The clip's track names are the model's own node names, not humanoid
      // bone names, so membership has to be tested against the node a bone
      // resolves to. Comparing the two vocabularies directly never matches,
      // which silently overwrites the clip on every bone it does drive.
      const driven = animations.animatedNodes
      for (const name of MOTION_BONES) {
        const bone = motionBones.get(name)
        const base = baseBoneRotations.get(name)
        if (!bone || !base || driven.has(bone.name)) continue
        const rest = neutralVrmRotation(name)
        setPoseEuler(rest[0], rest[1], rest[2])
        bone.quaternion.copy(base).multiply(motionQuaternion)
      }
      // Gaze layers on top of whatever the clip did with the head rather than
      // replacing it. A character that stops looking at you the moment a clip
      // starts stops feeling present, and the clip cannot know where the
      // pointer is -- so the clip keeps the performance and gaze adds the
      // attention, composed rather than one overwriting the other.
      const head = motionBones.get('head')
      if (head) {
        setPoseEuler(pointerY * -0.05, pointerX * 0.12, pointerX * -0.02)
        head.quaternion.multiply(motionQuaternion)
      }
      applyLookAt()
      return
    }

    const rotations = createNeutralVrmRotations()
    const root: [number, number, number] = [0, 0, 0]
    const blend = (running: RunningMotion | null, fadeWeight = 1) => {
      if (!running || reducedMotion) return
      const elapsed = Math.max(0, now - running.startedAt) / 1000
      const weight = motionEnvelope(running.motion, running.startedAt, now) * running.motion.intensity * fadeWeight
      if (weight <= 0) return
      const pose = vrmMotionPose(running.motion.name, elapsed)
      for (const [name, value] of Object.entries(pose.bones) as Array<[MotionBoneName, BoneRotation]>) {
        const current = rotations.get(name) || neutralVrmRotation(name)
        rotations.set(name, blendVrmMotionTarget(name, current, value, weight))
      }
      root[0] += pose.root[0] * weight
      root[1] += pose.root[1] * weight
      root[2] += pose.root[2] * weight
    }

    if (!reducedMotion) {
      const idlePose = vrmMotionPose('idle', now / 1000)
      for (const [name, value] of Object.entries(idlePose.bones) as Array<[MotionBoneName, BoneRotation]>) {
        rotations.set(name, addVrmRotation(rotations.get(name) || neutralVrmRotation(name), value))
      }
      root[0] += idlePose.root[0]
      root[1] += idlePose.root[1]
      root[2] += idlePose.root[2]
    }
    if (outgoingMotion?.fadeStartedAt) {
      blend(outgoingMotion, clamp(1 - (now - outgoingMotion.fadeStartedAt) / 180, 0, 1))
    }
    blend(activeMotion)

    const gaze = rotations.get('head') || [0, 0, 0]
    rotations.set('head', [
      gaze[0] + pointerY * -0.08,
      gaze[1] + pointerX * 0.18,
      gaze[2] + pointerX * -0.025,
    ])

    for (const name of MOTION_BONES) {
      const bone = motionBones.get(name)
      const base = baseBoneRotations.get(name)
      if (!bone || !base) continue
      bone.quaternion.copy(base)
      const value = rotations.get(name)
      if (!value) continue
      setPoseEuler(value[0], value[1], value[2])
      bone.quaternion.multiply(motionQuaternion)
    }
    vrm.scene.position.set(
      baseScenePosition.x + root[0],
      baseScenePosition.y + root[1],
      baseScenePosition.z + root[2],
    )
    applyLookAt()
  }

  const render = (now: number) => {
    if (destroyed) return
    const delta = clamp((now - previous) / 1000, 0.008, 0.05)
    previous = now
    pointerX += (targetPointerX - pointerX) * Math.min(1, delta * 6)
    pointerY += (targetPointerY - pointerY) * Math.min(1, delta * 6)
    // The mixer writes bone tracks first; applyMotion then either leaves them
    // alone or replaces them with the procedural pose.
    animations.update(delta)
    applyMotion(now)
    applyEmotion(now)
    vrm.update(delta)
    renderer.render(scene, camera)
    frameId = window.requestAnimationFrame(render)
  }

  resize()
  window.addEventListener('pointermove', handlePointer, { passive: true })
  frameId = window.requestAnimationFrame(render)
  void loadAuthoredMotions()

  return {
    resize,
    setCompact(value) {
      compact = value
      resize()
    },
    setZoom(value) {
      zoom = Number.isFinite(value) && value > 0 ? value : 1
      resize()
    },
    setEmotion(value) {
      // Restart the envelope even when the mood is unchanged, so a second
      // reaction of the same kind still shows.
      emotion = value
      emotionStartedAt = performance.now()
    },
    playMotion(request) {
      const now = performance.now()
      const resolved = resolveCharacterMotion(request, mapping.motions || [])
      // Prefer the authored clip for this semantic motion; fall back to the
      // procedural pose only when the package did not ship one.
      if (!reducedMotion && animations.play(resolved.name)) {
        activeMotion = { motion: resolved, startedAt: now }
        outgoingMotion = null
        return
      }
      if (activeMotion) outgoingMotion = { ...activeMotion, fadeStartedAt: now }
      activeMotion = { motion: resolved, startedAt: now }
    },
    debug() {
      return { vrm, camera, scene, animations }
    },
    destroy() {
      if (destroyed) return
      destroyed = true
      window.cancelAnimationFrame(frameId)
      window.removeEventListener('pointermove', handlePointer)
      if (vrm.lookAt) vrm.lookAt.target = undefined
      scene.remove(lookAtTarget)
      animations.destroy()
      scene.remove(vrm.scene)
      VRMUtils.deepDispose(vrm.scene)
      renderer.dispose()
    },
  }
}

function vrmMotionPose(name: ResolvedCharacterMotion['name'], time: number): MotionPose {
  const pose: MotionPose = { bones: {}, root: [0, 0, 0] }
  if (name === 'idle') {
    pose.bones.chest = [Math.sin(time * 1.7) * 0.012, 0, Math.sin(time * 0.8) * 0.008]
    pose.bones.head = [Math.sin(time * 0.7) * 0.006, Math.sin(time * 0.45) * 0.01, 0]
    pose.root[1] = Math.sin(time * 1.7) * 0.003
    return pose
  }
  if (name === 'greet') {
    const wave = Math.sin(time * 8.5)
    pose.bones.chest = [0, -0.08, -0.04]
    pose.bones.head = [0.03, -0.08, -0.08]
    pose.bones.rightShoulder = [0, 0, -0.18]
    pose.bones.rightUpperArm = [-0.28, -0.18, -1.08]
    pose.bones.rightLowerArm = [-0.12, 0.12, -1.08]
    pose.bones.rightHand = [0, wave * 0.36, wave * 0.18]
    return pose
  }
  if (name === 'talk') {
    const gesture = Math.sin(time * 3.3)
    pose.bones.chest = [0, gesture * 0.035, gesture * 0.025]
    pose.bones.head = [0, gesture * -0.025, gesture * -0.018]
    pose.bones.leftUpperArm = [-0.08, 0, 1.02 + gesture * 0.08]
    pose.bones.rightUpperArm = [-0.08, 0, -1.02 - gesture * 0.08]
    pose.bones.leftLowerArm = [0, gesture * 0.05, 0.18]
    pose.bones.rightLowerArm = [0, gesture * -0.05, -0.18]
    return pose
  }
  if (name === 'happy') {
    const bounce = Math.abs(Math.sin(time * 5.8))
    pose.bones.chest = [-0.08, 0, Math.sin(time * 5.8) * 0.04]
    pose.bones.head = [-0.04, 0, Math.sin(time * 5.8) * -0.035]
    pose.bones.leftUpperArm = [-0.22, -0.12, 1.08]
    pose.bones.rightUpperArm = [-0.22, 0.12, -1.08]
    pose.bones.leftLowerArm = [-0.18, 0, 0.52]
    pose.bones.rightLowerArm = [-0.18, 0, -0.52]
    pose.root[1] = bounce * 0.035
    return pose
  }
  if (name === 'finger_gun') {
    pose.bones.hips = [0, -0.12, -0.04]
    pose.bones.chest = [-0.06, 0.25, 0.09]
    pose.bones.head = [0.04, 0.15, -0.09]
    pose.bones.rightUpperArm = [-1.08, -0.18, -0.42]
    pose.bones.rightLowerArm = [-0.18, 0.18, -0.12]
    pose.bones.rightHand = [0.05, -0.08, 0.08]
    pose.bones.leftUpperArm = [-0.62, 0.18, 0.34]
    pose.bones.leftLowerArm = [-0.12, -0.15, 0.48]
    pose.root[0] = 0.025
    return pose
  }

  const sway = Math.sin(time * 4.1)
  const counter = Math.sin(time * 4.1 + Math.PI)
  const bounce = Math.abs(Math.sin(time * 4.1))
  pose.bones.hips = [0, sway * 0.16, sway * 0.16]
  pose.bones.spine = [0, counter * 0.08, counter * 0.08]
  pose.bones.chest = [0, counter * 0.12, counter * 0.11]
  pose.bones.head = [Math.sin(time * 8.2) * 0.025, sway * -0.05, sway * -0.08]
  pose.bones.leftUpperArm = [-0.18 + counter * 0.18, 0, 0.68 + sway * 0.38]
  pose.bones.rightUpperArm = [-0.18 + sway * 0.18, 0, -0.68 + sway * 0.38]
  pose.bones.leftLowerArm = [-0.2, counter * 0.16, 0.42]
  pose.bones.rightLowerArm = [-0.2, sway * 0.16, -0.42]
  pose.bones.leftUpperLeg = [counter * 0.12, 0, counter * 0.08]
  pose.bones.rightUpperLeg = [sway * 0.12, 0, sway * 0.08]
  pose.bones.leftLowerLeg = [Math.max(0, sway) * 0.14, 0, 0]
  pose.bones.rightLowerLeg = [Math.max(0, counter) * 0.14, 0, 0]
  pose.root[0] = sway * 0.045
  pose.root[1] = bounce * 0.028
  return pose
}
