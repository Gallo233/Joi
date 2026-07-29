import * as THREE from 'three'
import {
  motionEnvelope,
  motionExpired,
  resolveCharacterMotion,
  type CharacterMotionRequest,
  type ResolvedCharacterMotion,
} from '../characterMotion'
import type { Live2DEmotion, Live2DRuntimeMapping } from '../live2d/runtime'
import type { VrmController } from '../vrm/runtime'

type RigBoneName =
  | 'torso'
  | 'head'
  | 'leftUpperArm'
  | 'leftLowerArm'
  | 'leftHand'
  | 'rightUpperArm'
  | 'rightLowerArm'
  | 'rightHand'
  | 'leftUpperLeg'
  | 'leftLowerLeg'
  | 'rightUpperLeg'
  | 'rightLowerLeg'

type Rotation = [number, number, number]

interface CharacterPose {
  rotations: Partial<Record<RigBoneName, Rotation>>
  root: [number, number, number]
}

interface RunningMotion {
  motion: ResolvedCharacterMotion
  startedAt: number
  fadeStartedAt?: number
}

interface CharacterRig {
  root: THREE.Group
  torso: THREE.Group
  head: THREE.Group
  leftUpperArm: THREE.Group
  leftLowerArm: THREE.Group
  leftHand: THREE.Group
  rightUpperArm: THREE.Group
  rightLowerArm: THREE.Group
  rightHand: THREE.Group
  leftUpperLeg: THREE.Group
  leftLowerLeg: THREE.Group
  rightUpperLeg: THREE.Group
  rightLowerLeg: THREE.Group
  leftEye: THREE.Mesh
  rightEye: THREE.Mesh
  leftPupil: THREE.Mesh
  rightPupil: THREE.Mesh
  mouth: THREE.Mesh
  leftCheek: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>
  rightCheek: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>
  pointerFinger: THREE.Mesh
  sparkles: THREE.Mesh<THREE.OctahedronGeometry, THREE.MeshBasicMaterial>[]
  platform: THREE.Mesh<THREE.TorusGeometry, THREE.MeshBasicMaterial>
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

const damp = (current: number, target: number, lambda: number, delta: number) =>
  THREE.MathUtils.lerp(current, target, 1 - Math.exp(-lambda * delta))

export async function mountProceduralCharacter3D(
  canvas: HTMLCanvasElement,
  mapping: Live2DRuntimeMapping = {},
): Promise<VrmController> {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: true,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 0.96
  renderer.setClearColor(0x000000, 0)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(25, 1, 0.1, 30)
  camera.position.set(0, 1.58, 9.7)
  camera.lookAt(0, 1.58, 0)

  const hemisphere = new THREE.HemisphereLight(0xfffbff, 0x6f86b8, 1.55)
  scene.add(hemisphere)
  const key = new THREE.DirectionalLight(0xfff7ef, 2.35)
  key.position.set(3.5, 5.4, 5.2)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0x91b7ff, 1.15)
  fill.position.set(-4.2, 2.8, 3.2)
  scene.add(fill)
  const rim = new THREE.PointLight(0xff7baa, 2.2, 8)
  rim.position.set(2.6, 3.2, -1.5)
  scene.add(rim)

  const rig = createCharacterRig(scene)
  const bones: Record<RigBoneName, THREE.Group> = {
    torso: rig.torso,
    head: rig.head,
    leftUpperArm: rig.leftUpperArm,
    leftLowerArm: rig.leftLowerArm,
    leftHand: rig.leftHand,
    rightUpperArm: rig.rightUpperArm,
    rightLowerArm: rig.rightLowerArm,
    rightHand: rig.rightHand,
    leftUpperLeg: rig.leftUpperLeg,
    leftLowerLeg: rig.leftLowerLeg,
    rightUpperLeg: rig.rightUpperLeg,
    rightLowerLeg: rig.rightLowerLeg,
  }
  const baseRotations = new Map<RigBoneName, THREE.Euler>()
  for (const [name, bone] of Object.entries(bones) as Array<[RigBoneName, THREE.Group]>) {
    baseRotations.set(name, bone.rotation.clone())
  }
  const baseRootPosition = rig.root.position.clone()
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  let frameId = 0
  let destroyed = false
  let compact = false
  let emotion: Live2DEmotion = 'neutral'
  let previousFrame = performance.now()
  let talkUntil = 0
  let nextBlinkAt = previousFrame + 1700 + Math.random() * 2400
  let blinkUntil = 0
  let pointerX = 0
  let pointerY = 0
  let targetPointerX = 0
  let targetPointerY = 0
  let lastPointerAt = 0
  let activeMotion: RunningMotion | null = null
  let outgoingMotion: RunningMotion | null = null

