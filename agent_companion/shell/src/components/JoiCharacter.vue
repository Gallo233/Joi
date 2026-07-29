<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { CharacterMotionName, CharacterMotionRequest } from '../characterMotion'
import { mountLive2D, type Live2DController, type Live2DEmotion, type Live2DRuntimeMapping } from '../live2d/runtime'
import type { VrmController } from '../vrm/runtime'

type CharacterModelType = 'static' | 'live2d' | 'vrm' | 'procedural3d'

const props = defineProps<{
  modelUrl: string
  modelType?: CharacterModelType
  fallbackImageSrc: string
  characterName: string
  emotion: Live2DEmotion
  speech: string
  motion?: CharacterMotionRequest
  compact: boolean
  accessoryStyle: Record<string, string>
  accessories: { hat: boolean; glasses: boolean; ears: boolean }
  runtimeMapping?: Live2DRuntimeMapping
}>()

const canvas = ref<HTMLCanvasElement | null>(null)
const live2dState = ref<'loading' | 'ready' | 'error'>('loading')
const live2dError = ref('')
const fallbackFailed = ref(false)
const activeMotionName = ref<CharacterMotionName>('idle')
let controller: Live2DController | VrmController | null = null
let resizeObserver: ResizeObserver | null = null
let resizeTimer: number | null = null
let fallbackMotionTimer: number | null = null
let mountGeneration = 0

const showFallbackImage = computed(() => Boolean(props.fallbackImageSrc && !fallbackFailed.value))
const modelLabel = computed(() => {
  if (props.modelType === 'procedural3d') return '3D'
  if (props.modelType === 'vrm') return 'VRM'
  return 'Live2D'
})

async function mountModel() {
  const target = canvas.value
  if (!target || (!props.modelUrl && props.modelType !== 'procedural3d')) {
    live2dState.value = 'error'
    return
  }
  const generation = ++mountGeneration
  live2dState.value = 'loading'
  live2dError.value = ''
  controller?.destroy()
  controller = null
  try {
    const nextController = props.modelType === 'procedural3d'
      ? await (await import('../character3d/runtime')).mountProceduralCharacter3D(target, props.runtimeMapping || {})
      : props.modelType === 'vrm'
        ? await (await import('../vrm/runtime')).mountVRM(target, props.modelUrl, props.runtimeMapping || {})
        : await mountLive2D(target, props.modelUrl, props.runtimeMapping || {})
    if (generation !== mountGeneration) {
      nextController.destroy()
      return
    }
    controller = nextController
    controller.setCompact(props.compact)
    controller.setEmotion(props.emotion)
    controller.speak(props.speech)
    playCharacterMotion(props.motion)
    live2dState.value = 'ready'
  } catch (error) {
    if (generation !== mountGeneration) return
    console.warn('[Joi Character] Falling back to the configured sprite:', error)
    live2dError.value = error instanceof Error ? error.message : String(error)
    live2dState.value = 'error'
  }
}

function scheduleLive2DResize() {
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(() => {
    resizeTimer = null
    controller?.resize()
  }, 160)
}

function playCharacterMotion(motion?: CharacterMotionRequest) {
  if (!motion) return
  if (fallbackMotionTimer !== null) window.clearTimeout(fallbackMotionTimer)
  activeMotionName.value = motion.motion
  controller?.playMotion(motion)
  if (!motion.loop && Number(motion.durationMs || 0) > 0) {
    fallbackMotionTimer = window.setTimeout(() => {
      fallbackMotionTimer = null
      activeMotionName.value = 'idle'
    }, Number(motion.durationMs))
  }
}

watch(() => [props.modelUrl, props.modelType], () => void mountModel())
watch(() => props.compact, (compact) => controller?.setCompact(compact))
watch(() => props.emotion, (emotion) => controller?.setEmotion(emotion))
watch(() => props.speech, (speech) => controller?.speak(speech))
watch(() => props.motion, (motion) => playCharacterMotion(motion), { deep: true })
watch(() => props.fallbackImageSrc, () => (fallbackFailed.value = false))

onMounted(() => {
  playCharacterMotion(props.motion)
  void mountModel()
  if (canvas.value?.parentElement) {
    resizeObserver = new ResizeObserver(scheduleLive2DResize)
    resizeObserver.observe(canvas.value.parentElement)
  }
})

onBeforeUnmount(() => {
  mountGeneration += 1
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
  if (fallbackMotionTimer !== null) window.clearTimeout(fallbackMotionTimer)
  resizeObserver?.disconnect()
  controller?.destroy()
  controller = null
})
</script>

