<script setup lang="ts">
import { convertFileSrc } from '@tauri-apps/api/core'
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CoreClient, type CoreStatus } from './api'
import type { AgentEvent, ArtifactReadResult, ComputerUseAuditArtifact, ComputerUseAuditEvent, CoreReadyPayload, MemoryStatus, RuntimeConfigMutationResult, RuntimeProviderStatus, VoiceAudioPayload, WatchLoopStatus } from './protocol'
import { asrRpcTimeoutMs, nextVoiceEpoch, shouldPlayVoiceAudio, voiceAudioKey } from './voiceRuntime'

const input = ref('')
const status = ref<CoreStatus>('offline')
const errorText = ref('')
const events = ref<AgentEvent[]>([])
const developerMode = ref(false)
const ready = ref<CoreReadyPayload | null>(null)
const failedImageSrc = ref('')
const previewArtifact = ref('')
const previewArtifactEvent = ref<AgentEvent | null>(null)
const activeCabin = ref<'workspace' | 'chat' | 'inspector'>('workspace')
const artifactDialog = ref<HTMLDialogElement | null>(null)
const memoryStatus = ref<MemoryStatus | null>(null)

const isCompactMode = ref(false)
const equippedAccessories = ref({ hat: false, glasses: false, ears: false })
const miniSpeechActive = ref(false)
const miniDashboardActive = ref(false)
const watchTranscriptSource = ref<'system_audio' | 'ocr_subtitle' | 'auto'>('system_audio')
const watchProactiveEnabled = ref(true)
const watchCommentaryInterval = ref(30)
const watchVisionInterval = ref(5)
let miniSpeechTimer: number | null = null

async function toggleCompactMode() {
  const nextCompactMode = !isCompactMode.value
  isCompactMode.value = nextCompactMode
  clearMiniSpeechTimer()
  miniSpeechActive.value = false
  miniDashboardActive.value = false
  document.body.classList.toggle('transparent-active', nextCompactMode)
  await applyWindowShellMode(nextCompactMode)
}

async function applyWindowShellMode(compact: boolean) {
  try {
    const appWindow = getCurrentWindow()
    if (compact) {
      await safeWindowCall(() => appWindow.setShadow(false))
      await safeWindowCall(() => appWindow.setAlwaysOnTop(true))
      await safeWindowCall(() => appWindow.setSkipTaskbar(true))
      await safeWindowCall(() => appWindow.setResizable(false))
      await safeWindowCall(() => appWindow.setSize(compactWindowSize()))
      return
    }
    await safeWindowCall(() => appWindow.setSize(new LogicalSize(1080, 780)))
    await safeWindowCall(() => appWindow.setResizable(true))
    await safeWindowCall(() => appWindow.setSkipTaskbar(false))
    await safeWindowCall(() => appWindow.setAlwaysOnTop(false))
    await safeWindowCall(() => appWindow.setShadow(true))
  } catch (e) {
    // Browser preview fallback.
  }
}

function compactWindowSize() {
  if (miniDashboardActive.value && miniSpeechActive.value && miniBubbleHasActions.value) return new LogicalSize(390, 540)
  if (miniDashboardActive.value && miniSpeechActive.value) return new LogicalSize(380, 520)
  if (miniSpeechActive.value && miniBubbleHasActions.value) return new LogicalSize(360, 460)
  if (miniDashboardActive.value || miniSpeechActive.value) return new LogicalSize(360, 430)
  return new LogicalSize(300, 340)
}

async function syncCompactWindowSize() {
  if (!isCompactMode.value) return
  await safeWindowCall(() => getCurrentWindow().setSize(compactWindowSize()))
}

async function safeWindowCall(action: () => Promise<void>) {
  try {
    await action()
  } catch (e) {
    // Some window APIs are unavailable in browser preview or unsupported platforms.
  }
}

async function closeWindow() {
  try {
    await getCurrentWindow().close()
  } catch (e) {
    // Browser preview fallback: the button is decorative when no Tauri shell is present.
  }
}

async function minimizeWindow() {
  try {
    await getCurrentWindow().minimize()
  } catch (e) {
    // Browser preview fallback.
  }
}

async function toggleMaximizeWindow() {
  try {
    const appWindow = getCurrentWindow()
    if (await appWindow.isMaximized()) {
      await appWindow.unmaximize()
    } else {
      await appWindow.maximize()
    }
  } catch (e) {
    // Browser preview fallback.
  }
}

async function startWindowDrag(event: MouseEvent) {
  if (event.button !== 0) return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, input, select, textarea, a, [role="button"], .topbar-actions, .traffic-lights')) return
  try {
    await getCurrentWindow().startDragging()
  } catch (e) {
    // Browser preview fallback.
  }
}

function startMascotDrag(event: MouseEvent) {
  if (!isCompactMode.value || event.button !== 0) return
  if ((event.target as HTMLElement | null)?.closest('.mini-speech-bubble, .mini-control-dashboard')) return
  event.preventDefault()
  event.stopPropagation()
  if (event.detail >= 2) {
    clearMascotClickTimer()
    void toggleCompactMode()
    return
  }
  stopMascotDragWatch()
  mascotDragMoved = false
  mascotDragStart = { x: event.screenX, y: event.screenY }
  window.addEventListener('mousemove', maybeStartMascotDrag)
  window.addEventListener('mouseup', stopMascotDragWatch, { once: true })
}

async function maybeStartMascotDrag(event: MouseEvent) {
  if (!mascotDragStart) return
  const distance = Math.hypot(event.screenX - mascotDragStart.x, event.screenY - mascotDragStart.y)
  if (distance < 6) return
  event.preventDefault()
  event.stopPropagation()
  mascotDragMoved = true
  stopMascotDragWatch()
  try {
    await getCurrentWindow().startDragging()
  } catch (e) {
    // Browser preview fallback.
  }
}

function stopMascotDragWatch() {
  mascotDragStart = null
  window.removeEventListener('mousemove', maybeStartMascotDrag)
}

function handleMascotClick(event: MouseEvent) {
  if (!isCompactMode.value) return
  if (event.detail >= 2) {
    clearMascotClickTimer()
    void toggleCompactMode()
    return
  }
  if (mascotDragMoved) {
    mascotDragMoved = false
    return
  }
  clearMascotClickTimer()
  mascotClickTimer = window.setTimeout(() => {
    mascotClickTimer = null
    if (!isCompactMode.value) return
    miniDashboardActive.value = !miniDashboardActive.value
    void syncCompactWindowSize()
  }, 220)
}

function handleMascotDoubleClick() {
  clearMascotClickTimer()
  if (isCompactMode.value) void toggleCompactMode()
}

function clearMascotClickTimer() {
  if (mascotClickTimer === null) return
  window.clearTimeout(mascotClickTimer)
  mascotClickTimer = null
}

function preventNativeAssetDrag(event: DragEvent) {
  if ((event.target as HTMLElement | null)?.closest('.character, .stage')) {
    event.preventDefault()
    event.stopPropagation()
  }
}

function preventCompactSelection(event: Event) {
  if (!isCompactMode.value) return
  if ((event.target as HTMLElement | null)?.closest('.character')) {
    event.preventDefault()
  }
}

function toggleAccessory(acc: 'hat' | 'glasses' | 'ears') {
  equippedAccessories.value[acc] = !equippedAccessories.value[acc]
}

watch(previewArtifact, (newVal) => {
  if (newVal) {
    artifactDialog.value?.showModal()
  } else {
    artifactDialog.value?.close()
  }
})

const voiceState = ref<'idle' | 'recording' | 'transcribing'>('idle')
const lastTranscript = ref('')
const lastTtsError = ref('')
const nowSeconds = ref(Date.now() / 1000)
const runtimeDraft = ref(defaultRuntimeDraft())
const runtimeDraftDirty = ref(false)
const runtimePreview = ref<RuntimeConfigMutationResult | null>(null)
const runtimePreviewLoading = ref(false)
const runtimeApplyLoading = ref(false)
const artifactDataUrls = ref<Record<string, string>>({})
const artifactLoadFailed = ref<Record<string, boolean>>({})
let mediaRecorder: MediaRecorder | null = null
let mediaStream: MediaStream | null = null
let audioChunks: Blob[] = []
let voiceStopTimer: number | null = null
let clockTimer: number | null = null
let currentAudio: HTMLAudioElement | null = null
let voiceEpoch = 0
const taskVoiceEpochs = new Map<string, number>()
const voiceEventEpochs = new Map<string, number>()
const playedVoiceAudioKeys = new Set<string>()
let mascotDragStart: { x: number; y: number } | null = null
let mascotDragMoved = false
let mascotClickTimer: number | null = null

const client = new CoreClient({
  url: 'ws://127.0.0.1:8765',
  onStatus: (value) => (status.value = value),
  onEvent: (event) => {
    rememberVoiceEventEpoch(event)
    events.value.push(event)
    syncMemoryFromEvent(event)
    preloadImageArtifacts(event)
  },
  onReady: (payload) => {
    ready.value = payload
    memoryStatus.value = payload.memory || memoryStatus.value
    syncRuntimeDraft(payload)
  },
  onVoiceAudio: (payload) => void playVoiceAudio(payload),
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

const latestWatchLoopStatus = computed<WatchLoopStatus | undefined>(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const loop = asRecord(events.value[index].agent_state?.watch_loop)
    if ('active' in loop || stringValue(loop.session_id)) return loop as WatchLoopStatus
  }
  return ready.value?.watch_loop
})

const watchLoopStatus = computed<WatchLoopStatus>(() => latestWatchLoopStatus.value || ready.value?.watch_loop || {})
const watchLoopActive = computed(() => Boolean(watchLoopStatus.value.active))
const watchLoopTranscript = computed(() => {
  const rows = Array.isArray(watchLoopStatus.value.rolling_transcript) && watchLoopStatus.value.rolling_transcript.length
    ? watchLoopStatus.value.rolling_transcript
    : Array.isArray(watchLoopStatus.value.last_transcript)
      ? watchLoopStatus.value.last_transcript
      : []
  return rows.filter(Boolean).slice(0, 3).join(' / ')
})
const watchLoopMeta = computed(() => {
  const status = watchLoopStatus.value
  const pieces: string[] = []
  const iterations = Number(status.iterations || 0)
  if (iterations) pieces.push(`${iterations} 次采样`)
  const windowSeconds = Number(status.transcript_window_seconds || 0)
  if (windowSeconds) pieces.push(`最近 ${Math.max(1, Math.round(windowSeconds / 60))} 分钟`)
  pieces.push(status.proactive_enabled === false ? '主动发言关闭' : '主动发言开启')
  const visionInterval = Number(status.vision_interval_ticks ?? watchVisionInterval.value)
  pieces.push(visionInterval <= 0 ? '视觉手动' : `视觉每 ${visionInterval} 轮`)
  if (status.transcript_source) {
    const configured = String(status.transcript_source)
    const active = String(status.active_transcript_source || configured)
    pieces.push(active && active !== configured ? `${sourceLabel(configured)}→${sourceLabel(active)}` : sourceLabel(configured))
  }
  if (status.transcript_status) pieces.push(String(status.transcript_status))
  if (status.last_error) pieces.push(errorLabel(String(status.last_error)))
  if (status.visual_status) pieces.push(`视觉 ${status.visual_status}`)
  return pieces.join(' · ') || '等待采样'
})
const watchLoopSourceHealth = computed(() => {
  const health = asRecord(watchLoopStatus.value.source_health)
  return Object.entries(health)
    .map(([source, raw]) => {
      const row = asRecord(raw)
      return `${sourceLabel(source)} ${Number(row.count || 0)} 段${stringValue(row.status) ? ` · ${stringValue(row.status)}` : ''}`
    })
    .slice(0, 3)
})
const pendingMemories = computed(() => (memoryStatus.value?.pending || []).filter((item) => item.status === 'pending'))
const recentMemories = computed(() => memoryStatus.value?.recent || [])
const memoryEnabled = computed(() => memoryStatus.value?.enabled !== false)
const topPendingMemory = computed(() => pendingMemories.value[0] || null)
const memoryAuthorizeText = computed(() => topPendingMemory.value?.text || '')