  const resize = () => {
    if (destroyed) return
    const container = canvas.parentElement
    const width = Math.max(1, Math.round(container?.clientWidth || 360))
    const height = Math.max(1, Math.round(container?.clientHeight || 520))
    renderer.setSize(width, height, false)
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    camera.aspect = width / height
    const portraitRatio = height / Math.max(1, width)
    camera.position.z = compact ? 9.9 : clamp(9.55 + (1.2 - portraitRatio) * 0.35, 9.35, 9.95)
    camera.position.y = 1.58
    camera.lookAt(0, 1.58, 0)
    camera.updateProjectionMatrix()
    rig.root.scale.setScalar(compact ? 0.9 : 0.94)
  }

  const handlePointer = (event: PointerEvent) => {
    const bounds = canvas.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    targetPointerX = clamp((event.clientX - bounds.left) / bounds.width * 2 - 1, -1, 1)
    targetPointerY = clamp((event.clientY - bounds.top) / bounds.height * 2 - 1, -1, 1)
    lastPointerAt = performance.now()
  }

  const applyPose = (now: number) => {
    if (activeMotion && motionExpired(activeMotion.motion, activeMotion.startedAt, now)) activeMotion = null
    if (outgoingMotion?.fadeStartedAt && now - outgoingMotion.fadeStartedAt >= 180) outgoingMotion = null

    const rotations = new Map<RigBoneName, Rotation>()
    const root: [number, number, number] = [0, 0, 0]
    const blend = (running: RunningMotion | null, fadeWeight = 1) => {
      if (!running || reducedMotion) return
      const weight = motionEnvelope(running.motion, running.startedAt, now)
        * running.motion.intensity
        * fadeWeight
      if (weight <= 0) return
      const pose = proceduralMotionPose(running.motion.name, Math.max(0, now - running.startedAt) / 1000)
      for (const [name, value] of Object.entries(pose.rotations) as Array<[RigBoneName, Rotation]>) {
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
      const idle = proceduralMotionPose('idle', now / 1000)
      for (const [name, value] of Object.entries(idle.rotations) as Array<[RigBoneName, Rotation]>) {
        rotations.set(name, value)
      }
      root[0] += idle.root[0]
      root[1] += idle.root[1]
      root[2] += idle.root[2]
    }
    if (outgoingMotion?.fadeStartedAt) {
      blend(outgoingMotion, clamp(1 - (now - outgoingMotion.fadeStartedAt) / 180, 0, 1))
    }
    blend(activeMotion)

    const head = rotations.get('head') || [0, 0, 0]
    rotations.set('head', [
      head[0] + pointerY * -0.09,
      head[1] + pointerX * 0.18,
      head[2] + pointerX * -0.035,
    ])
    for (const [name, bone] of Object.entries(bones) as Array<[RigBoneName, THREE.Group]>) {
      const base = baseRotations.get(name)
      if (!base) continue
      const delta = rotations.get(name) || [0, 0, 0]
      bone.rotation.set(base.x + delta[0], base.y + delta[1], base.z + delta[2], base.order)
    }
    rig.root.position.set(
      baseRootPosition.x + root[0],
      baseRootPosition.y + root[1],
      baseRootPosition.z + root[2],
    )

    const activeName = activeMotion?.motion.name || 'idle'
    rig.pointerFinger.visible = activeName === 'finger_gun' && !reducedMotion
    applyFace(rig, emotion, now, talkUntil, blinkUntil, pointerX, pointerY)
    animateEffects(rig, activeName, now, reducedMotion)
  }

  const render = (now: number) => {
    if (destroyed) return
    const delta = clamp((now - previousFrame) / 1000, 0.008, 0.05)
    previousFrame = now
    if (lastPointerAt && now - lastPointerAt > 2600) {
      targetPointerX = 0
      targetPointerY = 0
    }
    pointerX = damp(pointerX, targetPointerX, 6, delta)
    pointerY = damp(pointerY, targetPointerY, 6, delta)
    if (!reducedMotion && now >= nextBlinkAt) {
      blinkUntil = now + 135
      nextBlinkAt = now + 1900 + Math.random() * 3200
    }
    applyPose(now)
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
      talkUntil = value ? performance.now() + clamp(value.length * 82, 900, 6500) : 0
    },
    playMotion(request: CharacterMotionRequest) {
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
      scene.traverse((object) => {
        if (!(object instanceof THREE.Mesh)) return
        object.geometry?.dispose()
        const materials = Array.isArray(object.material) ? object.material : [object.material]
        for (const material of materials) material?.dispose()
      })
      renderer.dispose()
    },
  }
}