<template>
  <div
    class="character-fit"
    :class="[{ 'live2d-ready': live2dState === 'ready' }, `motion-${activeMotionName}`]"
    :style="accessoryStyle"
    :data-live2d-state="live2dState"
    @dragstart.capture.prevent
    @selectstart.prevent
  >
    <canvas
      ref="canvas"
      class="character-live2d-canvas"
      :aria-label="`${characterName} ${modelLabel} 模型`"
      role="img"
    ></canvas>

    <div v-if="showFallbackImage" class="character-fallback-layer" aria-hidden="true">
      <img
        class="character-art"
        :src="fallbackImageSrc"
        alt=""
        draggable="false"
        @load="fallbackFailed = false"
        @error="fallbackFailed = true"
        @dragstart.prevent
        @mousedown.prevent
      />
    </div>

    <span v-if="live2dState === 'ready'" class="live2d-status-badge" aria-hidden="true">{{ modelLabel.toLocaleUpperCase() }}</span>
    <span class="sr-only" role="status" aria-live="polite">
      {{ live2dState === 'ready' ? `${modelLabel} 模型已就绪` : live2dState === 'error' ? `动态模型暂不可用。${live2dError}` : '正在加载角色模型' }}
    </span>

    <svg class="accessory-item wizard-hat" :style="{ display: accessories.hat ? 'block' : 'none' }" viewBox="0 0 140 100" fill="none" draggable="false" aria-hidden="true">
      <path d="M70 10 L40 65 L100 65 Z" fill="#4f46e5"/>
      <ellipse cx="70" cy="70" rx="60" ry="12" fill="#312e81"/>
      <path d="M48 50 Q70 45 92 50 L89 56 Q70 51 51 56 Z" fill="#facc15"/>
      <polygon points="70,18 73,26 81,26 74,31 77,39 70,34 63,39 66,31 59,26 67,26" fill="#facc15"/>
    </svg>

    <svg class="accessory-item glasses" :style="{ display: accessories.glasses ? 'block' : 'none' }" viewBox="0 0 100 30" fill="none" draggable="false" aria-hidden="true">
      <rect x="10" y="5" width="30" height="20" rx="3" fill="#111827"/>
      <rect x="60" y="5" width="30" height="20" rx="3" fill="#111827"/>
      <rect x="40" y="12" width="20" height="6" fill="#111827"/>
      <rect x="15" y="10" width="8" height="3" fill="#ffffff" opacity="0.7"/>
      <rect x="65" y="10" width="8" height="3" fill="#ffffff" opacity="0.7"/>
    </svg>

    <svg class="accessory-item bunny-ears" :style="{ display: accessories.ears ? 'block' : 'none' }" viewBox="0 0 130 80" fill="none" draggable="false" aria-hidden="true">
      <ellipse cx="40" cy="40" rx="14" ry="35" transform="rotate(-15 40 40)" fill="#fbcfe8"/>
      <ellipse cx="38" cy="40" rx="8" ry="25" transform="rotate(-15 38 40)" fill="#f472b6"/>
      <ellipse cx="90" cy="40" rx="14" ry="35" transform="rotate(15 90 40)" fill="#fbcfe8"/>
      <ellipse cx="92" cy="40" rx="8" ry="25" transform="rotate(15 92 40)" fill="#f472b6"/>
    </svg>
  </div>
</template>

<style scoped>
.motion-greet .character-fallback-layer {
  animation: fallback-greet 620ms ease-in-out 3;
  transform-origin: 50% 82%;
}

.motion-talk .character-fallback-layer {
  animation: fallback-talk 520ms ease-in-out infinite alternate;
  transform-origin: 50% 84%;
}

.motion-happy .character-fallback-layer {
  animation: fallback-happy 460ms cubic-bezier(.22, .8, .3, 1) 4;
  transform-origin: 50% 86%;
}

.motion-finger_gun .character-fallback-layer {
  animation: fallback-finger-gun 520ms cubic-bezier(.2, .8, .24, 1) 2 alternate;
  transform-origin: 50% 78%;
}

.motion-dance .character-fallback-layer {
  animation: fallback-dance 760ms ease-in-out infinite;
  transform-origin: 50% 88%;
}

@keyframes fallback-greet {
  0%, 100% { transform: rotate(0deg) translateY(0); }
  35% { transform: rotate(-4deg) translateY(-2px); }
  70% { transform: rotate(3deg) translateY(-1px); }
}

@keyframes fallback-talk {
  from { transform: rotate(-1deg) translateY(0); }
  to { transform: rotate(1deg) translateY(-2px); }
}

@keyframes fallback-happy {
  0%, 100% { transform: translateY(0) scale(1); }
  45% { transform: translateY(-9px) scale(1.025); }
}

@keyframes fallback-finger-gun {
  from { transform: translateX(0) rotate(0deg); }
  to { transform: translateX(7px) rotate(2deg); }
}

@keyframes fallback-dance {
  0%, 100% { transform: translate(0, 0) rotate(-2.5deg); }
  25% { transform: translate(-7px, -6px) rotate(2deg); }
  50% { transform: translate(0, 0) rotate(3deg); }
  75% { transform: translate(7px, -6px) rotate(-2deg); }
}

@media (prefers-reduced-motion: reduce) {
  .character-fallback-layer {
    animation: none !important;
  }
}
</style>