watch(watchLoopStatus, (status) => {
  const source = stringValue(status.transcript_source)
  if (source === 'system_audio' || source === 'ocr_subtitle' || source === 'auto') watchTranscriptSource.value = source
  if (typeof status.proactive_enabled === 'boolean') watchProactiveEnabled.value = status.proactive_enabled
  if (status.commentary_interval_seconds) watchCommentaryInterval.value = Number(status.commentary_interval_seconds)
  const visionInterval = Number(status.vision_interval_ticks)
  if (Number.isFinite(visionInterval)) watchVisionInterval.value = Math.max(0, visionInterval)
})

const latestSpeech = computed(() => {
  const latest = [...events.value]
    .reverse()
    .find((event) => event.voice_line?.text && isSpeakableEvent(event))
  return latest?.voice_line.text || '我在。要看、要玩、要写代码，都可以直接告诉我。'
})

const miniBubbleHasActions = computed(() => Boolean(isCompactMode.value && pendingApproval.value && approvalIdFor(pendingApproval.value)))

const miniBubbleText = computed(() => {
  const approval = pendingApproval.value
  if (!approval) return latestSpeech.value
  return approval.display_card.summary || latestSpeech.value
})

watch(latestSpeech, (newVal) => {
  if (newVal) showMiniSpeech()
})

watch(pendingApproval, (approval) => {
  if (approval && isCompactMode.value) showMiniSpeech()
})

function showMiniSpeech() {
  clearMiniSpeechTimer()
  miniSpeechActive.value = true
  void syncCompactWindowSize()
  if (pendingApproval.value) return
  miniSpeechTimer = window.setTimeout(() => {
    miniSpeechTimer = null
    miniSpeechActive.value = false
    void syncCompactWindowSize()
  }, 8000)
}

function clearMiniSpeechTimer() {
  if (miniSpeechTimer === null) return
  window.clearTimeout(miniSpeechTimer)
  miniSpeechTimer = null
}

const latestExpressionEvent = computed(() =>
  [...events.value]
    .reverse()
    .find((event) => {
      const sync = asRecord(event.agent_state?.expression_sync)
      return isSpeakableEvent(event) && (event.voice_line?.sprite || event.voice_line?.emotion || sync.sprite || sync.emotion)
    }),
)

const activeSpriteId = computed(() => {
  const latest = latestExpressionEvent.value
  const sync = asRecord(latest?.agent_state?.expression_sync)
  return stringValue(sync.sprite) || latest?.voice_line?.sprite || '1'
})

const activeExpressionEmotion = computed(() => {
  const latest = latestExpressionEvent.value
  const sync = asRecord(latest?.agent_state?.expression_sync)
  return expressionEmotionClass(stringValue(sync.emotion) || latest?.voice_line?.emotion || 'neutral')
})

const activeEmotionStatus = computed(() => ({
  emotion: activeExpressionEmotion.value,
  label: expressionEmotionLabel(activeExpressionEmotion.value),
  sprite: activeSpriteId.value,
}))

const activeSpriteMeta = computed(() => {
  const sprites = ready.value?.character?.sprites || []
  return sprites.find((sprite) => sprite.id === activeSpriteId.value) || sprites[0]
})

const accessoryFitStyle = computed(() => {
  const label = activeSpriteMeta.value?.label || ''
  const emotion = activeExpressionEmotion.value
  const hasHatAndEars = equippedAccessories.value.hat && equippedAccessories.value.ears
  const style: Record<string, string> = {
    '--acc-hat-top': '-14%',
    '--acc-hat-left': '50%',
    '--acc-hat-width': '42%',
    '--acc-hat-rotate': '0deg',
    '--acc-glasses-top': '35%',
    '--acc-glasses-left': '50%',
    '--acc-glasses-width': '34%',
    '--acc-glasses-rotate': '0deg',
    '--acc-ears-top': '-21%',
    '--acc-ears-left': '50%',
    '--acc-ears-width': '44%',
    '--acc-ears-rotate': '0deg',
  }
  if (label.includes('歪头') || label.includes('好奇') || emotion === 'thinking') {
    style['--acc-hat-left'] = '51.5%'
    style['--acc-glasses-left'] = '51%'
    style['--acc-glasses-top'] = '34%'
    style['--acc-hat-rotate'] = '2deg'
  }
  if (label.includes('低头') || label.includes('困倦') || label.includes('疲')) {
    style['--acc-hat-top'] = '-10%'
    style['--acc-glasses-top'] = '38%'
  }
  if (label.includes('兴奋') || emotion === 'happy') {
    style['--acc-ears-top'] = '-23%'
    style['--acc-ears-width'] = '46%'
  }
  if (hasHatAndEars) {
    style['--acc-hat-width'] = '38%'
    style['--acc-hat-top'] = '-10%'
    style['--acc-ears-width'] = '48%'
    style['--acc-ears-top'] = '-24%'
  }
  return style
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
  if (watchLoopActive.value) return '陪看'
  const latest = [...events.value].reverse().find((event) => intentName(event) || toolName(event))
  const intent = latest ? intentName(latest) : ''
  const tool = latest ? toolName(latest) : ''
  if (intent === 'game_assist' || tool === 'game.ok_ww.run') return '游戏'
  if (intent === 'coding' || tool === 'codex.run') return '写码'
  if (intent === 'computer_use' || tool.startsWith('computer.')) return '电脑操作'
  if (intent === 'semantic_target' || tool === 'vision.resolve_target') return '目标定位'
  if (intent === 'watch_together' || intent === 'watch_followup' || intent === 'browser' || tool === 'watch.recall' || tool.startsWith('browser.')) return '陪看'
  return '闲聊'
})
const currentSemanticSelectionId = computed(() => {
  const inactiveIds = new Set<string>()
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const event = events.value[index]
    const state = event.agent_state || {}
    const selectionId = stringValue(state.selection_id)
    if (selectionId && (state.selected_rank || event.type === 'approval_required' || state.selection_expired || state.selection_missing || state.selection_not_current)) {
      inactiveIds.add(selectionId)
      continue
    }
    if (selectionId && state.candidate_selection_required) {
      if (selectionExpired(event)) inactiveIds.add(selectionId)
      else if (!inactiveIds.has(selectionId)) return selectionId
    }
  }
  return ''
})
const asrConfigured = computed(() => Boolean(ready.value?.asr?.configured))
const voiceMaxSeconds = computed(() => Math.max(1, Number(ready.value?.asr?.max_seconds || 30)))
const voiceMaxBytes = computed(() => Math.max(1024, Number(ready.value?.asr?.max_bytes || 12 * 1024 * 1024)))
const voiceAsrTimeoutSeconds = computed(() => Math.max(1, Number(ready.value?.asr?.timeout_seconds || 30)))
const voiceTranscribeTimeoutMs = computed(() => asrRpcTimeoutMs(voiceAsrTimeoutSeconds.value))
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
  if (event.type === 'tool_started' && toolName(event) === 'codex.run') return true
  return ['approval_required', 'tool_completed', 'tool_failed', 'task_completed', 'task_failed'].includes(event.type)
}

function isSpeakableEvent(event: AgentEvent) {
  if (event.type === 'user_message' || event.type === 'plan_created' || event.type === 'tool_started' || event.type === 'audit_event') return false
  return isSafeVoiceText(event.voice_line?.text || '')
}

function isPlayableVoiceEvent(event: AgentEvent) {
  if (event.type === 'user_message' || event.type === 'plan_created' || event.type === 'audit_event') return false
  return isSafeVoiceText(event.voice_line?.text || '')
}

