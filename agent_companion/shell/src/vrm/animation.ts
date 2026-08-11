/**
 * VRM Animation (.vrma) playback.
 *
 * A .vrma file is a glTF carrying the `VRMC_vrm_animation` extension: humanoid
 * bone tracks plus optional expression and look-at tracks, authored against the
 * VRM humanoid rig rather than one model's skeleton. That is what makes a clip
 * portable -- the same file drives any VRM, whatever its proportions.
 *
 * Clips are the authored path; the procedural poses in `runtime.ts` remain the
 * fallback for a character package that ships none.
 */

import { VRMAnimationLoaderPlugin, createVRMAnimationClip, type VRMAnimation } from '@pixiv/three-vrm-animation'
import type { VRM } from '@pixiv/three-vrm'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

export interface LoadedVrmAnimation {
  name: string
  clip: THREE.AnimationClip
  duration: number
  /**
   * Normalized bone node names the clip actually writes to.
   *
   * A clip is free to animate only part of the body. Anything it leaves alone
   * keeps whatever the normalized rig holds, which is the T-pose, so a
   * gesture clip with no arm tracks would fling the arms out sideways. The
   * caller uses this to hold untouched bones at their resting pose instead.
   */
  animatedNodes: ReadonlySet<string>
}

let loader: GLTFLoader | null = null

function animationLoader(): GLTFLoader {
  if (!loader) {
    loader = new GLTFLoader()
    loader.register((parser) => new VRMAnimationLoaderPlugin(parser))
  }
  return loader
}

/**
 * Load one .vrma and retarget it onto a specific model.
 *
 * The returned clip is bound to `vrm`: a clip built for one model cannot be
 * played on another, because retargeting bakes in that model's proportions.
 */
export async function loadVrmAnimation(url: string, vrm: VRM, name: string): Promise<LoadedVrmAnimation | null> {
  try {
    const gltf = await animationLoader().loadAsync(url)
    const animations = gltf.userData.vrmAnimations as VRMAnimation[] | undefined
    const animation = animations?.[0]
    if (!animation) return null
    const clip = createVRMAnimationClip(animation, vrm)
    clip.name = name
    // Track names are "<node name>.<property>"; the node name is what the
    // caller can match against its own bone map.
    const animatedNodes = new Set(clip.tracks.map((track) => track.name.split('.')[0]).filter(Boolean))
    return { name, clip, duration: clip.duration, animatedNodes }
  } catch {
    // A missing or malformed clip degrades to procedural motion rather than
    // failing the character: the stage must still render.
    return null
  }
}

/**
 * Plays retargeted clips with cross-fades, and reports whether anything is
 * currently driving the skeleton so procedural motion can stay out of the way.
 */
const EMPTY_NODES: ReadonlySet<string> = new Set()

export class VrmAnimationPlayer {
  private readonly mixer: THREE.AnimationMixer
  private readonly actions = new Map<string, THREE.AnimationAction>()
  private readonly animated = new Map<string, ReadonlySet<string>>()
  private current: THREE.AnimationAction | null = null
  private currentName = ''
  private idleName = ''

  constructor(vrm: VRM) {
    this.mixer = new THREE.AnimationMixer(vrm.scene)
  }

  add(animation: LoadedVrmAnimation, { idle = false } = {}): void {
    const action = this.mixer.clipAction(animation.clip)
    action.clampWhenFinished = !idle
    action.loop = idle ? THREE.LoopRepeat : THREE.LoopOnce
    this.actions.set(animation.name, action)
    this.animated.set(animation.name, animation.animatedNodes)
    if (idle) this.idleName = animation.name
  }

  /** Bones the currently playing clip writes to, empty when nothing plays. */
  get animatedNodes(): ReadonlySet<string> {
    return (this.currentName && this.animated.get(this.currentName)) || EMPTY_NODES
  }

  has(name: string): boolean {
    return this.actions.has(name)
  }

  get active(): boolean {
    return this.current !== null
  }

  /** Start a clip, cross-fading from whatever is playing. Returns false if unknown. */
  play(name: string, { fade = 0.25 } = {}): boolean {
    const next = this.actions.get(name)
    if (!next) return false
    if (this.current === next) return true
    next.reset()
    next.enabled = true
    next.setEffectiveWeight(1)
    if (this.current) {
      this.current.crossFadeTo(next, fade, false)
      next.play()
    } else {
      next.fadeIn(fade).play()
    }
    this.current = next
    this.currentName = name
    return true
  }

  /** Return to the idle clip, or stop entirely when the package ships none. */
  rest({ fade = 0.3 } = {}): void {
    if (this.idleName && this.play(this.idleName, { fade })) return
    this.current?.fadeOut(fade)
    this.current = null
    this.currentName = ''
  }

  update(delta: number): void {
    this.mixer.update(delta)
  }

  /** Jump the playing clip to a time. Development harness only. */
  seek(seconds: number): void {
    if (!this.current) return
    this.current.time = seconds
    this.current.setEffectiveWeight(1)
    this.mixer.update(0)
  }

  destroy(): void {
    this.mixer.stopAllAction()
    for (const action of this.actions.values()) this.mixer.uncacheAction(action.getClip())
    this.actions.clear()
    this.animated.clear()
    this.current = null
    this.currentName = ''
  }
}