function createCharacterRig(scene: THREE.Scene): CharacterRig {
  const root = new THREE.Group()
  root.position.y = -0.08
  scene.add(root)

  const skin = toon(0xffc9b8, 0.1)
  const skinWarm = toon(0xf3b8a7, 0.08)
  const hair = toon(0x4b2d42, 0.25)
  const hairLight = toon(0x765069, 0.3)
  const cream = toon(0xf7eadf, 0.05)
  const creamShadow = toon(0xd8c5bd, 0.04)
  const coral = toon(0xd76568, 0.08)
  const coralDark = toon(0x9e444e, 0.12)
  const navy = toon(0x31516e, 0.18)
  const navyDark = toon(0x1e314b, 0.12)
  const gold = toon(0xf1b760, 0.22)
  const white = toon(0xffffff, 0.08)

  const platformMaterial = new THREE.MeshBasicMaterial({
    color: 0x77a9ff,
    transparent: true,
    opacity: 0.38,
  })
  const platform = new THREE.Mesh(new THREE.TorusGeometry(0.82, 0.025, 10, 72), platformMaterial)
  platform.rotation.x = Math.PI / 2
  platform.position.y = 0.02
  root.add(platform)
  const shadowMaterial = new THREE.MeshBasicMaterial({
    color: 0x8da0bd,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
  })
  const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.72, 48), shadowMaterial)
  shadow.rotation.x = -Math.PI / 2
  shadow.position.y = 0.018
  root.add(shadow)

  const torso = new THREE.Group()
  torso.position.y = 1.48
  root.add(torso)
  addMesh(torso, new THREE.CapsuleGeometry(0.42, 0.54, 8, 20), cream, [0, 0.52, 0], [0, 0, 0], [1.08, 1, 0.72])
  addMesh(torso, new THREE.CylinderGeometry(0.42, 0.67, 0.72, 28), coral, [0, -0.03, 0], [0, 0, 0], [1, 1, 0.78])
  addMesh(torso, new THREE.BoxGeometry(0.08, 0.72, 0.035), creamShadow, [-0.22, 0.51, 0.34], [0, 0, 0.04])
  addMesh(torso, new THREE.BoxGeometry(0.08, 0.72, 0.035), creamShadow, [0.22, 0.51, 0.34], [0, 0, -0.04])
  addMesh(torso, new THREE.CylinderGeometry(0.055, 0.07, 0.42, 12), navy, [0, 0.67, 0.4], [0.04, 0, 0], [1, 1, 0.45])
  addMesh(torso, new THREE.SphereGeometry(0.055, 16, 12), gold, [0, 0.3, 0.39])
  addMesh(torso, new THREE.SphereGeometry(0.055, 16, 12), gold, [0, 0.53, 0.4])
  addMesh(torso, new THREE.SphereGeometry(0.055, 16, 12), gold, [0, 0.76, 0.38])

  const neck = addMesh(torso, new THREE.CylinderGeometry(0.13, 0.15, 0.28, 16), skinWarm, [0, 1.08, 0])
  neck.renderOrder = 0

  const head = new THREE.Group()
  head.position.set(0, 3.06, 0)
  head.scale.setScalar(0.78)
  root.add(head)
  addMesh(head, new THREE.SphereGeometry(0.58, 36, 28), skin, [0, 0, 0], [0, 0, 0], [0.96, 1.07, 0.9])
  addMesh(
    head,
    new THREE.SphereGeometry(0.62, 36, 24, 0, Math.PI * 2, 0, 1.54),
    hair,
    [0, 0.09, -0.02],
    [0, 0, 0],
    [1, 1.04, 0.97],
  )
  for (const [x, angle, scale] of [
    [-0.43, -0.18, 0.92],
    [-0.25, -0.1, 1.05],
    [-0.06, -0.03, 1.12],
    [0.15, 0.08, 1.04],
    [0.34, 0.16, 0.92],
  ] as Array<[number, number, number]>) {
    addMesh(
      head,
      new THREE.CapsuleGeometry(0.075, 0.26 * scale, 4, 10),
      hairLight,
      [x, 0.34, 0.49],
      [0.08, 0, angle],
    )
  }
  addMesh(head, new THREE.CapsuleGeometry(0.13, 0.54, 6, 14), hair, [-0.53, -0.1, -0.04], [0.05, 0.1, -0.06])
  addMesh(head, new THREE.CapsuleGeometry(0.13, 0.54, 6, 14), hair, [0.53, -0.1, -0.04], [0.05, -0.1, 0.06])
  addMesh(head, new THREE.CapsuleGeometry(0.18, 0.48, 6, 14), hair, [0, -0.22, -0.42], [0, 0, Math.PI / 2])
  const ahoge = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.022, 8, 24, Math.PI * 1.3), hairLight)
  ahoge.position.set(0.04, 0.69, 0)
  ahoge.rotation.set(0.12, 0.2, -0.4)
  head.add(ahoge)

  const eyeWhite = toon(0xfffeff, 0.04)
  const eyeDark = toon(0x3b2837, 0.2)
  const eyeBlue = toon(0x6d9fd6, 0.25)
  const leftEye = addMesh(head, new THREE.SphereGeometry(0.1, 20, 14), eyeWhite, [-0.2, 0.03, 0.5], [0, 0, 0], [1, 1.08, 0.34])
  const rightEye = addMesh(head, new THREE.SphereGeometry(0.1, 20, 14), eyeWhite, [0.2, 0.03, 0.5], [0, 0, 0], [1, 1.08, 0.34])
  const leftPupil = addMesh(head, new THREE.SphereGeometry(0.052, 18, 12), eyeBlue, [-0.2, 0.02, 0.576], [0, 0, 0], [0.88, 1.06, 0.32])
  const rightPupil = addMesh(head, new THREE.SphereGeometry(0.052, 18, 12), eyeBlue, [0.2, 0.02, 0.576], [0, 0, 0], [0.88, 1.06, 0.32])
  addMesh(head, new THREE.SphereGeometry(0.023, 12, 8), eyeDark, [-0.2, 0.02, 0.626])
  addMesh(head, new THREE.SphereGeometry(0.023, 12, 8), eyeDark, [0.2, 0.02, 0.626])
  addMesh(head, new THREE.SphereGeometry(0.014, 10, 8), white, [-0.22, 0.055, 0.64])
  addMesh(head, new THREE.SphereGeometry(0.014, 10, 8), white, [0.18, 0.055, 0.64])
  addMesh(head, new THREE.BoxGeometry(0.18, 0.025, 0.025), hair, [-0.2, 0.21, 0.53], [0, 0, -0.08])
  addMesh(head, new THREE.BoxGeometry(0.18, 0.025, 0.025), hair, [0.2, 0.21, 0.53], [0, 0, 0.08])

  const mouth = addMesh(head, new THREE.SphereGeometry(0.075, 18, 12), coralDark, [0, -0.2, 0.55], [0, 0, 0], [1.15, 0.36, 0.3])
  const cheekMaterial = new THREE.MeshBasicMaterial({
    color: 0xff8f9f,
    transparent: true,
    opacity: 0.2,
    depthWrite: false,
  })
  const leftCheek = new THREE.Mesh(new THREE.CircleGeometry(0.085, 20), cheekMaterial.clone())
  leftCheek.position.set(-0.34, -0.12, 0.548)
  leftCheek.scale.y = 0.42
  head.add(leftCheek)
  const rightCheek = new THREE.Mesh(new THREE.CircleGeometry(0.085, 20), cheekMaterial.clone())
  rightCheek.position.set(0.34, -0.12, 0.548)
  rightCheek.scale.y = 0.42
  head.add(rightCheek)

  const bowCenter = addMesh(head, new THREE.SphereGeometry(0.075, 14, 10), gold, [0.5, 0.24, 0.2])
  bowCenter.rotation.z = -0.2
  addMesh(head, new THREE.ConeGeometry(0.14, 0.28, 3), coral, [0.57, 0.33, 0.18], [0, 0, -1.1])
  addMesh(head, new THREE.ConeGeometry(0.14, 0.28, 3), coral, [0.56, 0.14, 0.18], [0, 0, 1.05])

  const leftUpperArm = createArm(root, 0.59, 2.48, 1, cream, skin, coral)
  const rightUpperArm = createArm(root, -0.59, 2.48, -1, cream, skin, coral)
  const leftLowerArm = leftUpperArm.children.find((child) => child.name === 'lower-arm') as THREE.Group
  const rightLowerArm = rightUpperArm.children.find((child) => child.name === 'lower-arm') as THREE.Group
  const leftHand = leftLowerArm.children.find((child) => child.name === 'hand') as THREE.Group
  const rightHand = rightLowerArm.children.find((child) => child.name === 'hand') as THREE.Group
  const pointerFinger = addMesh(
    rightHand,
    new THREE.CapsuleGeometry(0.028, 0.22, 4, 8),
    skin,
    [0, -0.19, 0.09],
    [Math.PI / 2, 0, 0],
  )
  pointerFinger.visible = false

  const leftUpperLeg = createLeg(root, 0.23, 1.3, skin, white, navyDark)
  const rightUpperLeg = createLeg(root, -0.23, 1.3, skin, white, navyDark)
  const leftLowerLeg = leftUpperLeg.children.find((child) => child.name === 'lower-leg') as THREE.Group
  const rightLowerLeg = rightUpperLeg.children.find((child) => child.name === 'lower-leg') as THREE.Group

  const sparkles: THREE.Mesh<THREE.OctahedronGeometry, THREE.MeshBasicMaterial>[] = []
  for (let index = 0; index < 7; index += 1) {
    const material = new THREE.MeshBasicMaterial({
      color: index % 2 ? 0xff7ba7 : 0x73b6ff,
      transparent: true,
      opacity: 0,
      depthWrite: false,
    })
    const sparkle = new THREE.Mesh(new THREE.OctahedronGeometry(index % 3 === 0 ? 0.07 : 0.045, 0), material)
    sparkle.visible = false
    root.add(sparkle)
    sparkles.push(sparkle)
  }

  return {
    root,
    torso,
    head,
    leftUpperArm,
    leftLowerArm,
    leftHand,
    rightUpperArm,
    rightLowerArm,
    rightHand,
    leftUpperLeg,
    leftLowerLeg,
    rightUpperLeg,
    rightLowerLeg,
    leftEye,
    rightEye,
    leftPupil,
    rightPupil,
    mouth,
    leftCheek,
    rightCheek,
    pointerFinger,
    sparkles,
    platform,
  }
}

