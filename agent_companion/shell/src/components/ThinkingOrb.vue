<script setup lang="ts">
/**
 * The character's "I am doing something" indicator.
 *
 * Three bouncing dots say only "wait". These orbs say *what kind* of waiting:
 * searching sweeps a globe, listening rolls a waveform, connecting wires a
 * constellation. That maps onto phases Joi already reports, so the animation
 * carries real state instead of decorating a pause -- which is the same rule
 * the rest of the shell follows, that presentation must not invent status the
 * system does not have.
 *
 * The painters come from `thinking-orbs`; only the React wrapper was dropped.
 * The canvas, the clock and the theme are owned here.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { MODE_DRAWS, resolvePreset } from '../vendor/thinkingOrbs.js'

type OrbState =
  | 'working' | 'searching' | 'solving' | 'listening'
  | 'connecting' | 'weaving' | 'composing' | 'breathing' | 'shaping'

const props = withDefaults(defineProps<{
  state?: OrbState
  /** Upstream ships two hand-tuned designs, not one design scaled. */
  size?: 64 | 20
  speed?: number
  paused?: boolean
  label?: string
}>(), { state: 'working', size: 20, speed: 1, paused: false, label: '' })

const canvas = ref<HTMLCanvasElement | null>(null)
let frame = 0
let started = 0

// A companion that animates forever in the corner of the eye is a companion
// people turn off. Honour the OS preference rather than deciding for them.
const reducedMotion = ref(false)
let motionQuery: MediaQueryList | null = null
const onMotionChange = (event: MediaQueryListEvent) => { reducedMotion.value = event.matches }

const dark = ref(false)
let themeQuery: MediaQueryList | null = null
const onThemeChange = (event: MediaQueryListEvent) => { dark.value = event.matches }

const preset = computed(() => resolvePreset(props.state, props.size))

function paint(now: number) {
  const element = canvas.value
  const context = element?.getContext('2d')
  if (!element || !context) return
  // Redrawn at device resolution: a canvas sized only in CSS pixels renders
  // these dots visibly soft on a Retina display.
  const ratio = Math.min(3, window.devicePixelRatio || 1)
  const pixels = Math.round(props.size * ratio)
  if (element.width !== pixels) {
    element.width = pixels
    element.height = pixels
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, props.size, props.size)
  const { mode, speed, opts } = preset.value
  // Frozen at a representative moment rather than at zero, which for several
  // of these modes is a degenerate frame with the dots stacked.
  const elapsed = props.paused || reducedMotion.value ? 1.2 : (now - started) / 1000
  MODE_DRAWS[mode](context, props.size, elapsed * speed * props.speed, dark.value, opts)
}

function loop(now: number) {
  paint(now)
  frame = requestAnimationFrame(loop)
}

function start() {
  stop()
  started = performance.now()
  if (props.paused || reducedMotion.value) {
    // Still painted once, so a reduced-motion user sees the shape rather than
    // an empty square.
    requestAnimationFrame(paint)
    return
  }
  frame = requestAnimationFrame(loop)
}

function stop() {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
}

onMounted(() => {
  if (typeof matchMedia === 'function') {
    motionQuery = matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = motionQuery.matches
    motionQuery.addEventListener('change', onMotionChange)
    themeQuery = matchMedia('(prefers-color-scheme: dark)')
    dark.value = themeQuery.matches
    themeQuery.addEventListener('change', onThemeChange)
  }
  start()
})

onBeforeUnmount(() => {
  stop()
  motionQuery?.removeEventListener('change', onMotionChange)
  themeQuery?.removeEventListener('change', onThemeChange)
})

watch(() => [props.state, props.size, props.paused, reducedMotion.value], start)
</script>

<template>
  <canvas
    ref="canvas"
    class="thinking-orb"
    :style="{ width: `${props.size}px`, height: `${props.size}px` }"
    :role="props.label ? 'img' : undefined"
    :aria-label="props.label || undefined"
    :aria-hidden="props.label ? undefined : 'true'"
  />
</template>
