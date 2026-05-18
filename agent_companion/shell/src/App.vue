<script setup lang="ts">
import { convertFileSrc } from '@tauri-apps/api/core'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CoreClient, type CoreStatus } from './api'
import type { AgentEvent, CoreReadyPayload } from './protocol'

const input = ref('')
const status = ref<CoreStatus>('offline')
const errorText = ref('')
const events = ref<AgentEvent[]>([])
const developerMode = ref(false)
const ready = ref<CoreReadyPayload | null>(null)
const failedImageSrc = ref('')

const client = new CoreClient({
  url: 'ws://127.0.0.1:8765',
  onStatus: (value) => (status.value = value),
  onEvent: (event) => {
    events.value.push(event)
  },
  onReady: (payload) => {
    ready.value = payload
  },
  onVoiceAudio: (payload) => void playAudioPath(payload.voice_audio_path),
  onError: (message) => (errorText.value = message),
})

const connected = computed(() => status.value === 'online')
const connectionLabel = computed(() => {
  if (status.value === 'online') return 'Core online'
  if (status.value === 'connecting') return '连接中'
  return 'Core offline'
})

const userTextByTask = computed(() => {
  const rows = new Map<string, string>()
  for (const event of events.value) {
    if (event.type === 'user_message') rows.set(event.task_id, event.display_card.summary)
  }
  return rows
})

const pendingApproval = computed(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const event = events.value[index]
    if (event.type !== 'approval_required') continue
    const hasLaterEvent = events.value.slice(index + 1).some((later) => later.task_id === event.task_id)
    return hasLaterEvent ? undefined : event
  }
  return undefined
})

const chatRows = computed(() =>
  events.value.filter((event) => event.type === 'user_message' || isCompanionChat(event)),
)

const taskRows = computed(() => {
  const byTask = new Map<string, AgentEvent[]>()
  for (const event of events.value) {
    if (!isTaskCardEvent(event)) continue
    const rows = byTask.get(event.task_id) ?? []
    rows.push(event)
    byTask.set(event.task_id, rows)
  }

  return [...byTask.entries()]
    .map(([taskId, rows]) => {
      const latest = rows[rows.length - 1]
      const detail = [...rows]
        .reverse()
        .find((event) => event.display_card.body || event.display_card.artifacts?.length)
      return { taskId, latest, detail, rows }
    })
    .sort((a, b) => b.latest.created_at - a.latest.created_at)
})

const activeTask = computed(() => taskRows.value[0])

const latestSpeech = computed(() => {
  const latest = [...events.value]
    .reverse()
    .find((event) => event.voice_line?.text && isSpeakableEvent(event))
  return latest?.voice_line.text || '我在。要看、要玩、要写代码，都可以直接告诉我。'
})

const activeSpriteId = computed(() => {
  const latest = [...events.value]
    .reverse()
    .find((event) => event.voice_line?.sprite && isSpeakableEvent(event))
  return latest?.voice_line.sprite || '1'
})

const characterName = computed(() => ready.value?.character?.name || 'Joi')

const characterImageSrc = computed(() => {
  const sprites = ready.value?.character?.sprites || []
  const active = sprites.find((sprite) => sprite.id === activeSpriteId.value) || sprites[0]
  if (active?.image_data_url) return active.image_data_url
  return active?.image_path ? convertFileSrc(active.image_path) : ''
})

const currentMode = computed(() => {
  if (pendingApproval.value) return '等待确认'
  const latest = [...events.value].reverse().find((event) => intentName(event) || toolName(event))
  const intent = latest ? intentName(latest) : ''
  const tool = latest ? toolName(latest) : ''
  if (intent === 'game_assist' || tool === 'game.ok_ww.run') return '游戏'
  if (intent === 'coding' || tool === 'codex.run') return '写码'
  if (intent === 'computer_use' || tool.startsWith('computer.')) return '电脑操作'
  if (intent === 'watch_together' || intent === 'browser' || tool.startsWith('browser.')) return '陪看'
  return '闲聊'
})