function createArm(
  parent: THREE.Group,
  x: number,
  y: number,
  side: 1 | -1,
  sleeveMaterial: THREE.Material,
  skinMaterial: THREE.Material,
  accentMaterial: THREE.Material,
) {
  const upper = new THREE.Group()
  upper.position.set(x, y, 0)
  upper.rotation.z = side * -0.15
  parent.add(upper)
  addMesh(upper, new THREE.CapsuleGeometry(0.15, 0.44, 6, 14), sleeveMaterial, [0, -0.36, 0], [0, 0, 0], [1, 1, 0.82])
  addMesh(upper, new THREE.TorusGeometry(0.15, 0.025, 8, 20), accentMaterial, [0, -0.64, 0], [Math.PI / 2, 0, 0])

  const lower = new THREE.Group()
  lower.name = 'lower-arm'
  lower.position.y = -0.7
  upper.add(lower)
  addMesh(lower, new THREE.CapsuleGeometry(0.11, 0.38, 6, 12), sleeveMaterial, [0, -0.31, 0], [0, 0, 0], [1, 1, 0.82])

  const hand = new THREE.Group()
  hand.name = 'hand'
  hand.position.y = -0.62
  lower.add(hand)
  addMesh(hand, new THREE.SphereGeometry(0.13, 20, 14), skinMaterial)
  return upper
}