function isSafeVoiceText(text: string) {
  return !/[{}[\]"=]|task-|approval-|selection-|codex-|sk-|\/(?:Users|home|private|tmp|var|Volumes)\/|\.png|\.jpg|\.jpeg|\.webp|\.bmp|\.gif|\.ppm|\.json|\.jsonl|\.log|\.txt|\.yaml|\.yml|\.gguf|\.safetensors|\.ckpt|\.pth|\.onnx|\.bin|[A-Z]:\\/.test(text)
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
  if (tool === 'vision.resolve_target') {
    const candidates = targetCandidates(source)
    if (candidates.length) meta.push(`候选目标：${candidates.length}`)
    if (source.display_card.artifacts?.length) meta.push(`截图：${source.display_card.artifacts.length}`)
  }
  const codex = codexRun(source)
  if (tool === 'codex.run' || codex.status) {
    meta.push(codexRunStatusLabel(stringValue(codex.status) || (event.display_card.status === 'success' ? 'completed' : 'running')))
    if (codex.permission_required) meta.push('等待权限')
    const elapsed = Number(codex.elapsed_seconds || 0)
    if (elapsed > 0) meta.push(`${elapsed.toFixed(1)}s`)
  }
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

function auditEventsForTask(taskId: string) {
  const rows: ComputerUseAuditEvent[] = []
  for (const event of events.value) {
    if (event.task_id !== taskId) continue
    const auditRows = event.agent_state?.computer_use_audit
    if (!Array.isArray(auditRows)) continue
    for (const row of auditRows) {
      if (row && typeof row === 'object') rows.push(row)
    }
  }
  return rows.sort((left, right) => Number(left.timestamp || 0) - Number(right.timestamp || 0))
}

function auditTitle(row: ComputerUseAuditEvent) {
  const labels: Record<string, string> = {
    observe: '观察',
    target_candidates: '候选目标',
    approval_pending: '等待确认',
    approval_approved: '已确认',
    approval_denied: '已拒绝',
    approval_expired: '已过期',
    approval_duplicate: '重复确认',
    action_verified: '动作已验证',
    action_failed: '动作失败',
    verification_noop: '变化不明显',
    verification_unavailable: '验证不可用',
    verification_inconclusive: '验证不确定',
    target_selection_expired: '候选过期',
    target_selection_missing: '候选缺失',
    target_selection_not_current: '候选已失效',
  }
  return labels[row.event_type] || '审计事件'
}

function auditTime(row: { timestamp?: unknown }) {
  const timestamp = Number(row.timestamp || 0)
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function auditMeta(row: ComputerUseAuditEvent) {
  const meta: string[] = []
  const action = auditActionLabel(row)
  if (action) meta.push(action)
  if (row.risk_level) meta.push(riskLabel(String(row.risk_level)))
  if (row.approval_status) meta.push(approvalStatusLabel(String(row.approval_status)))
  const verification = verificationStatusLabel(String(row.verification_result?.status || ''))
  if (verification) meta.push(verification)
  return meta
}

function auditSignalRows(row: ComputerUseAuditEvent) {
  const signals = row.verification_result?.signals || {}
  return Object.entries(signals)
    .filter(([key]) => ['image_changed', 'screenshot_changed', 'title_changed', 'ocr_changed', 'dimensions_changed'].includes(key))
    .map(([key, value]) => [signalLabel(key), signalValue(String(value || 'unknown'))])
}

function auditEvidenceRows(row: ComputerUseAuditEvent) {
  const rows = Array.isArray(row.candidate_evidence) ? row.candidate_evidence : []
  return rows.slice(0, 5).map((item) => {
    const evidence = asRecord(item)
    const rank = Number(evidence.rank || 0)
    return [
      rank > 0 ? `第 ${rank} 项` : '候选',
      evidenceSourceLabel(stringValue(evidence.source)),
      evidenceConfidenceLabel(stringValue(evidence.confidence_band)),
      evidenceAmbiguityLabel(stringValue(evidence.ambiguity_reason)),
      evidenceActionabilityLabel(stringValue(evidence.actionability)),
      evidenceCaptureTrustLabel(stringValue(evidence.capture_trust)),
    ]
      .filter(Boolean)
      .join(' · ')
  })
}

function auditActionLabel(row: ComputerUseAuditEvent) {
  if (row.tool_name?.startsWith('computer.')) return computerActionLabel(row.tool_name)
  const labels: Record<string, string> = {
    observe: '画面观察',
    target_candidate: '目标定位',
  }
  return labels[row.action_name || ''] || ''
}

function riskLabel(value: string) {
  const labels: Record<string, string> = {
    low: '低风险',
    medium: '中风险',
    high: '高风险',
  }
  return labels[value] || '风险未知'
}

function approvalStatusLabel(value: string) {
  const labels: Record<string, string> = {
    pending: '待确认',
    approved: '已允许',
    denied: '已拒绝',
    expired: '已过期',
    duplicate: '重复响应',
  }
  return labels[value] || '确认状态未知'
}

function verificationStatusLabel(value: string) {
  const labels: Record<string, string> = {
    changed: '验证：有变化',
    likely_noop: '验证：变化不明显',
    unavailable: '验证：不可用',
    inconclusive: '验证：不确定',
    failed: '验证：失败',
  }
  return labels[value] || ''
}

function signalLabel(key: string) {
  const labels: Record<string, string> = {
    image_changed: '图像',
    screenshot_changed: '像素',
    title_changed: '标题',
    ocr_changed: 'OCR',
    dimensions_changed: '尺寸',
  }
  return labels[key] || key
}

function signalValue(value: string) {
  const labels: Record<string, string> = {
    changed: '有变化',
    unchanged: '无明显变化',
    unknown: '未知',
  }
  return labels[value] || '未知'
}

function auditArgumentRows(row: ComputerUseAuditEvent) {
  const args = row.sanitized_arguments || {}
  return Object.entries(args).map(([key, value]) => [argumentLabel(key), argumentValue(value)])
}

function argumentLabel(key: string) {
  const labels: Record<string, string> = {
    target: '目标',
    button: '按键',
    input: '输入',
    characters: '字符数',
    direction: '方向',
    amount: '数量',
    shortcut: '快捷键',
    key_count: '键数',
    target_description: '描述',
    candidate_count: '候选数',
    requires_selection: '需选择',
    requires_approval: '需确认',
    source: '来源',
    ambiguity: '歧义',
    rank: '排序',
    selected_rank: '已选',
    selection_status: '候选状态',
  }
  return labels[key] || key
}

function argumentValue(value: unknown) {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  const text = stringValue(value)
  const labels: Record<string, string> = {
    screen_position: '屏幕位置',
    typed_text_hidden: '已隐藏文本',
    keys_hidden: '已隐藏按键',
    scroll_steps_hidden: '已隐藏步数',
    hidden: '已隐藏',
    up: '向上',
    down: '向下',
    unknown: '未知',
  }
  return labels[text] || trimText(text, 28)
}

function evidenceSourceLabel(value: string) {
  const labels: Record<string, string> = {
    accessibility: 'UI控件',
    ocr: 'OCR',
    fused: '融合',
    visual: '视觉',
    unknown: '来源未知',
  }
  return labels[value] || '来源未知'
}

function evidenceConfidenceLabel(value: string) {
  const labels: Record<string, string> = {
    high: '置信较高',
    medium: '置信中等',
    low: '置信偏低',
  }
  return labels[value] || '置信未知'
}

function evidenceAmbiguityLabel(value: string) {
  const labels: Record<string, string> = {
    none: '较明确',
    close_score: '分数接近',
    low_confidence: '置信偏低',
  }
  return labels[value] || '需要确认'
}

function evidenceActionabilityLabel(value: string) {
  const labels: Record<string, string> = {
    actionable: '可操作',
    disabled: '未启用',
    static_text: '静态文字',
    visual_only: '仅视觉',
    ocr_text: '文字匹配',
    ocr_uia_fused: '融合证据',
    unknown: '可操作性未知',
  }
  return labels[value] || '可操作性未知'
}

function evidenceCaptureTrustLabel(value: string) {
  const labels: Record<string, string> = {
    trusted: '位置可信',
    untrusted: '位置待复核',
    unavailable: '缺少位置',
  }
  return labels[value] || '位置待确认'
}

function auditArtifacts(row: ComputerUseAuditEvent) {
  return [...(row.before_artifacts || []), ...(row.after_artifacts || [])].filter((artifact) => artifact.ref)
}

function auditArtifactLabel(artifact: ComputerUseAuditArtifact, index: number) {
  if (artifact.label) return artifact.role === 'before' ? '执行前截图' : artifact.role === 'after' ? '执行后截图' : artifact.label
  return artifactLabel(artifact.ref || '', index)
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

function codexRun(event?: AgentEvent) {
  return asRecord(event?.agent_state?.codex_run)
}

function codexRunForTask(event?: AgentEvent, detail?: AgentEvent) {
  const latest = codexRun(event)
  if (latest.status || latest.safe_summary) return latest
  return codexRun(detail)
}

function codexTimeline(event?: AgentEvent, detail?: AgentEvent) {
  const run = codexRunForTask(event, detail)
  const rows = Array.isArray(run.events) ? run.events : []
  return rows.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object' && !Array.isArray(row))
}

function codexArtifactRows(event?: AgentEvent, detail?: AgentEvent) {
  const run = codexRunForTask(event, detail)
  const rows = Array.isArray(run.artifacts) ? run.artifacts : []
  return rows.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object' && !Array.isArray(row))
}

function codexRunStatusLabel(status: string) {
  const labels: Record<string, string> = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    permission_required: '等待权限',
    fail_closed: '权限不可继续',
    permission_unresumable: '权限不可继续',
    denied: '已拒绝',
    expired: '已过期',
    mismatch: '已失效',
    not_found: 'Codex 未找到',
  }
  return labels[status] || '代码任务'
}

function codexEventLabel(row: Record<string, unknown>) {
  const category = stringValue(row.category) || stringValue(row.event_type)
  const labels: Record<string, string> = {
    started: '开始',
    progress: '进度',
    final: '最终结果',
    error: '错误',
    permission_request: '权限请求',
    permission_denied: '权限已拒绝',
    permission_expired: '权限已过期',
    permission_mismatch: '权限已失效',
    unknown: '未知事件',
    malformed: '无法解析',
  }
  return labels[category] || 'Codex 事件'
}

function codexRunSummary(event?: AgentEvent, detail?: AgentEvent) {
  const run = codexRunForTask(event, detail)
  return stringValue(run.safe_summary)
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
  return artifactDataUrls.value[artifact] || convertFileSrc(artifactPath(artifact))
}

function openArtifactPreview(artifact: string, event: AgentEvent) {
  void loadArtifactData(artifact)
  previewArtifact.value = artifact
  previewArtifactEvent.value = event
}

function closeArtifactPreview() {
  previewArtifact.value = ''
  previewArtifactEvent.value = null
}

function handleDialogClick(event: MouseEvent) {
  if (!artifactDialog.value) return
  const rect = artifactDialog.value.getBoundingClientRect()
  if (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  ) {
    closeArtifactPreview()
  }
}

function preloadImageArtifacts(event: AgentEvent) {
  for (const artifact of event.display_card.artifacts || []) {
    if (isImageArtifact(artifact)) void loadArtifactData(artifact)
  }
}

async function loadArtifactData(artifact: string) {
  if (!artifact || artifactDataUrls.value[artifact] || artifactLoadFailed.value[artifact]) return
  try {
    const result = (await client.readArtifact(artifact)) as ArtifactReadResult
    if (result.ok && result.data_url) {
      artifactDataUrls.value = { ...artifactDataUrls.value, [artifact]: result.data_url }
      return
    }
  } catch {
    // Fall back to Tauri asset URLs; failures are reflected by the image element.
  }
  artifactLoadFailed.value = { ...artifactLoadFailed.value, [artifact]: true }
}

function targetCandidates(event?: AgentEvent): Record<string, unknown>[] {
  const state = event?.agent_state || {}
  const rows = Array.isArray(state.target_candidates) ? state.target_candidates : state.target_candidate ? [state.target_candidate] : []
  return rows.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object' && !Array.isArray(row))
}

function eventSelectionId(event?: AgentEvent) {
  return stringValue(event?.agent_state?.selection_id)
}

function targetSelectionId(event: AgentEvent | undefined, candidate: Record<string, unknown>) {
  return stringValue(candidate.selection_id) || eventSelectionId(event)
}

function targetPreviews(event: AgentEvent | undefined, artifact: string) {
  return targetCandidates(event).filter((candidate) => {
    const preview = asRecord(candidate.preview)
    const previewArtifact = stringValue(preview.artifact)
    return !previewArtifact || previewArtifact === artifact
  })
}

function artifactFrameStyle(event: AgentEvent | undefined, artifact: string) {
  const preview = asRecord(targetPreviews(event, artifact)[0]?.preview)
  const imageWidth = Number(preview.image_width || 0)
  const imageHeight = Number(preview.image_height || 0)
  if (imageWidth <= 0 || imageHeight <= 0) return {}
  return { aspectRatio: `${imageWidth} / ${imageHeight}` }
}

function targetBoxStyle(candidate: Record<string, unknown>) {
  const preview = asRecord(candidate.preview)
  const bbox = numberTuple(preview.bbox)
  const imageWidth = Number(preview.image_width || 0)
  const imageHeight = Number(preview.image_height || 0)
  if (!bbox || imageWidth <= 0 || imageHeight <= 0) return {}
  const [left, top, width, height] = bbox
  return {
    left: `${(left / imageWidth) * 100}%`,
    top: `${(top / imageHeight) * 100}%`,
    width: `${(width / imageWidth) * 100}%`,
    height: `${(height / imageHeight) * 100}%`,
  }
}

function targetRank(candidate: Record<string, unknown>, index: number) {
  const rank = Number(candidate.rank || 0)
  return Number.isFinite(rank) && rank > 0 ? rank : index + 1
}

function targetLabel(candidate: Record<string, unknown>) {
  const preview = asRecord(candidate.preview)
  return stringValue(preview.label) || stringValue(candidate.label) || '候选目标'
}

function targetRegion(candidate: Record<string, unknown>) {
  const preview = asRecord(candidate.preview)
  return stringValue(preview.region_name) || stringValue(preview.region_label) || stringValue(candidate.region_label) || '未知区域'
}

function targetConfidence(candidate: Record<string, unknown>) {
  const preview = asRecord(candidate.preview)
  const value = Number(preview.confidence || candidate.confidence || 0)
  if (!Number.isFinite(value) || value <= 0) return ''
  return `${Math.round(value * 100)}%`
}

function targetEvidence(candidate: Record<string, unknown>) {
  return asRecord(candidate.evidence)
}

function targetSource(candidate: Record<string, unknown>) {
  const evidence = targetEvidence(candidate)
  const sourceLabel = stringValue(evidence.source_label)
  if (sourceLabel) return sourceLabel
  const preview = asRecord(candidate.preview)
  const source = stringValue(preview.source) || stringValue(candidate.source)
  const labels: Record<string, string> = {
    accessibility: 'UI控件',
    ocr: 'OCR',
    fused: '融合',
    visual: '视觉',
  }
  return labels[source] || '候选'
}

function targetReason(candidate: Record<string, unknown>) {
  return stringValue(candidate.reason) || 'OCR 候选'
}

function targetAmbiguity(candidate: Record<string, unknown>) {
  const labels: Record<string, string> = {
    none: '较明确',
    close_score: '分数接近',
    low_confidence: '置信偏低',
  }
  const value = stringValue(candidate.ambiguity)
  return labels[value] || '需要确认'
}

function targetConfidenceChip(candidate: Record<string, unknown>) {
  const evidence = targetEvidence(candidate)
  const label = stringValue(evidence.confidence_label)
  if (label) return label
  const confidence = targetConfidence(candidate)
  return confidence ? `置信 ${confidence}` : '置信未知'
}

function targetRiskChip(event: AgentEvent | undefined, candidate: Record<string, unknown>) {
  const evidence = targetEvidence(candidate)
  const actionability = stringValue(evidence.actionability)
  const ambiguity = stringValue(evidence.ambiguity_reason)
  if (event?.type === 'approval_required') return '中风险确认'
  if (actionability === 'disabled' || actionability === 'static_text' || actionability === 'visual_only') return '需人工判断'
  if (ambiguity && ambiguity !== 'none') return '存在歧义'
  return '需确认'
}

function targetActionability(candidate: Record<string, unknown>) {
  const evidence = targetEvidence(candidate)
  return stringValue(evidence.actionability_label) || '可操作性未知'
}

function targetCaptureTrust(candidate: Record<string, unknown>) {
  const evidence = targetEvidence(candidate)
  return stringValue(evidence.capture_trust_label) || '位置待确认'
}

function targetConfirmationReason(candidate: Record<string, unknown>, event?: AgentEvent) {
  const evidence = targetEvidence(candidate)
  const reason = stringValue(evidence.confirmation_reason)
  if (reason) return reason
  if (event?.type === 'approval_required') return '这是一次电脑操作，执行前需要你确认。'
  return '候选目标还需要你确认后才能继续。'
}

function canSelectTargetCandidate(event: AgentEvent | undefined, candidate: Record<string, unknown>) {
  const selectionId = targetSelectionId(event, candidate)
  return Boolean(connected.value && event && !selectionExpired(event) && selectionId && selectionId === currentSemanticSelectionId.value)
}

function selectionExpired(event: AgentEvent) {
  const context = asRecord(event.agent_state?.selection_context)
  const ttl = Number(context.expires_in_seconds || 0)
  if (!Number.isFinite(ttl) || ttl <= 0) return false
  return nowSeconds.value > event.created_at + ttl
}

function targetPreviewSummary(event: AgentEvent | undefined, artifact: string) {
  const previews = targetPreviews(event, artifact)
  if (!previews.length) return ''
  return previews
    .slice(0, 3)
    .map((candidate) => [targetLabel(candidate), targetSource(candidate), targetRegion(candidate), targetConfidence(candidate)].filter(Boolean).join(' · '))
    .join(' / ')
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function numberTuple(value: unknown) {
  if (!Array.isArray(value) || value.length !== 4) return undefined
  const numbers = value.map((item) => Number(item))
  return numbers.every((item) => Number.isFinite(item)) ? numbers : undefined
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function sourceLabel(value: string) {
  const labels: Record<string, string> = {
    system_audio: '系统音频',
    ocr_subtitle: '字幕/OCR',
    auto: '自动',
  }
  return labels[value] || value
}

function errorLabel(value: string) {
  const labels: Record<string, string> = {
    system_audio_unavailable: '音频不可用',
    system_audio_windows_only: '仅 Windows 音频',
    system_audio_dependency_missing: '音频依赖缺失',
    system_audio_device_missing: '无回环设备',
    system_audio_capture_failed: '音频捕获失败',
    asr_unconfigured: 'ASR 未配置',
    asr_disabled: 'ASR 未启用',
    asr_timeout: 'ASR 超时',
    empty_transcript: '音频无文本',
  }
  return labels[value] || value
}

function expressionEmotionClass(value: string) {
  const normalized = value.trim().toLowerCase().replace(/\s+/g, '_')
  return ['happy', 'thinking', 'alert', 'worried', 'serious', 'neutral'].includes(normalized) ? normalized : 'neutral'
}

function expressionEmotionLabel(value: string) {
  return {
    happy: '开心',
    thinking: '思考',
    alert: '警觉',
    worried: '担心',
    serious: '专注',
    neutral: '平静',
  }[expressionEmotionClass(value)]
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
  beginNewVoiceIntent()
  void client.sendUserText(text).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '发送失败'
  })
  input.value = ''
}

function startWatchLoop() {
  errorText.value = ''
  void client.watchLoopStart({
    query: '陪我看当前视频',
    interval_seconds: 6,
    sample_count: 3,
    sample_interval_ms: 700,
    transcript_source: watchTranscriptSource.value,
    proactive_enabled: watchProactiveEnabled.value,
    commentary_interval_seconds: watchCommentaryInterval.value,
    vision_interval_ticks: watchVisionInterval.value,
  }).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '实时陪看启动失败'
  })
}

function configureWatchLoop() {
  errorText.value = ''
  void client.watchLoopConfigure({
    transcript_source: watchTranscriptSource.value,
    proactive_enabled: watchProactiveEnabled.value,
    commentary_interval_seconds: watchCommentaryInterval.value,
    vision_interval_ticks: watchVisionInterval.value,
  }).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '实时陪看设置失败'
  })
}

