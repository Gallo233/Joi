import {
  VRMLoaderPlugin,
  VRMUtils,
  type VRM,
  type VRMExpressionManager,
} from '@pixiv/three-vrm'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { Live2DEmotion } from '../live2d/runtime'

export interface VrmController {
  resize: () => void
  setCompact: (compact: boolean) => void
  setEmotion: (emotion: Live2DEmotion) => void
  speak: (text: string) => void
  destroy: () => void
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

export async function mountVRM(canvas: HTMLCanvasElement, modelUrl: string): Promise<VrmController> {
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

  const render = (now: number) => {
    if (destroyed) return
    const delta = clamp((now - previous) / 1000, 0.008, 0.05)
    previous = now
    pointerX += (targetPointerX - pointerX) * Math.min(1, delta * 6)
    pointerY += (targetPointerY - pointerY) * Math.min(1, delta * 6)
    const head = vrm.humanoid?.getNormalizedBoneNode('head')
    if (head) {
      head.rotation.y = pointerX * 0.18
      head.rotation.x = pointerY * -0.08
      head.rotation.z = pointerX * -0.025
    }
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