function createLeg(
  parent: THREE.Group,
  x: number,
  y: number,
  skinMaterial: THREE.Material,
  bootMaterial: THREE.Material,
  soleMaterial: THREE.Material,
) {
  const upper = new THREE.Group()
  upper.position.set(x, y, 0)
  parent.add(upper)
  addMesh(upper, new THREE.CapsuleGeometry(0.14, 0.52, 6, 14), skinMaterial, [0, -0.42, 0], [0, 0, 0], [1, 1, 0.92])

  const lower = new THREE.Group()
  lower.name = 'lower-leg'
  lower.position.y = -0.82
  upper.add(lower)
  addMesh(lower, new THREE.CapsuleGeometry(0.145, 0.48, 6, 14), bootMaterial, [0, -0.4, 0], [0, 0, 0], [1, 1, 0.94])
  addMesh(lower, new THREE.SphereGeometry(0.18, 20, 14), bootMaterial, [0, -0.78, 0.1], [0.12, 0, 0], [0.92, 0.72, 1.28])
  addMesh(lower, new THREE.BoxGeometry(0.25, 0.06, 0.36), soleMaterial, [0, -0.9, 0.12])
  return upper
}

function addMesh<T extends THREE.BufferGeometry, M extends THREE.Material>(
  parent: THREE.Object3D,
  geometry: T,
  material: M,
  position: [number, number, number] = [0, 0, 0],
  rotation: [number, number, number] = [0, 0, 0],
  scale: [number, number, number] = [1, 1, 1],
) {
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(...position)
  mesh.rotation.set(...rotation)
  mesh.scale.set(...scale)
  parent.add(mesh)
  return mesh
}