function refreshWatchVision() {
  if (!watchLoopActive.value) return
  errorText.value = ''
  void client.watchLoopRefresh({ force_visual_summary: true }).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '画面理解失败'
  })
}

function stopWatchLoop() {
  errorText.value = ''
  void client.watchLoopStop().catch((error) => {
    errorText.value = error instanceof Error ? error.message : '实时陪看停止失败'
  })
}

function syncMemoryFromEvent(event: AgentEvent) {
  const memory = asRecord(event.agent_state?.memory)
  if ('recent' in memory || 'pending' in memory || 'vault_path' in memory) {
    memoryStatus.value = memory as unknown as MemoryStatus
  }
}

async function refreshMemoryStatus() {
  try {
    const result = (await client.memoryStatus()) as { ok?: boolean; memory?: MemoryStatus }
    if (result.memory) memoryStatus.value = result.memory
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆状态读取失败'
  }
}

async function saveMemoryCandidate(candidateId: number) {
  try {
    const result = (await client.memorySaveCandidate(candidateId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
    if (result.memory) memoryStatus.value = result.memory
    if (!result.ok) errorText.value = result.error || '记忆保存失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆保存失败'
  }
}

async function rejectMemoryCandidate(candidateId: number) {
  try {
    const result = (await client.memoryRejectCandidate(candidateId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
    if (result.memory) memoryStatus.value = result.memory
    if (!result.ok) errorText.value = result.error || '记忆已忽略'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆忽略失败'
  }
}

async function toggleMemoryEnabled(event: Event) {
  const enabled = Boolean((event.target as HTMLInputElement | null)?.checked)
  try {
    const result = (await client.memorySetEnabled(enabled)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
    if (result.memory) memoryStatus.value = result.memory
    if (!result.ok) errorText.value = result.error || '记忆开关更新失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆开关更新失败'
  }
}

async function deleteMemory(memoryId: number) {
  try {
    const result = (await client.memoryDelete(memoryId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
    if (result.memory) memoryStatus.value = result.memory
    if (!result.ok) errorText.value = result.error || '记忆删除失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆删除失败'
  }
}

async function clearMemory() {
  const count = pendingMemories.value.length + recentMemories.value.length
  if (!count) return
  if (!window.confirm(`清空 ${count} 条记忆和待确认候选？此操作不会删除手动编辑区。`)) return
  try {
    const result = (await client.memoryClear()) as { ok?: boolean; memory?: MemoryStatus; error?: string }
    if (result.memory) memoryStatus.value = result.memory
    if (!result.ok) errorText.value = result.error || '记忆清空失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '记忆清空失败'
  }
}

function resolveApproval(approved: boolean) {
  if (!pendingApproval.value) return
  const approvalId = approvalIdFor(pendingApproval.value)
  if (!approvalId) return
  const epoch = beginNewVoiceIntent()
  taskVoiceEpochs.set(pendingApproval.value.task_id, epoch)
  clearMiniSpeechTimer()
  miniSpeechActive.value = false
  void syncCompactWindowSize()
  void client.resolveApproval(approvalId, approved).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '审批提交失败'
  })
}

function requestMiniChange() {
  if (pendingApproval.value) resolveApproval(false)
  clearMiniSpeechTimer()
  miniSpeechActive.value = false
  miniDashboardActive.value = true
  void syncCompactWindowSize()
  void nextTick(() => {
    document.querySelector<HTMLInputElement>('.mini-input')?.focus()
  })
}

function selectTargetCandidate(event: AgentEvent | undefined, candidate: Record<string, unknown>, index: number) {
  if (!canSelectTargetCandidate(event, candidate)) return
  const rank = targetRank(candidate, index)
  const selectionId = targetSelectionId(event, candidate)
  beginNewVoiceIntent()
  void client.selectSemanticTarget(selectionId, rank).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '候选选择失败'
  })
}

function approvalIdFor(event: AgentEvent) {
  const approval = asRecord(event.agent_state?.approval)
  return stringValue(approval.approval_id)
}

async function playAudioPath(path?: string, dataUrl?: string) {
  const source = dataUrl || path
  if (!source) return
  let audio: HTMLAudioElement | null = null
  try {
    stopSpokenAudio()
    const url = source.startsWith('data:') ? source : convertFileSrc(source)
    audio = new Audio(url)
    currentAudio = audio
    audio.onended = () => {
      if (currentAudio === audio) currentAudio = null
    }
    await audio.play()
  } catch (error) {
    if (audio && currentAudio === audio) currentAudio = null
    lastTtsError.value = error instanceof Error ? error.name || 'audio_play_failed' : 'audio_play_failed'
  }
}

function playVoiceAudio(payload: VoiceAudioPayload) {
  if (payload.voice_audio_error) {
    lastTtsError.value = ttsErrorLabel(payload.voice_audio_error)
  }
  if (shouldSuppressProactiveVoice(payload)) return
  const audioKey = voiceAudioKey(payload)
  const eventEpoch = voiceEventEpochs.get(audioKey)
  if (!shouldPlayVoiceAudio(eventEpoch, voiceEpoch)) return
  if (playedVoiceAudioKeys.has(audioKey)) return
  rememberPlayedVoiceAudioKey(audioKey)
  void playAudioPath(payload.voice_audio_path, payload.voice_audio_data_url)
}

function shouldSuppressProactiveVoice(payload: VoiceAudioPayload) {
  if (!payload.watch_commentary) return false
  if (input.value.trim()) return true
  const active = document.activeElement
  return active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement
}

function rememberPlayedVoiceAudioKey(key: string) {
  playedVoiceAudioKeys.add(key)
  if (playedVoiceAudioKeys.size <= 80) return
  const oldest = playedVoiceAudioKeys.values().next().value
  if (oldest) playedVoiceAudioKeys.delete(oldest)
}

function rememberVoiceEventEpoch(event: AgentEvent) {
  const runtimeUpdate = asRecord(event.agent_state?.runtime_config_update)
  if (event.type === 'tool_completed' && runtimeUpdate.ok) {
    runtimePreview.value = runtimeUpdate as unknown as RuntimeConfigMutationResult
    if (runtimeUpdate.changed && !runtimeUpdate.dry_run) runtimeDraftDirty.value = false
  }
  let eventEpoch = taskVoiceEpochs.get(event.task_id)
  if (event.type === 'user_message' || eventEpoch === undefined) {
    eventEpoch = voiceEpoch
    taskVoiceEpochs.set(event.task_id, eventEpoch)
  }
  if (event.voice_line?.text && isPlayableVoiceEvent(event)) {
    voiceEventEpochs.set(
      voiceAudioKey({
        task_id: event.task_id,
        event_type: event.type,
        event_created_at: event.created_at,
        voice_text: event.voice_line.text,
      }),
      eventEpoch,
    )
  }
}

function beginNewVoiceIntent() {
  voiceEpoch = nextVoiceEpoch(voiceEpoch)
  stopSpokenAudio()
  return voiceEpoch
}

function runtimeStatusRows(): RuntimeProviderStatus[] {
  const providers = ready.value?.runtime?.providers
  if (providers?.length) {
    return providers.map((row) =>
      row.name === 'tts' && lastTtsError.value
        ? { ...row, last_error: lastTtsError.value }
        : row,
    )
  }
  const asr = ready.value?.asr
  const tts = ready.value?.tts
  return [
    {
      name: 'asr',
      label: 'ASR',
      state: asr?.configured ? 'ready' : 'off',
      enabled: Boolean(asr?.enabled),
      configured: Boolean(asr?.configured),
      provider: asr?.provider || 'none',
      summary: asr?.configured ? '已配置' : '未配置',
      timeout_seconds: asr?.timeout_seconds || 30,
      limit: formatBytes(asr?.max_bytes || 0),
      notes: [`max ${asr?.max_seconds || 30}s`],
    },
    {
      name: 'tts',
      label: 'TTS',
      state: tts?.configured ? 'ready' : 'off',
      enabled: Boolean(tts?.enabled),
      configured: Boolean(tts?.configured),
      provider: tts?.provider || 'none',
      summary: tts?.configured ? '已配置' : '未配置',
      last_error: tts?.last_error || '',
    },
  ]
}

function providerSummary(row: RuntimeProviderStatus) {
  return row.summary || providerStateLabel(row.state)
}

function providerMeta(row: RuntimeProviderStatus) {
  const meta: string[] = []
  if (row.provider) meta.push(`provider=${row.provider}`)
  if (row.model) meta.push(`model=${row.model}`)
  if (row.timeout_seconds) meta.push(`timeout=${row.timeout_seconds}s`)
  if (row.limit) meta.push(row.limit)
  const error = providerErrorLabel(row.last_error || '')
  if (error) meta.push(`last=${error}`)
  for (const note of row.notes || []) {
    if (note) meta.push(note)
  }
  return meta
}

function defaultRuntimeDraft() {
  return {
    asr_enabled: false,
    asr_max_seconds: 30,
    asr_max_bytes: 12 * 1024 * 1024,
    asr_timeout_seconds: 30,
    tts_enabled: false,
    tts_volume: 0.85,
    tts_speed_factor: 1.2,
    tts_fallback_to_system: false,
    ocr_timeout_seconds: 5,
    llm_temperature: 0.7,
    llm_use_mock: true,
    computer_post_action_settle_ms: 200,
  }
}

function syncRuntimeDraft(payload: CoreReadyPayload) {
  if (runtimeDraftDirty.value) return
  const draft = defaultRuntimeDraft()
  const settings = payload.runtime_settings || {}
  const asr = settings.asr || payload.asr || {}
  const tts = payload.tts || {}
  draft.asr_enabled = Boolean(asr.enabled)
  draft.asr_max_seconds = Math.max(1, Number(asr.max_seconds || draft.asr_max_seconds))
  draft.asr_max_bytes = Math.max(1024, Number(asr.max_bytes || draft.asr_max_bytes))
  draft.asr_timeout_seconds = Math.max(1, Number(asr.timeout_seconds || draft.asr_timeout_seconds))
  const ttsSettings = settings.tts || tts
  draft.tts_enabled = Boolean(ttsSettings.enabled)
  draft.tts_volume = Math.max(0, Number(ttsSettings.volume ?? draft.tts_volume))
  draft.tts_speed_factor = Math.max(0.5, Number(ttsSettings.speed_factor ?? draft.tts_speed_factor))
  draft.tts_fallback_to_system = Boolean(ttsSettings.fallback_to_system)
  const ocr = settings.ocr || runtimeProvider(payload, 'ocr')
  draft.ocr_timeout_seconds = Math.max(1, Number(ocr?.timeout_seconds || draft.ocr_timeout_seconds))
  const computerUse = settings.computer_use || {}
  const settleMs = computerUse.post_action_settle_ms ?? (parseSettleMs(runtimeProvider(payload, 'computer_use')?.limit) || draft.computer_post_action_settle_ms)
  draft.computer_post_action_settle_ms = Math.max(0, Number(settleMs))
  const llmSettings = settings.llm || {}
  const text = runtimeProvider(payload, 'text')
  draft.llm_temperature = Math.max(0, Number(llmSettings.temperature ?? draft.llm_temperature))
  draft.llm_use_mock = typeof llmSettings.use_mock === 'boolean' ? llmSettings.use_mock : text?.state === 'mock'
  runtimeDraft.value = draft
}

function runtimeProvider(payload: CoreReadyPayload, name: string) {
  return (payload.runtime?.providers || []).find((row) => row.name === name)
}

function parseSettleMs(value?: string) {
  const match = String(value || '').match(/settle\s+(\d+)ms/i)
  return match ? Number(match[1]) : 0
}

function runtimeUpdatePayload() {
  const draft = runtimeDraft.value
  return {
    asr: {
      enabled: Boolean(draft.asr_enabled),
      max_seconds: safeInteger(draft.asr_max_seconds, 1),
      max_bytes: safeInteger(draft.asr_max_bytes, 1024),
      timeout_seconds: safeInteger(draft.asr_timeout_seconds, 1),
    },
    tts: {
      enabled: Boolean(draft.tts_enabled),
      volume: safeNumber(draft.tts_volume, 0.85),
      speed_factor: safeNumber(draft.tts_speed_factor, 1.2),
      fallback_to_system: Boolean(draft.tts_fallback_to_system),
    },
    ocr: {
      timeout_seconds: safeInteger(draft.ocr_timeout_seconds, 1),
    },
    llm: {
      temperature: safeNumber(draft.llm_temperature, 0.7),
      use_mock: Boolean(draft.llm_use_mock),
    },
    computer_use: {
      post_action_settle_ms: safeInteger(draft.computer_post_action_settle_ms, 0),
    },
  }
}

function markRuntimeDraftDirty() {
  runtimeDraftDirty.value = true
  runtimePreview.value = null
}

async function previewRuntimeSettings() {
  runtimePreviewLoading.value = true
  runtimePreview.value = null
  try {
    const result = (await client.previewRuntimeConfig(runtimeUpdatePayload())) as { ok?: boolean; preview?: RuntimeConfigMutationResult }
    runtimePreview.value = result.preview || null
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '运行设置预览失败'
  } finally {
    runtimePreviewLoading.value = false
  }
}

async function applyRuntimeSettings() {
  runtimeApplyLoading.value = true
  try {
    const result = (await client.applyRuntimeConfig(runtimeUpdatePayload())) as { ok?: boolean; submitted?: boolean; preview?: RuntimeConfigMutationResult }
    if (result.preview) runtimePreview.value = result.preview
    if (result.ok === false) errorText.value = result.preview?.summary || '运行设置没有提交'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '运行设置提交失败'
  } finally {
    runtimeApplyLoading.value = false
  }
}

function runtimeChangeActionLabel(action: string) {
  const labels: Record<string, string> = {
    changed: '将更新',
    unchanged: '无变化',
  }
  return labels[action] || '待处理'
}

function runtimeValueKindLabel(kind: string) {
  const labels: Record<string, string> = {
    boolean: '开关',
    seconds: '秒',
    bytes: '字节',
    milliseconds: '毫秒',
    number: '数值',
    identifier: '标识',
    model: '模型名',
    endpoint: '端点',
    language: '语言',
  }
  return labels[kind] || '设置'
}

function safeInteger(value: unknown, fallback: number) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.round(number) : fallback
}

function safeNumber(value: unknown, fallback: number) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function providerStateLabel(state: string) {
  const labels: Record<string, string> = {
    ready: '可用',
    mock: 'Mock',
    off: '未启用',
    unavailable: '不可用',
    error: '需配置',
  }
  return labels[state] || '未知'
}

function providerErrorLabel(error: string) {
  if (!error) return ''
  const labels: Record<string, string> = {
    asr_unconfigured: 'ASR 未配置',
    asr_disabled: 'ASR 未启用',
    asr_config_error: 'ASR 配置有误',
    mock_asr_developer_only: '仅开发可用',
    audio_too_large: '音频过大',
    audio_decode_failed: '音频解码失败',
    asr_timeout: '识别超时',
    openai_package_missing: '缺少 OpenAI 包',
    empty_audio: '音频为空',
    empty_transcript: '无转写文本',
    asr_failed: '识别失败',
    tts_timeout: '合成超时',
    tts_service_unavailable: '服务未连接',
    tts_config_error: 'TTS 配置有误',
    tts_failed: '合成失败',
    pillow_missing: '缺少 Pillow',
    pytesseract_missing: '缺少 pytesseract',
    tesseract_missing: '缺少 Tesseract',
    tesseract_unavailable: 'Tesseract 不可用',
    ocr_dependency_missing: 'OCR 依赖缺失',
    computer_use_windows_only: '仅 Windows 可执行',
    model_unconfigured: '模型未配置',
    runtime_status_unavailable: '状态不可用',
    empty_update: '没有变更',
    field_not_allowed: '不允许修改',
    duplicate_field: '重复设置',
    invalid_type: '类型不正确',
    invalid_value: '值不可用',
    invalid_endpoint: '端点不可用',
    out_of_range: '超出范围',
    sensitive_field_forbidden: '敏感字段已拒绝',
    sensitive_value_forbidden: '敏感值已拒绝',
    config_missing: '缺少配置',
    config_invalid: '配置不可读取',
    write_failed: '写入失败',
  }
  return error
    .split(';')
    .map((part) => labels[part] || '')
    .filter(Boolean)
    .join(' / ')
}

function ttsErrorLabel(error: string) {
  const labels: Record<string, string> = {
    tts_timeout: '合成超时',
    tts_service_unavailable: '服务未连接',
    tts_config_error: '配置有误',
    tts_failed: '合成失败',
  }
  return labels[error] || ''
}

function formatBytes(value: number) {
  const bytes = Number(value || 0)
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / 1024 / 1024)}MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`
  return `${Math.max(0, bytes)}B`
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
    beginNewVoiceIntent()
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
    const result = (await client.transcribeVoice(audioBase64, mimeType, voiceTranscribeTimeoutMs.value)) as { ok?: boolean; transcript?: string; error?: string; message?: string }
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

onMounted(() => {
  client.connect()
  clockTimer = window.setInterval(() => {
    nowSeconds.value = Date.now() / 1000
  }, 5000)
  window.addEventListener('dragstart', preventNativeAssetDrag, true)
  window.addEventListener('selectstart', preventCompactSelection, true)
})
onBeforeUnmount(() => {
  document.body.classList.remove('transparent-active')
  window.removeEventListener('dragstart', preventNativeAssetDrag, true)
  window.removeEventListener('selectstart', preventCompactSelection, true)
  stopMascotDragWatch()
  clearMascotClickTimer()
  clearMiniSpeechTimer()
  if (clockTimer !== null) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
  cleanupVoiceStream()
  stopSpokenAudio()
  client.close()
})
</script>

<template>
  <main
    class="shell"
    :class="{
      'compact-active': isCompactMode,
      'mini-dashboard-active': isCompactMode && miniDashboardActive,
      'mini-speech-active': isCompactMode && miniSpeechActive,
    }"
    @dragstart.capture="preventNativeAssetDrag"
    @drop.capture.prevent
  >
    <!-- Header Titlebar -->
    <header class="titlebar" @mousedown="startWindowDrag">
      <div class="traffic-lights">
        <button type="button" class="light close" title="关闭" aria-label="关闭窗口" @mousedown.stop @click.stop="closeWindow"></button>
        <button type="button" class="light minimize" title="最小化" aria-label="最小化窗口" @mousedown.stop @click.stop="minimizeWindow"></button>
        <button type="button" class="light zoom" title="缩放" aria-label="缩放窗口" @mousedown.stop @click.stop="toggleMaximizeWindow"></button>
      </div>
      <div class="window-title">Joi Desktop</div>
      
      <!-- Actions panel on right header -->
      <div class="topbar-actions">
        <!-- Speak replies -->
        <div class="speak-replies-wrapper" :class="{ checked: runtimeDraft.tts_enabled }" @click="runtimeDraft.tts_enabled = !runtimeDraft.tts_enabled; markRuntimeDraftDirty(); applyRuntimeSettings()">
          <div class="checkbox-custom"></div>
          <span>Speak replies</span>
        </div>
        <!-- Compact Mode Switcher -->
        <button class="compact-toggle-btn" @click="toggleCompactMode" title="切换到微缩挂件模式">
          🗜️ 微缩模式
        </button>
      </div>
    </header>

    <section class="workspace" :class="`cabin-${activeCabin}`">
      <div class="topbar">
        <div>
          <span class="brand">Joi</span>
          <span class="mode">{{ currentMode }}</span>
        </div>
        <div class="top-actions">
          <button type="button" class="ghost-button watch-loop-action" @click="watchLoopActive ? stopWatchLoop() : startWatchLoop()" v-if="activeCabin === 'workspace'">
            {{ watchLoopActive ? '停止陪看' : '实时陪看' }}
          </button>
          <button type="button" class="ghost-button" @click="developerMode = !developerMode" v-if="activeCabin === 'workspace'">
            {{ developerMode ? '隐藏审计' : '显示审计' }}
          </button>
          <span class="status" :class="{ online: connected }">{{ connectionLabel }}</span>
        </div>
      </div>

      <p class="error" v-if="errorText">{{ errorText }}</p>

      <section class="watch-session-strip" :class="{ active: watchLoopActive }" v-if="activeCabin === 'workspace' && (watchLoopActive || watchLoopStatus.iterations)">
        <div class="watch-session-main">
          <span class="watch-session-dot"></span>
          <div>
            <strong>{{ watchLoopActive ? '实时陪看运行中' : '实时陪看已停止' }}</strong>
            <span>{{ watchLoopMeta }}</span>
          </div>
        </div>
        <p v-if="watchLoopTranscript">{{ watchLoopTranscript }}</p>
        <p v-else>{{ watchLoopStatus.last_visual_summary || watchLoopStatus.rolling_summary || watchLoopStatus.last_summary || '后台会持续捕获当前视频画面、字幕和系统音频转写上下文。' }}</p>
        <div class="watch-session-controls">
          <label>
            <span>源</span>
            <select v-model="watchTranscriptSource" @change="configureWatchLoop">
              <option value="system_audio">系统音频</option>
              <option value="ocr_subtitle">字幕/OCR</option>
              <option value="auto">自动</option>
            </select>
          </label>
          <label class="watch-session-toggle">
            <input type="checkbox" v-model="watchProactiveEnabled" @change="configureWatchLoop" />
            <span>主动发言</span>
          </label>
          <label>
            <span>间隔</span>
            <select v-model.number="watchCommentaryInterval" :disabled="!watchProactiveEnabled" @change="configureWatchLoop">
              <option :value="20">20s</option>
              <option :value="30">30s</option>
              <option :value="45">45s</option>
              <option :value="60">60s</option>
            </select>
          </label>
          <label>
            <span>视觉</span>
            <select v-model.number="watchVisionInterval" @change="configureWatchLoop">
              <option :value="0">手动</option>
              <option :value="3">3轮</option>
              <option :value="5">5轮</option>
              <option :value="10">10轮</option>
            </select>
          </label>
          <button type="button" class="watch-inline-button" :disabled="!watchLoopActive" title="立即理解当前画面" @click="refreshWatchVision">
            立即理解
          </button>
          <small v-if="watchLoopSourceHealth.length">{{ watchLoopSourceHealth.join(' / ') }}</small>
        </div>
        <button type="button" class="ghost-button watch-session-stop" @click="watchLoopActive ? stopWatchLoop() : startWatchLoop()">
          {{ watchLoopActive ? '停止' : '重新开始' }}
        </button>
      </section>

      <section class="hero-panel" v-if="activeCabin === 'workspace' && !taskRows.length">
        <p class="eyebrow">Joi Agent</p>
        <h1>把任务直接交给角色。</h1>
        <p>当前优先打通写码、陪看和游戏三条闭环。你说目标，Joi 会用任务卡展示执行结果，语音只播报自然短句。</p>
      </section>

      <section class="task-section" v-if="activeCabin === 'workspace' && taskRows.length">
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
                @click="openArtifactPreview(artifact, task.latest)"
              >
                <div class="artifact-image-frame" :style="artifactFrameStyle(task.latest, artifact)">
                  <img :src="artifactSrc(artifact)" alt="" @error="loadArtifactData(artifact)" />
                  <div class="target-overlays" v-if="targetPreviews(task.latest, artifact).length">
                    <div
                      v-for="(candidate, candidateIndex) in targetPreviews(task.latest, artifact)"
                      :key="`${artifact}-target-${candidateIndex}`"
                      class="target-box"
                      :style="targetBoxStyle(candidate)"
                    >
                      <span>{{ targetRank(candidate, candidateIndex) }}</span>
                    </div>
                  </div>
                </div>
                <span>{{ artifactLabel(artifact, index) }}</span>
                <small v-if="targetPreviewSummary(task.latest, artifact)" class="target-caption">
                  {{ targetPreviewSummary(task.latest, artifact) }}
                </small>
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
            <div class="target-list" v-if="targetCandidates(task.latest).length">
              <div
                v-for="(candidate, candidateIndex) in targetCandidates(task.latest).slice(0, 5)"
                :key="`candidate-${task.taskId}-${candidateIndex}`"
                class="target-row"
              >
                <div class="target-row-head">
                  <strong>{{ targetRank(candidate, candidateIndex) }}. {{ targetLabel(candidate) }}</strong>
                  <button type="button" :disabled="!canSelectTargetCandidate(task.latest, candidate)" @click="selectTargetCandidate(task.latest, candidate, candidateIndex)">选择</button>
                </div>
                <div class="target-chip-row">
                  <span class="source-chip">{{ targetSource(candidate) }}</span>
                  <span>{{ targetConfidenceChip(candidate) }}</span>
                  <span>{{ targetRiskChip(task.latest, candidate) }}</span>
                  <span>{{ targetAmbiguity(candidate) }}</span>
                  <span>{{ targetActionability(candidate) }}</span>
                  <span>{{ targetCaptureTrust(candidate) }}</span>
                  <span>{{ targetRegion(candidate) }}</span>
                </div>
                <p>{{ targetConfirmationReason(candidate, task.latest) }}</p>
                <small>{{ targetReason(candidate) }}</small>
              </div>
            </div>
          </details>
          <div class="audit-panel" v-if="developerMode && auditEventsForTask(task.taskId).length">
            <header>
              <span>Computer Use 审计</span>
              <small>{{ auditEventsForTask(task.taskId).length }} 条</small>
            </header>
            <div class="audit-timeline">
              <div
                v-for="(row, auditIndex) in auditEventsForTask(task.taskId)"
                :key="`${task.taskId}-audit-${auditIndex}-${row.timestamp}`"
                class="audit-row"
              >
                <div class="audit-marker"></div>
                <div>
                  <div class="audit-row-head">
                    <strong>{{ auditTitle(row) }}</strong>
                    <span>{{ auditTime(row) }}</span>
                  </div>
                  <p>{{ row.sanitized_summary }}</p>
                  <div class="audit-meta" v-if="auditMeta(row).length">
                    <span v-for="item in auditMeta(row)" :key="item">{{ item }}</span>
                  </div>
                  <div class="audit-args" v-if="auditArgumentRows(row).length">
                    <span v-for="[key, value] in auditArgumentRows(row)" :key="`${row.timestamp}-${key}`">
                      <strong>{{ key }}</strong>{{ value }}
                    </span>
                  </div>
                  <div class="audit-evidence" v-if="auditEvidenceRows(row).length">
                    <span v-for="item in auditEvidenceRows(row)" :key="`${row.timestamp}-evidence-${item}`">{{ item }}</span>
                  </div>
                  <div class="audit-signals" v-if="auditSignalRows(row).length">
                    <span v-for="[key, value] in auditSignalRows(row)" :key="`${row.timestamp}-signal-${key}`">
                      <strong>{{ key }}</strong>{{ value }}
                    </span>
                  </div>
                  <div class="audit-artifacts" v-if="auditArtifacts(row).length">
                    <button
                      v-for="(artifact, artifactIndex) in auditArtifacts(row)"
                      :key="`${row.timestamp}-${artifact.ref}`"
                      type="button"
                      :disabled="!artifact.ref || !isImageArtifact(artifact.ref)"
                      @click="artifact.ref && isImageArtifact(artifact.ref) ? openArtifactPreview(artifact.ref, task.latest) : undefined"
                    >
                      {{ auditArtifactLabel(artifact, artifactIndex) }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="audit-panel" v-if="developerMode && codexTimeline(task.latest, task.detail).length">
            <header>
              <span>Codex 运行审计</span>
              <small>{{ codexRunStatusLabel(stringValue(codexRunForTask(task.latest, task.detail).status)) }}</small>
            </header>
            <p v-if="codexRunSummary(task.latest, task.detail)">{{ codexRunSummary(task.latest, task.detail) }}</p>
            <div class="audit-meta" v-if="codexArtifactRows(task.latest, task.detail).length">
              <span v-for="artifact in codexArtifactRows(task.latest, task.detail)" :key="stringValue(artifact.kind) || stringValue(artifact.label)">
                {{ stringValue(artifact.label) || 'Codex 产物' }}
              </span>
            </div>
            <div class="audit-timeline">
              <div
                v-for="(row, codexIndex) in codexTimeline(task.latest, task.detail)"
                :key="`${task.taskId}-codex-${codexIndex}-${stringValue(row.category)}`"
                class="audit-row"
              >
                <div class="audit-marker"></div>
                <div>
                  <div class="audit-row-head">
                    <strong>{{ codexEventLabel(row) }}</strong>
                    <span>{{ auditTime(row) }}</span>
                  </div>
                  <p>{{ stringValue(row.summary) || 'Codex 状态已更新。' }}</p>
                </div>
              </div>
            </div>
          </div>
          <div class="approval-actions" v-if="task.latest.type === 'approval_required' && pendingApproval?.task_id === task.taskId && approvalIdFor(task.latest)">
            <button type="button" @click="resolveApproval(true)">允许执行</button>
            <button type="button" class="secondary" @click="resolveApproval(false)">停在这里</button>
          </div>
        </article>
      </section>

      <section class="chat-section" v-if="activeCabin === 'chat'">
        <div class="section-title">
          <h2>对话舱</h2>
          <span v-if="chatRows.length">最近 {{ Math.min(chatRows.length, 8) }} 条</span>
          <span v-else>暂无记录</span>
        </div>
        <div class="emotion-status-card" :class="`emotion-${activeEmotionStatus.emotion}`">
          <div class="emotion-status-dot"></div>
          <div class="emotion-status-copy">
            <span>当前情绪</span>
            <strong>{{ activeEmotionStatus.label }}</strong>
          </div>
          <span class="emotion-status-sprite">立绘 {{ activeEmotionStatus.sprite }}</span>
        </div>
        <div class="chat-scroll-area" v-if="chatRows.length">
          <div
            v-for="event in chatRows.slice(-8)"
            :key="`${event.task_id}-${event.created_at}`"
            class="message-row"
            :class="{ human: event.type === 'user_message', joi: event.type !== 'user_message' }"
          >
            <span class="chat-name">{{ event.type === 'user_message' ? '你' : 'Joi' }}</span>
            <div class="message-bubble">{{ event.display_card.summary }}</div>
          </div>
        </div>
        <div class="chat-placeholder" v-else>
          <p>和 Joi 的交流舱已就绪</p>
          <small>输入你的问题，或点击下方麦克风开始语音对话</small>
        </div>
        
        <!-- Bottom Vocal Box & Keyboard Composer Input -->
        <form class="chat-composer-area" @submit.prevent="submit">
          <button
            type="button"
            class="composer-mic-btn"
            :class="{ recording: voiceState === 'recording' }"
            :disabled="!connected || !asrConfigured || voiceState === 'transcribing'"
            @click="toggleVoiceInput"
            title="语音说话"
          >
            <svg viewBox="0 0 24 24">
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>
            </svg>
          </button>
          <div class="composer-input-wrapper">
            <input
              v-model="input"
              class="composer-text-input"
              :disabled="!connected"
              placeholder="给 Joi 发送指令或直接与她聊天..."
            />
            <button class="composer-send-btn" :disabled="!connected" title="发送消息">
              <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
            </button>
          </div>
        </form>
      </section>

      <section class="debug-section" v-if="activeCabin === 'inspector'">
        <!-- Closet Wardrobe -->
        <div class="runtime-settings" style="margin-bottom: 20px;">
          <div class="runtime-settings-head">
            <strong>个性化装扮 (Cosplay Closet)</strong>
            <span>点击进行穿戴</span>
          </div>
          <div class="closet-grid">
            <button
              type="button"
              class="accessory-card"
              :class="{ equipped: equippedAccessories.hat }"
              @click="toggleAccessory('hat')"
            >
              🎓 巫师帽
            </button>
            <button
              type="button"
              class="accessory-card"
              :class="{ equipped: equippedAccessories.glasses }"
              @click="toggleAccessory('glasses')"
            >
              🕶️ 酷墨镜
            </button>
            <button
              type="button"
              class="accessory-card"
              :class="{ equipped: equippedAccessories.ears }"
              @click="toggleAccessory('ears')"
            >
              🐰 兔耳朵
            </button>
          </div>
        </div>

        <div class="runtime-settings memory-settings">
          <div class="runtime-settings-head">
            <strong>记忆舱</strong>
            <div class="memory-head-actions">
              <label class="memory-enable-toggle">
                <input type="checkbox" :checked="memoryEnabled" @change="toggleMemoryEnabled" />
                <span>{{ memoryEnabled ? '已开启' : '已关闭' }}</span>
              </label>
              <button type="button" class="memory-link-button" @click="refreshMemoryStatus">刷新</button>
              <button type="button" class="memory-link-button danger" :disabled="!pendingMemories.length && !recentMemories.length" @click="clearMemory">清空</button>
            </div>
          </div>
          <p class="memory-disabled-note" v-if="!memoryEnabled">长期记忆已关闭，新候选不会写入待确认队列。</p>
          <div class="memory-vault-path" v-if="memoryStatus?.vault_path">{{ memoryStatus.vault_path }}</div>
          <div class="memory-list" v-if="pendingMemories.length">
            <article v-for="candidate in pendingMemories" :key="candidate.id" class="memory-row pending">
              <div>
                <strong>{{ candidate.kind || 'note' }}</strong>
                <p>{{ candidate.text }}</p>
                <span>{{ candidate.source || 'candidate' }}</span>
              </div>
              <div class="memory-actions">
                <button type="button" @click="saveMemoryCandidate(candidate.id)">记住</button>
                <button type="button" class="secondary" @click="rejectMemoryCandidate(candidate.id)">忽略</button>
              </div>
            </article>
          </div>
          <div class="memory-list" v-if="recentMemories.length">
            <article v-for="memory in recentMemories" :key="memory.id" class="memory-row">
              <div>
                <strong>{{ memory.kind || 'note' }}</strong>
                <p>{{ memory.text }}</p>
                <span>{{ memory.source || 'manual' }}</span>
              </div>
              <button type="button" class="memory-delete" @click="deleteMemory(memory.id)">删除</button>
            </article>
          </div>
          <p class="memory-empty" v-if="!pendingMemories.length && !recentMemories.length">暂无长期记忆</p>
        </div>

        <div class="section-title">
          <h2>运行设置</h2>
          <span>{{ ready?.runtime?.read_only ? '只读' : '状态' }}</span>
        </div>
        <div class="provider-grid">
          <div v-for="row in runtimeStatusRows()" :key="row.name" class="provider-card" :class="row.state">
            <header>
              <strong>{{ row.label || row.name }}</strong>
              <span>{{ providerStateLabel(row.state) }}</span>
            </header>
            <p>{{ providerSummary(row) }}</p>
            <div class="provider-meta" v-if="providerMeta(row).length">
              <span v-for="item in providerMeta(row)" :key="item">{{ item }}</span>
            </div>
          </div>
        </div>
        <div class="runtime-settings">
          <div class="runtime-settings-head">
            <strong>安全设置</strong>
            <span>非密钥字段</span>
          </div>
          <div class="runtime-controls">
            <label>
              <span>ASR</span>
              <input v-model="runtimeDraft.asr_enabled" type="checkbox" @change="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>ASR 时长</span>
              <input v-model.number="runtimeDraft.asr_max_seconds" type="number" min="1" max="600" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>ASR 体积</span>
              <input v-model.number="runtimeDraft.asr_max_bytes" type="number" min="1024" step="1024" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>ASR 超时</span>
              <input v-model.number="runtimeDraft.asr_timeout_seconds" type="number" min="1" max="300" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>TTS</span>
              <input v-model="runtimeDraft.tts_enabled" type="checkbox" @change="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>音量</span>
              <input v-model.number="runtimeDraft.tts_volume" type="number" min="0" max="2" step="0.05" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>语速</span>
              <input v-model.number="runtimeDraft.tts_speed_factor" type="number" min="0.5" max="2" step="0.05" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>系统回退</span>
              <input v-model="runtimeDraft.tts_fallback_to_system" type="checkbox" @change="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>OCR 超时</span>
              <input v-model.number="runtimeDraft.ocr_timeout_seconds" type="number" min="1" max="120" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>温度</span>
              <input v-model.number="runtimeDraft.llm_temperature" type="number" min="0" max="2" step="0.05" @input="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>Mock 模型</span>
              <input v-model="runtimeDraft.llm_use_mock" type="checkbox" @change="markRuntimeDraftDirty" />
            </label>
            <label>
              <span>操作等待</span>
              <input v-model.number="runtimeDraft.computer_post_action_settle_ms" type="number" min="0" max="10000" step="25" @input="markRuntimeDraftDirty" />
            </label>
          </div>
          <div class="runtime-actions">
            <button type="button" :disabled="!connected || runtimePreviewLoading" @click="previewRuntimeSettings">
              {{ runtimePreviewLoading ? '预览中' : '预览' }}
            </button>
            <button type="button" class="secondary" :disabled="!connected || runtimeApplyLoading || !runtimePreview?.ok || !runtimePreview?.changed" @click="applyRuntimeSettings">
              {{ runtimeApplyLoading ? '提交中' : '提交审批' }}
            </button>
          </div>
          <div class="runtime-preview" v-if="runtimePreview">
            <p>{{ runtimePreview.summary }}</p>
            <div class="runtime-preview-list" v-if="runtimePreview.changes?.length">
              <span v-for="change in runtimePreview.changes" :key="change.setting">
                <strong>{{ change.label }}</strong>{{ runtimeChangeActionLabel(change.action) }} · {{ runtimeValueKindLabel(change.value_kind) }}
              </span>
            </div>
            <div class="runtime-preview-list failed" v-if="runtimePreview.errors?.length">
              <span v-for="error in runtimePreview.errors" :key="`${error.setting}-${error.code}`">
                <strong>{{ error.setting }}</strong>{{ providerErrorLabel(error.code) || '无法应用' }}
              </span>
            </div>
          </div>
        </div>
        <div class="section-title debug-title">
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
        <span class="stage-emotion-pill">情绪 {{ activeEmotionStatus.label }} · 立绘 {{ activeEmotionStatus.sprite }}</span>
        <strong>{{ activeTask?.latest.display_card.status || currentMode }}</strong>
      </div>
      <div class="scene-line"></div>

      <div class="memory-authorize-bubble" v-if="topPendingMemory">
        <div>
          <strong>待确认记忆</strong>
          <p>{{ memoryAuthorizeText }}</p>
        </div>
        <div class="memory-authorize-actions">
          <button type="button" @mousedown.stop @click.stop="saveMemoryCandidate(topPendingMemory.id)">记住</button>
          <button type="button" class="secondary" @mousedown.stop @click.stop="rejectMemoryCandidate(topPendingMemory.id)">忽略</button>
        </div>
      </div>
      
      <!-- Mascot Container circles -->
      <div
        :class="['character', `emotion-${activeExpressionEmotion}`]"
        :title="isCompactMode ? '拖拽移动，单击输入，双击恢复主界面' : 'Joi Companion'"
        @mousedown="startMascotDrag"
        @dragstart.capture.prevent
        @selectstart.prevent
        @click.stop="handleMascotClick"
        @dblclick.stop.prevent="handleMascotDoubleClick"
      >
        <div class="character-fit" :style="accessoryFitStyle" @dragstart.capture.prevent @selectstart.prevent>
          <img
            v-if="characterImageSrc && failedImageSrc !== characterImageSrc"
            class="character-art"
            :src="characterImageSrc"
            alt="Joi Mascot Digital Companion"
            draggable="false"
            @load="failedImageSrc = ''"
            @error="failedImageSrc = characterImageSrc"
            @dragstart.prevent
            @mousedown.prevent
          />
          <div v-else class="character-fallback">{{ characterName.slice(0, 1) }}</div>
          
          <!-- Customizable Cosplay Accessories overlays -->
          <svg class="accessory-item wizard-hat" :style="{ display: equippedAccessories.hat ? 'block' : 'none' }" viewBox="0 0 140 100" fill="none" draggable="false" aria-hidden="true">
            <path d="M70 10 L40 65 L100 65 Z" fill="#4f46e5"/>
            <ellipse cx="70" cy="70" rx="60" ry="12" fill="#312e81"/>
            <path d="M48 50 Q70 45 92 50 L89 56 Q70 51 51 56 Z" fill="#facc15"/>
            <polygon points="70,18 73,26 81,26 74,31 77,39 70,34 63,39 66,31 59,26 67,26" fill="#facc15"/>
          </svg>
          
          <svg class="accessory-item glasses" :style="{ display: equippedAccessories.glasses ? 'block' : 'none' }" viewBox="0 0 100 30" fill="none" draggable="false" aria-hidden="true">
            <rect x="10" y="5" width="30" height="20" rx="3" fill="#111827"/>
            <rect x="60" y="5" width="30" height="20" rx="3" fill="#111827"/>
            <rect x="40" y="12" width="20" height="6" fill="#111827"/>
            <rect x="15" y="10" width="8" height="3" fill="#ffffff" opacity="0.7"/>
            <rect x="65" y="10" width="8" height="3" fill="#ffffff" opacity="0.7"/>
          </svg>
          
          <svg class="accessory-item bunny-ears" :style="{ display: equippedAccessories.ears ? 'block' : 'none' }" viewBox="0 0 130 80" fill="none" draggable="false" aria-hidden="true">
            <ellipse cx="40" cy="40" rx="14" ry="35" transform="rotate(-15 40 40)" fill="#fbcfe8"/>
            <ellipse cx="38" cy="40" rx="8" ry="25" transform="rotate(-15 38 40)" fill="#f472b6"/>
            <ellipse cx="90" cy="40" rx="14" ry="35" transform="rotate(15 90 40)" fill="#fbcfe8"/>
            <ellipse cx="92" cy="40" rx="8" ry="25" transform="rotate(15 92 40)" fill="#f472b6"/>
          </svg>
        </div>
        <div class="character-shadow"></div>
      </div>

      <!-- Large Speech bubble (Hidden in compact mode) -->
      <div class="speech">
        <strong>{{ characterName }}</strong>
        <span>{{ latestSpeech }}</span>
      </div>

      <!-- Voice Status Text -->
      <div class="voice-status" v-if="voiceStatusText">{{ voiceStatusText }}</div>

      <!-- Floating Comic Speech Bubble (Only compact mode) -->
      <div
        class="mini-speech-bubble"
        :class="{ active: miniSpeechActive && isCompactMode, actionable: miniBubbleHasActions }"
        @mousedown.stop
        @click.stop
      >
        <div class="mini-speech-text">{{ miniBubbleText }}</div>
        <div class="mini-approval-actions" v-if="miniBubbleHasActions">
          <button type="button" @click="resolveApproval(true)">允许执行</button>
          <button type="button" class="secondary" @click="requestMiniChange">改需求</button>
          <button type="button" class="secondary" @click="resolveApproval(false)">停下</button>
        </div>
      </div>

      <!-- Compact Mode Mini Control Dashboard -->
      <div class="mini-control-dashboard" :class="{ active: isCompactMode && miniDashboardActive }">
        <div class="mini-status-row">
          <div class="mini-task-pulse">
            <div class="mini-pulse-dot" :style="{ backgroundColor: connected ? 'var(--color-primary)' : 'var(--color-error)' }"></div>
            <span>{{ connected ? 'Joi online' : 'Core offline' }}</span>
          </div>
          <button type="button" class="mini-restore-btn" title="恢复主界面" @click="toggleCompactMode">还原</button>
        </div>
        <form class="mini-composer" @submit.prevent="submit">
          <button
            type="button"
            class="mini-mic-btn"
            :class="{ recording: voiceState === 'recording' }"
            :disabled="!connected || !asrConfigured || voiceState === 'transcribing'"
            @click="toggleVoiceInput"
            title="语音说话"
          >
            🎤
          </button>
          <input
            v-model="input"
            type="text"
            class="mini-input"
            :disabled="!connected"
            placeholder="给 Joi 下达指令..."
          />
        </form>
        <div style="font-size: 10px; color:#9ca3af; text-align:center; font-weight:600; margin-top:2px;">
          点击角色可展开/折叠此控制台
        </div>
      </div>

      <!-- Bottom Capsule Dock (Navigation Bar) -->
      <div class="floating-dock-container">
        <nav class="floating-dock">
          <button type="button" class="dock-btn" :class="{ active: activeCabin === 'chat' }" @click="activeCabin = 'chat'">
            <svg viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
            </svg>
            <span>对话舱</span>
          </button>
          <button type="button" class="dock-btn" :class="{ active: activeCabin === 'workspace' }" @click="activeCabin = 'workspace'">
            <svg viewBox="0 0 24 24">
              <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/>
            </svg>
            <span>任务流</span>
          </button>
          <button type="button" class="dock-btn" :class="{ active: activeCabin === 'inspector' }" @click="activeCabin = 'inspector'">
            <svg viewBox="0 0 24 24">
              <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
            </svg>
            <span>配置舱</span>
          </button>
        </nav>
      </div>
    </aside>

    <dialog ref="artifactDialog" class="artifact-modal" @click="handleDialogClick">
      <div class="artifact-modal-body">
        <button type="button" class="modal-close" @click="closeArtifactPreview">关闭</button>
        <div class="artifact-modal-image-frame">
          <img :src="previewArtifactSrc" alt="" @error="loadArtifactData(previewArtifact)" />
          <div class="target-overlays" v-if="targetPreviews(previewArtifactEvent || undefined, previewArtifact).length">
            <div
              v-for="(candidate, candidateIndex) in targetPreviews(previewArtifactEvent || undefined, previewArtifact)"
              :key="`modal-target-${candidateIndex}`"
              class="target-box"
              :style="targetBoxStyle(candidate)"
            >
              <span>{{ targetRank(candidate, candidateIndex) }}</span>
            </div>
          </div>
        </div>
      </div>
    </dialog>
  </main>
</template>
