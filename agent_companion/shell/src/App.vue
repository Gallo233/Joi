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
const previewArtifact = ref('')
const voiceState = ref<'idle' | 'recording' | 'transcribing'>('idle')
const lastTranscript = ref('')
let mediaRecorder: MediaRecorder | null = null
let mediaStream: MediaStream | null = null
let audioChunks: Blob[] = []
let voiceStopTimer: number | null = null
let currentAudio: HTMLAudioElement | null = null

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
  if (intent === 'watch_together' || intent === 'watch_followup' || intent === 'browser' || tool === 'watch.recall' || tool.startsWith('browser.')) return '陪看'
  return '闲聊'
})
const asrConfigured = computed(() => Boolean(ready.value?.asr?.configured))
const voiceMaxSeconds = computed(() => Math.max(1, Number(ready.value?.asr?.max_seconds || 30)))
const voiceMaxBytes = computed(() => Math.max(1024, Number(ready.value?.asr?.max_bytes || 12 * 1024 * 1024)))
const voiceButtonLabel = computed(() => {
  if (!asrConfigured.value) return 'ASR 未配置'
  if (voiceState.value === 'recording') return '停止'
  if (voiceState.value === 'transcribing') return '转写中'
  return '语音'
})
const previewArtifactSrc = computed(() => (previewArtifact.value ? artifactSrc(previewArtifact.value) : ''))
const voiceStatusText = computed(() => {
  if (!asrConfigured.value) return 'ASR 未配置，请先在 config.yaml 中启用语音识别。'
  if (voiceState.value === 'recording') return `录音中，最长 ${voiceMaxSeconds.value} 秒。`
  if (voiceState.value === 'transcribing') return '转写中...'
  if (lastTranscript.value) return `识别：${lastTranscript.value}`
  return ''
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
    const modelStatus = stringValue(state.model_status)
    if (modelStatus) meta.push(visionStatusLabel(modelStatus))
    if (source.display_card.artifacts?.length) meta.push(`截图：${source.display_card.artifacts.length}`)
  }
  if (tool === 'watch.recall') {
    const modelStatus = stringValue(state.model_status)
    if (modelStatus) meta.push(visionStatusLabel(modelStatus))
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

function visionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    ok: '视觉模型：已启用',
    vision_context: '视觉上下文',
    unconfigured: '视觉模型：未配置',
    vision_unconfigured: '视觉模型：未配置',
    error: '视觉模型：失败',
    vision_error: '视觉模型：失败',
    no_context: '暂无视觉上下文',
    llm_answer: '角色理解',
  }
  return labels[status] || '视觉状态'
}

function artifactLabel(artifact: string, index: number) {
  const value = artifact.toLowerCase()
  if (/\.(png|jpg|jpeg|webp)$/.test(value)) return `截图 ${index + 1}`
  if (/\.(jsonl|json)$/.test(value)) return `事件记录 ${index + 1}`
  if (/\.(log|txt)$/.test(value)) return `日志 ${index + 1}`
  return `附件 ${index + 1}`
}

function isImageArtifact(artifact: string) {
  return /\.(png|jpg|jpeg|webp)$/i.test(artifact)
}

function artifactPath(artifact: string) {
  if (/^[a-zA-Z]:[\\/]/.test(artifact) || artifact.startsWith('/')) return artifact
  const workspace = ready.value?.workspace || ''
  if (!workspace) return artifact
  const separator = workspace.includes('\\') ? '\\' : '/'
  return `${workspace.replace(/[\\/]$/, '')}${separator}${artifact.replace(/[\\/]/g, separator)}`
}

function artifactSrc(artifact: string) {
  return convertFileSrc(artifactPath(artifact))
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
  stopSpokenAudio()
  void client.sendUserText(text).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '发送失败'
  })
  input.value = ''
}

function resolveApproval(approved: boolean) {
  if (!pendingApproval.value) return
  const approvalId = approvalIdFor(pendingApproval.value)
  if (!approvalId) return
  void client.resolveApproval(approvalId, approved).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '审批提交失败'
  })
}

function approvalIdFor(event: AgentEvent) {
  const approval = asRecord(event.agent_state?.approval)
  return stringValue(approval.approval_id)
}

async function playAudioPath(path?: string) {
  if (!path) return
  let audio: HTMLAudioElement | null = null
  try {
    stopSpokenAudio()
    const url = convertFileSrc(path)
    audio = new Audio(url)
    currentAudio = audio
    audio.onended = () => {
      if (currentAudio === audio) currentAudio = null
    }
    await audio.play()
  } catch {
    if (audio && currentAudio === audio) currentAudio = null
    // Text remains visible when local audio is unavailable.
  }
}

function stopSpokenAudio() {
  if (!currentAudio) return
  currentAudio.pause()
  currentAudio.currentTime = 0
  currentAudio = null
}