function toolName(event: AgentEvent) {
  const tool = event.agent_state?.tool
  return typeof tool === 'string' ? tool : ''
}

function intentName(event: AgentEvent) {
  const intent = event.agent_state?.intent
  return typeof intent === 'string' ? intent : ''
}

function isCompanionChat(event: AgentEvent) {
  return toolName(event) === 'companion.chat' || (event.type === 'tool_completed' && event.display_card.title === '对话')
}

function isTaskCardEvent(event: AgentEvent) {
  if (event.type === 'user_message' || isCompanionChat(event)) return false
  return ['approval_required', 'tool_completed', 'tool_failed', 'task_completed', 'task_failed'].includes(event.type)
}

function isSpeakableEvent(event: AgentEvent) {
  if (event.type === 'user_message' || event.type === 'plan_created' || event.type === 'tool_started') return false
  const text = event.voice_line?.text || ''
  return !/[{}[\]"=]|task-|codex-|\.json|\.log|[A-Z]:\\/.test(text)
}

function taskGoal(taskId: string, fallback: string) {
  return userTextByTask.value.get(taskId) || fallback
}

function taskMeta(event: AgentEvent, detail?: AgentEvent) {
  const source = detail || event
  const state = source.agent_state || {}
  const meta: string[] = []
  const tool = toolName(source)
  if (tool.startsWith('browser.')) {
    const data = asRecord(state.data)
    const title = stringValue(data.title)
    const elements = Array.isArray(data.elements) ? data.elements.length : 0
    if (title) meta.push(`页面：${trimText(title, 18)}`)
    if (elements) meta.push(`可见元素：${elements}`)
    if (source.display_card.artifacts?.length) meta.push(`截图：${source.display_card.artifacts.length}`)
  }
  if (tool === 'observe.screen') {
    const observation = asRecord(state.observation)
    const title = stringValue(observation.title)
    const width = Number(observation.width || 0)
    const height = Number(observation.height || 0)
    if (title) meta.push(`窗口：${trimText(title, 18)}`)
    if (width && height) meta.push(`尺寸：${width}x${height}`)
    if (source.display_card.artifacts?.length) meta.push(`截图：${source.display_card.artifacts.length}`)
  }
  if (tool === 'codex.run') meta.push(event.display_card.status === 'success' ? '代码任务完成' : '代码任务')
  if (tool === 'game.ok_ww.run') meta.push('游戏技能')
  if (tool.startsWith('computer.')) meta.push(computerActionLabel(tool))
  return meta
}

function computerActionLabel(tool: string) {
  const labels: Record<string, string> = {
    'computer.click': '点击',
    'computer.type_text': '输入',
    'computer.scroll': '滚动',
    'computer.hotkey': '快捷键',
  }
  return labels[tool] || '电脑操作'
}

function artifactLabel(artifact: string, index: number) {
  const value = artifact.toLowerCase()
  if (/\.(png|jpg|jpeg|webp)$/.test(value)) return `截图 ${index + 1}`
  if (/\.(jsonl|json)$/.test(value)) return `事件记录 ${index + 1}`
  if (/\.(log|txt)$/.test(value)) return `日志 ${index + 1}`
  return `附件 ${index + 1}`
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function trimText(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function eventTime(event: AgentEvent) {
  return new Date(event.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

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
        <div>
          <span class="brand">Joi</span>
          <span class="mode">{{ currentMode }}</span>
        </div>
        <div class="top-actions">
          <button type="button" class="ghost-button" @click="developerMode = !developerMode">
            {{ developerMode ? '隐藏调试' : '开发者' }}
          </button>
          <span class="status" :class="{ online: connected }">{{ connectionLabel }}</span>
        </div>
      </div>

      <p class="error" v-if="errorText">{{ errorText }}</p>

      <section class="hero-panel" v-if="!taskRows.length && !chatRows.length">
        <p class="eyebrow">Joi Agent</p>
        <h1>把任务直接交给角色。</h1>
        <p>当前优先打通写码、陪看和游戏三条闭环。你说目标，Joi 会用任务卡展示执行结果，语音只播报自然短句。</p>
      </section>

      <section class="task-section" v-if="taskRows.length">
        <div class="section-title">
          <h2>任务</h2>
          <span>{{ taskRows.length }} 个</span>
        </div>
        <article
          v-for="task in taskRows.slice(0, 5)"
          :key="task.taskId"
          class="task-card"
          :class="task.latest.display_card.status || 'info'"
        >
          <header>
            <span>{{ task.latest.display_card.title }}</span>
            <small>{{ task.latest.display_card.status || 'info' }}</small>
          </header>
          <p class="task-goal">{{ taskGoal(task.taskId, task.latest.display_card.summary) }}</p>
          <p>{{ task.latest.display_card.summary }}</p>
          <div class="task-meta" v-if="taskMeta(task.latest, task.detail).length">
            <span v-for="item in taskMeta(task.latest, task.detail)" :key="item">{{ item }}</span>
          </div>
          <details v-if="task.detail?.display_card.body || task.detail?.display_card.artifacts?.length">
            <summary>结果详情</summary>
            <pre v-if="task.detail?.display_card.body">{{ task.detail.display_card.body }}</pre>
            <div class="artifacts" v-if="task.detail?.display_card.artifacts?.length">
              <span
                v-for="(artifact, index) in task.detail.display_card.artifacts"
                :key="artifact"
                :title="artifact"
              >
                {{ artifactLabel(artifact, index) }}
              </span>
            </div>
          </details>
          <div class="approval-actions" v-if="task.latest.type === 'approval_required' && pendingApproval?.task_id === task.taskId">
            <button type="button" @click="resolveApproval(true)">允许执行</button>
            <button type="button" class="secondary" @click="resolveApproval(false)">停在这里</button>
          </div>
        </article>
      </section>

      <section class="chat-section" v-if="chatRows.length">
        <div class="section-title">
          <h2>对话</h2>
          <span>最近 {{ Math.min(chatRows.length, 8) }} 条</span>
        </div>
        <div class="chat-list">
          <div
            v-for="event in chatRows.slice(-8)"
            :key="`${event.task_id}-${event.created_at}`"
            class="chat-row"
            :class="{ user: event.type === 'user_message' }"
          >
            <span class="chat-name">{{ event.type === 'user_message' ? '你' : 'Joi' }}</span>
            <p>{{ event.display_card.summary }}</p>
          </div>
        </div>
      </section>

      <section class="debug-section" v-if="developerMode">
        <div class="section-title">
          <h2>开发者事件</h2>
          <span>{{ events.length }} 条</span>
        </div>
        <div class="debug-list">
          <div v-for="event in events.slice(-18).reverse()" :key="`${event.task_id}-${event.created_at}`" class="debug-row">
            <span>{{ eventTime(event) }}</span>
            <strong>{{ event.type }}</strong>
            <code>{{ toolName(event) || intentName(event) || event.display_card.status }}</code>
            <p>{{ event.display_card.summary }}</p>
          </div>
        </div>
      </section>
    </section>

    <aside class="stage">
      <div class="stage-top">
        <span>Joi Companion</span>
        <strong>{{ activeTask?.latest.display_card.status || currentMode }}</strong>
      </div>
      <div class="scene-line"></div>
      <div class="character">
        <img
          v-if="characterImageSrc && failedImageSrc !== characterImageSrc"
          class="character-art"
          :src="characterImageSrc"
          alt=""
          @load="failedImageSrc = ''"
          @error="failedImageSrc = characterImageSrc"
        />
        <div v-else class="character-fallback">{{ characterName.slice(0, 1) }}</div>
      </div>
      <div class="speech">
        <strong>{{ characterName }}</strong>
        <span>{{ latestSpeech }}</span>
      </div>
      <form class="composer" @submit.prevent="submit">
        <input v-model="input" :disabled="!connected" placeholder="输入：帮我刷鸣潮日常 / 陪我看当前视频 / 修复项目 bug" />
        <button :disabled="!connected">发送</button>
      </form>
    </aside>
  </main>
</template>
