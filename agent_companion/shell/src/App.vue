<script setup lang="ts">
import { convertFileSrc } from '@tauri-apps/api/core'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CoreClient, type CoreStatus } from './api'
import type { AgentEvent } from './protocol'

const input = ref('')
const status = ref<CoreStatus>('offline')
const errorText = ref('')
const events = ref<AgentEvent[]>([])
const client = new CoreClient({
  url: 'ws://127.0.0.1:8765',
  onStatus: (value) => (status.value = value),
  onEvent: (event) => {
    events.value.push(event)
  },
  onVoiceAudio: (payload) => void playAudioPath(payload.voice_audio_path),
  onError: (message) => (errorText.value = message),
})
const connected = computed(() => status.value === 'online')
const visibleEvents = computed(() => events.value.filter((event) => event.type !== 'plan_created' && event.type !== 'tool_started'))
const activeCard = computed(
  () =>
    [...visibleEvents.value].reverse().find((event) => event.display_card && event.type !== 'user_message') ??
    [...visibleEvents.value].reverse().find((event) => event.display_card),
)
const pendingApproval = computed(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const event = events.value[index]
    if (event.type !== 'approval_required') continue
    const hasLaterEvent = events.value.slice(index + 1).some((later) => later.task_id === event.task_id)
    return hasLaterEvent ? undefined : event
  }
  return undefined
})

function submit() {
  const text = input.value.trim()
  if (!text) return
  client.sendUserText(text)
  input.value = ''
}

function resolveApproval(approved: boolean) {
  if (!pendingApproval.value) return
  client.resolveApproval(pendingApproval.value.task_id, approved)
}

async function playAudioPath(path?: string) {
  if (!path) return
  try {
    const url = convertFileSrc(path)
    const audio = new Audio(url)
    await audio.play()
  } catch {
    // Text remains visible when local audio is unavailable.
  }
}

onMounted(() => client.connect())
onBeforeUnmount(() => client.close())
</script>

<template>
  <main class="shell">
    <section class="workspace">
      <div class="topbar">
        <span class="brand">Joi</span>
        <span class="status" :class="{ online: connected }">{{ connected ? 'Core online' : status }}</span>
      </div>
      <p class="error" v-if="errorText">{{ errorText }}</p>
      <article class="task-card" v-if="activeCard">
        <header>
          <span>{{ activeCard.display_card.title }}</span>
          <small>{{ activeCard.display_card.status || 'info' }}</small>
        </header>
        <p>{{ activeCard.display_card.summary }}</p>
        <pre v-if="activeCard.display_card.body">{{ activeCard.display_card.body }}</pre>
        <div class="approval-actions" v-if="activeCard.type === 'approval_required'">
          <button type="button" @click="resolveApproval(true)">允许执行</button>
          <button type="button" class="secondary" @click="resolveApproval(false)">停在这里</button>
        </div>
        <div class="artifacts" v-if="activeCard.display_card.artifacts?.length">
          <span v-for="artifact in activeCard.display_card.artifacts" :key="artifact">{{ artifact }}</span>
        </div>
      </article>
      <div class="timeline">
        <div v-for="event in visibleEvents" :key="`${event.task_id}-${event.created_at}`" class="event-row">
          <strong>{{ event.display_card.title }}</strong>
          <span>{{ event.display_card.summary }}</span>
        </div>
      </div>
    </section>

    <aside class="stage">
      <div class="background"></div>
      <div class="character">
        <div class="face">澪</div>
      </div>
      <div class="speech">
        <strong>星野澪</strong>
        <span>{{ activeCard?.voice_line.text || '我在。要看、要玩、要写代码，都可以直接告诉我。' }}</span>
      </div>
      <form class="composer" @submit.prevent="submit">
        <input v-model="input" :disabled="!connected" placeholder="输入：帮我刷鸣潮日常 / 陪我看当前视频 / 修复项目 bug" />
        <button :disabled="!connected">发送</button>
      </form>
    </aside>
  </main>
</template>