async function toggleVoiceInput() {
  if (voiceState.value === 'recording') {
    stopVoiceRecording()
    return
  }
  if (voiceState.value !== 'idle' || !connected.value) return
  await startVoiceRecording()
}

async function startVoiceRecording() {
  try {
    if (!asrConfigured.value) {
      errorText.value = 'ASR 未配置'
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      errorText.value = '当前 WebView 不支持麦克风录音'
      return
    }
    stopSpokenAudio()
    lastTranscript.value = ''
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    audioChunks = []
    mediaRecorder = new MediaRecorder(mediaStream)
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunks.push(event.data)
    }
    mediaRecorder.onstop = () => {
      const mimeType = mediaRecorder?.mimeType || 'audio/webm'
      const blob = new Blob(audioChunks, { type: mimeType })
      audioChunks = []
      cleanupVoiceStream()
      void transcribeVoiceBlob(blob, mimeType)
    }
    mediaRecorder.start()
    voiceStopTimer = window.setTimeout(() => stopVoiceRecording(), voiceMaxSeconds.value * 1000)
    voiceState.value = 'recording'
  } catch {
    cleanupVoiceStream()
    voiceState.value = 'idle'
    errorText.value = '麦克风启动失败'
  }
}

function stopVoiceRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    voiceState.value = 'transcribing'
    mediaRecorder.stop()
    return
  }
  cleanupVoiceStream()
  voiceState.value = 'idle'
}

function cleanupVoiceStream() {
  if (voiceStopTimer !== null) {
    window.clearTimeout(voiceStopTimer)
    voiceStopTimer = null
  }
  mediaStream?.getTracks().forEach((track) => track.stop())
  mediaStream = null
  mediaRecorder = null
}

async function transcribeVoiceBlob(blob: Blob, mimeType: string) {
  try {
    if (blob.size <= 0) {
      errorText.value = '我没有录到声音，请再说一次。'
      return
    }
    if (blob.size > voiceMaxBytes.value) {
      errorText.value = '这段语音太长了，我没有发送出去。'
      return
    }
    const audioBase64 = await blobToBase64(blob)
    const result = (await client.transcribeVoice(audioBase64, mimeType)) as { ok?: boolean; transcript?: string; error?: string; message?: string }
    if (result.ok && result.transcript) lastTranscript.value = result.transcript
    if (!result.ok) errorText.value = result.message || result.error || '没有识别到语音'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '语音转写失败'
  } finally {
    voiceState.value = 'idle'
  }
}

function blobToBase64(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || '').split(',', 2)[1] || '')
    reader.onerror = () => reject(new Error('音频读取失败'))
    reader.readAsDataURL(blob)
  })
}

onMounted(() => client.connect())
onBeforeUnmount(() => {
  cleanupVoiceStream()
  stopSpokenAudio()
  client.close()
})
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
              <button
                v-for="(artifact, index) in task.detail.display_card.artifacts.filter(isImageArtifact)"
                :key="`image-${artifact}`"
                class="artifact-thumb"
                type="button"
                :title="artifactLabel(artifact, index)"
                @click="previewArtifact = artifact"
              >
                <img :src="artifactSrc(artifact)" alt="" />
                <span>{{ artifactLabel(artifact, index) }}</span>
              </button>
              <span
                v-for="(artifact, index) in task.detail.display_card.artifacts"
                v-show="!isImageArtifact(artifact)"
                :key="`file-${artifact}`"
                :title="artifactLabel(artifact, index)"
              >
                {{ artifactLabel(artifact, index) }}
              </span>
            </div>
          </details>
          <div class="approval-actions" v-if="task.latest.type === 'approval_required' && pendingApproval?.task_id === task.taskId && approvalIdFor(task.latest)">
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
      <div class="voice-status" v-if="voiceStatusText">{{ voiceStatusText }}</div>
      <form class="composer" @submit.prevent="submit">
        <button
          type="button"
          class="mic-button"
          :class="{ recording: voiceState === 'recording' }"
          :disabled="!connected || !asrConfigured || voiceState === 'transcribing'"
          @click="toggleVoiceInput"
        >
          {{ voiceButtonLabel }}
        </button>
        <input v-model="input" :disabled="!connected" placeholder="输入：帮我刷鸣潮日常 / 陪我看当前视频 / 修复项目 bug" />
        <button :disabled="!connected">发送</button>
      </form>
    </aside>

    <div class="artifact-modal" v-if="previewArtifact" @click.self="previewArtifact = ''">
      <div class="artifact-modal-body">
        <button type="button" class="modal-close" @click="previewArtifact = ''">关闭</button>
        <img :src="previewArtifactSrc" alt="" />
      </div>
    </div>
  </main>
</template>
