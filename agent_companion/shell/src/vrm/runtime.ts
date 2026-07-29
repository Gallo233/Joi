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
import type { Live2DEmotion, Live2DRuntimeMapping } from '../live2d/runtime'

export interface VrmController {
  resize: () => void
  setCompact: (compact: boolean) => void
  setEmotion: (emotion: Live2DEmotion) => void
  speak: (text: string) => void
  playMotion: (request: CharacterMotionRequest) => void
  destroy: () => void
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

type MotionBoneName =
  | 'hips'
  | 'spine'
  | 'chest'
  | 'upperChest'
  | 'neck'
  | 'head'
  | 'leftShoulder'
  | 'leftUpperArm'
  | 'leftLowerArm'
  | 'leftHand'
  | 'rightShoulder'
  | 'rightUpperArm'
  | 'rightLowerArm'
  | 'rightHand'
  | 'leftUpperLeg'
  | 'leftLowerLeg'
  | 'rightUpperLeg'
  | 'rightLowerLeg'

type BoneRotation = [number, number, number]
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

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 100)
  camera.position.set(0, 1.25, 3.2)
  camera.lookAt(0, 1.15, 0)
  scene.add(new THREE.HemisphereLight(0xffffff, 0x90a4c5, 2.2))
  const key = new THREE.DirectionalLight(0xffffff, 2.8)
  key.position.set(1.8, 2.8, 3.4)
  scene.add(key)
  const rim = new THREE.DirectionalLight(0x8fb7ff, 1.3)
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

  let compact = false
  let destroyed = false
  let emotion: Live2DEmotion = 'neutral'
  let talkUntil = 0
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

  const setExpression = (manager: VRMExpressionManager | null | undefined, name: string, value: number) => {
    try {
      manager?.setValue(name, clamp(value, 0, 1))
    } catch {
      // Individual VRM files may omit optional expression presets.
    }
  }

  const applyEmotion = (now: number) => {
    const manager = vrm.expressionManager
    const rows = ['happy', 'relaxed', 'sad', 'surprised', 'angry']
    for (const name of rows) setExpression(manager, name, 0)
    if (emotion === 'happy') setExpression(manager, 'happy', 0.72)
    if (emotion === 'thinking') setExpression(manager, 'relaxed', 0.34)
    if (emotion === 'alert') setExpression(manager, 'surprised', 0.5)
    if (emotion === 'worried') setExpression(manager, 'sad', 0.45)
    if (emotion === 'serious') setExpression(manager, 'angry', 0.18)
    const speaking = now < talkUntil
    setExpression(manager, 'aa', speaking ? 0.12 + Math.abs(Math.sin(now / 82)) * 0.48 : 0)
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
    const heightScale = Math.max(0.6, size.y || 1.7)
    const viewHeight = compact ? heightScale * 1.06 : heightScale * 0.9
    const distance = viewHeight / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)))
    camera.position.set(0, compact ? heightScale * 0.52 : heightScale * 0.56, distance * (compact ? 1.04 : 1.08))
    camera.lookAt(0, compact ? heightScale * 0.5 : heightScale * 0.52, 0)
    camera.updateProjectionMatrix()
  }

  const handlePointer = (event: PointerEvent) => {
    const rect = canvas.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    targetPointerX = clamp((event.clientX - rect.left) / rect.width * 2 - 1, -1, 1)
    targetPointerY = clamp((event.clientY - rect.top) / rect.height * 2 - 1, -1, 1)
  }

  const applyMotion = (now: number) => {
    if (activeMotion && motionExpired(activeMotion.motion, activeMotion.startedAt, now)) activeMotion = null
    if (outgoingMotion?.fadeStartedAt && now - outgoingMotion.fadeStartedAt >= 180) outgoingMotion = null

    const rotations = new Map<MotionBoneName, BoneRotation>()
    const root: [number, number, number] = [0, 0, 0]
    const blend = (running: RunningMotion | null, fadeWeight = 1) => {
      if (!running || reducedMotion) return
      const elapsed = Math.max(0, now - running.startedAt) / 1000
      const weight = motionEnvelope(running.motion, running.startedAt, now) * running.motion.intensity * fadeWeight
      if (weight <= 0) return
      const pose = vrmMotionPose(running.motion.name, elapsed)
      for (const [name, value] of Object.entries(pose.bones) as Array<[MotionBoneName, BoneRotation]>) {
        const current = rotations.get(name) || [0, 0, 0]
        rotations.set(name, [
          current[0] + value[0] * weight,
          current[1] + value[1] * weight,
          current[2] + value[2] * weight,
        ])
      }
      root[0] += pose.root[0] * weight
      root[1] += pose.root[1] * weight
      root[2] += pose.root[2] * weight
    }

    if (!reducedMotion) {
      const idlePose = vrmMotionPose('idle', now / 1000)
      for (const [name, value] of Object.entries(idlePose.bones) as Array<[MotionBoneName, BoneRotation]>) {
        rotations.set(name, value)
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
      motionEuler.set(value[0], value[1], value[2], 'XYZ')
      motionQuaternion.setFromEuler(motionEuler)
      bone.quaternion.multiply(motionQuaternion)
    }
    vrm.scene.position.set(
      baseScenePosition.x + root[0],
      baseScenePosition.y + root[1],
      baseScenePosition.z + root[2],
    )
  }

  const render = (now: number) => {
    if (destroyed) return
    const delta = clamp((now - previous) / 1000, 0.008, 0.05)
    previous = now
    pointerX += (targetPointerX - pointerX) * Math.min(1, delta * 6)
    pointerY += (targetPointerY - pointerY) * Math.min(1, delta * 6)
    applyMotion(now)
    applyEmotion(now)
    vrm.update(delta)
    renderer.render(scene, camera)
    frameId = window.requestAnimationFrame(render)
  }

  resize()
  window.addEventListener('pointermove', handlePointer, { passive: true })
  frameId = window.requestAnimationFrame(render)

  return {
    resize,
    setCompact(value) {
      compact = value
      resize()
    },
    setEmotion(value) {
      emotion = value
    },
    speak(text) {
      const value = text.trim()
      talkUntil = value ? performance.now() + clamp(value.length * 85, 900, 6500) : 0
    },
    playMotion(request) {
      const now = performance.now()
      if (activeMotion) outgoingMotion = { ...activeMotion, fadeStartedAt: now }
      activeMotion = {
        motion: resolveCharacterMotion(request, mapping.motions || []),
        startedAt: now,
      }
    },
    destroy() {
      if (destroyed) return
      destroyed = true
      window.cancelAnimationFrame(frameId)
      window.removeEventListener('pointermove', handlePointer)
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
    pose.bones.leftUpperArm = [-0.08, 0, 0.18 + gesture * 0.08]
    pose.bones.rightUpperArm = [-0.08, 0, -0.18 - gesture * 0.08]
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
