<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentEvent } from './protocol'

const input = ref('')
const connected = ref(false)
const events = ref<AgentEvent[]>([])
const activeCard = computed(() => [...events.value].reverse().find((event) => event.display_card))

function submit() {
  const text = input.value.trim()
  if (!text) return
  const now = Date.now() / 1000
  events.value.push({
    type: 'user_message',
    task_id: `local-${Date.now()}`,
    created_at: now,
    display_card: { title: '用户请求', summary: text, status: 'info' },
    voice_line: { text: '我收到了。', sprite: '1' },
    agent_state: { pending_core_bridge: true },
  })
  input.value = ''
}
</script>

<template>
  <main class="shell">
    <section class="workspace">
      <div class="topbar">
        <span class="brand">Agent Companion</span>
        <span class="status" :class="{ online: connected }">{{ connected ? 'Core online' : 'Core scaffold' }}</span>
      </div>
      <article class="task-card" v-if="activeCard">
        <header>
          <span>{{ activeCard.display_card.title }}</span>
          <small>{{ activeCard.type }}</small>
        </header>
        <p>{{ activeCard.display_card.summary }}</p>
        <pre v-if="activeCard.display_card.body">{{ activeCard.display_card.body }}</pre>
      </article>
      <div class="timeline">
        <div v-for="event in events" :key="`${event.task_id}-${event.created_at}`" class="event-row">
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
        <input v-model="input" placeholder="输入：帮我刷鸣潮日常 / 陪我看当前视频 / 修复项目 bug" />
        <button>发送</button>
      </form>
    </aside>
  </main>
</template>