function toon(color: number, emissiveIntensity = 0) {
  return new THREE.MeshToonMaterial({
    color,
    emissive: color,
    emissiveIntensity,
  })
}

function applyFace(
  rig: CharacterRig,
  emotion: Live2DEmotion,
  now: number,
  talkUntil: number,
  blinkUntil: number,
  pointerX: number,
  pointerY: number,
) {
  const blink = now < blinkUntil ? 0.08 : 1
  const happy = emotion === 'happy'
  const worried = emotion === 'worried'
  const alert = emotion === 'alert'
  const eyeHeight = blink * (happy ? 0.55 : worried ? 0.84 : alert ? 1.12 : 1)
  rig.leftEye.scale.y = 1.08 * eyeHeight
  rig.rightEye.scale.y = 1.08 * eyeHeight
  rig.leftPupil.scale.y = 1.06 * eyeHeight
  rig.rightPupil.scale.y = 1.06 * eyeHeight
  const pupilX = pointerX * 0.022
  const pupilY = pointerY * -0.018
  rig.leftPupil.position.set(-0.2 + pupilX, 0.02 + pupilY, 0.576)
  rig.rightPupil.position.set(0.2 + pupilX, 0.02 + pupilY, 0.576)

  const speaking = now < talkUntil
  const mouthOpen = speaking ? 0.5 + Math.abs(Math.sin(now / 78)) * 0.9 : happy ? 0.68 : worried ? 0.38 : 0.45
  rig.mouth.scale.set(1.15 + (happy ? 0.28 : 0), 0.36 * mouthOpen, 0.3)
  rig.mouth.position.y = worried ? -0.17 : -0.2
  rig.mouth.rotation.z = worried ? Math.PI : 0
  const cheekOpacity = happy ? 0.48 : emotion === 'alert' ? 0.28 : 0.2
  rig.leftCheek.material.opacity = cheekOpacity
  rig.rightCheek.material.opacity = cheekOpacity
}

function animateEffects(
  rig: CharacterRig,
  motion: ResolvedCharacterMotion['name'],
  now: number,
  reducedMotion: boolean,
) {
  const show = !reducedMotion && (motion === 'dance' || motion === 'happy')
  const seconds = now / 1000
  for (let index = 0; index < rig.sparkles.length; index += 1) {
    const sparkle = rig.sparkles[index]
    sparkle.visible = show
    sparkle.material.opacity = show ? 0.36 + Math.sin(seconds * 5 + index) * 0.18 : 0
    const angle = seconds * (index % 2 ? 0.55 : -0.48) + index / rig.sparkles.length * Math.PI * 2
    const radius = 0.92 + (index % 3) * 0.14
    sparkle.position.set(Math.cos(angle) * radius, 2.05 + Math.sin(angle * 1.7) * 1.1, -0.25)
    sparkle.rotation.set(seconds + index, seconds * 0.8, angle)
  }
  rig.platform.material.opacity = motion === 'dance' ? 0.65 : 0.38
  rig.platform.material.color.setHex(motion === 'dance' ? 0xff6fa8 : 0x77a9ff)
  rig.platform.rotation.z = reducedMotion ? 0 : seconds * (motion === 'dance' ? 0.8 : 0.15)
}

function proceduralMotionPose(name: ResolvedCharacterMotion['name'], time: number): CharacterPose {
  const pose: CharacterPose = { rotations: {}, root: [0, 0, 0] }
  if (name === 'idle') {
    pose.rotations.torso = [Math.sin(time * 1.5) * 0.008, 0, Math.sin(time * 0.75) * 0.012]
    pose.rotations.head = [Math.sin(time * 0.65) * 0.008, Math.sin(time * 0.42) * 0.012, 0]
    pose.root[1] = Math.sin(time * 1.5) * 0.006
    return pose
  }
  if (name === 'greet') {
    const wave = Math.sin(time * 9)
    pose.rotations.torso = [0, -0.08, -0.045]
    pose.rotations.head = [0.025, -0.07, -0.1]
    pose.rotations.rightUpperArm = [-0.18, -0.1, -2.25]
    pose.rotations.rightLowerArm = [0, 0.18, -0.5]
    pose.rotations.rightHand = [0, wave * 0.52, wave * 0.28]
    return pose
  }
  if (name === 'talk') {
    const gesture = Math.sin(time * 3.2)
    pose.rotations.torso = [0, gesture * 0.04, gesture * 0.035]
    pose.rotations.head = [0, gesture * -0.035, gesture * -0.025]
    pose.rotations.leftUpperArm = [-0.05, 0, 0.24 + gesture * 0.12]
    pose.rotations.rightUpperArm = [-0.05, 0, -0.24 - gesture * 0.12]
    pose.rotations.leftLowerArm = [0, 0, 0.3 + gesture * 0.14]
    pose.rotations.rightLowerArm = [0, 0, -0.3 - gesture * 0.14]
    return pose
  }
  if (name === 'happy') {
    const bounce = Math.abs(Math.sin(time * 5.8))
    pose.rotations.torso = [-0.08, 0, Math.sin(time * 5.8) * 0.04]
    pose.rotations.head = [-0.04, 0, Math.sin(time * 5.8) * -0.05]
    pose.rotations.leftUpperArm = [-0.15, -0.1, 2.0]
    pose.rotations.rightUpperArm = [-0.15, 0.1, -2.0]
    pose.rotations.leftLowerArm = [-0.1, 0, 0.4]
    pose.rotations.rightLowerArm = [-0.1, 0, -0.4]
    pose.root[1] = bounce * 0.08
    return pose
  }
  if (name === 'finger_gun') {
    pose.rotations.torso = [-0.08, 0.28, 0.1]
    pose.rotations.head = [0.03, 0.16, -0.1]
    pose.rotations.rightUpperArm = [-1.25, -0.12, -0.52]
    pose.rotations.rightLowerArm = [-0.45, 0.22, -0.08]
    pose.rotations.rightHand = [0.1, -0.1, 0.08]
    pose.rotations.leftUpperArm = [-0.72, 0.12, 0.42]
    pose.rotations.leftLowerArm = [-0.22, -0.1, 0.62]
    pose.root[0] = 0.04
    return pose
  }

  const beat = Math.sin(time * 4.2)
  const counter = Math.sin(time * 4.2 + Math.PI)
  const bounce = Math.abs(Math.sin(time * 4.2))
  pose.rotations.torso = [0, counter * 0.17, counter * 0.12]
  pose.rotations.head = [Math.sin(time * 8.4) * 0.035, beat * -0.06, beat * -0.11]
  pose.rotations.leftUpperArm = [-0.2 + counter * 0.22, 0, 0.72 + beat * 0.52]
  pose.rotations.rightUpperArm = [-0.2 + beat * 0.22, 0, -0.72 + beat * 0.52]
  pose.rotations.leftLowerArm = [-0.25, counter * 0.14, 0.5]
  pose.rotations.rightLowerArm = [-0.25, beat * 0.14, -0.5]
  pose.rotations.leftUpperLeg = [counter * 0.22, 0, counter * 0.1]
  pose.rotations.rightUpperLeg = [beat * 0.22, 0, beat * 0.1]
  pose.rotations.leftLowerLeg = [Math.max(0, beat) * 0.24, 0, 0]
  pose.rotations.rightLowerLeg = [Math.max(0, counter) * 0.24, 0, 0]
  pose.root[0] = beat * 0.1
  pose.root[1] = bounce * 0.07
  return pose
}
