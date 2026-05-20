<script setup lang="ts">
import { convertFileSrc } from '@tauri-apps/api/core'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CoreClient, type CoreStatus } from './api'
import type { AgentEvent, ComputerUseAuditArtifact, ComputerUseAuditEvent, CoreReadyPayload, RuntimeProviderStatus, VoiceAudioPayload } from './protocol'
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
const voiceState = ref<'idle' | 'recording' | 'transcribing'>('idle')
const lastTranscript = ref('')
const lastTtsError = ref('')
const nowSeconds = ref(Date.now() / 1000)
let mediaRecorder: MediaRecorder | null = null
let mediaStream: MediaStream | null = null
let audioChunks: Blob[] = []
let voiceStopTimer: number | null = null
let clockTimer: number | null = null
let currentAudio: HTMLAudioElement | null = null
let voiceEpoch = 0
const taskVoiceEpochs = new Map<string, number>()
const voiceEventEpochs = new Map<string, number>()

const client = new CoreClient({
  url: 'ws://127.0.0.1:8765',
  onStatus: (value) => (status.value = value),
  onEvent: (event) => {
    rememberVoiceEventEpoch(event)
    events.value.push(event)
  },
  onReady: (payload) => {
    ready.value = payload
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
  const text = event.voice_line?.text || ''
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
  return convertFileSrc(artifactPath(artifact))
}

function openArtifactPreview(artifact: string, event: AgentEvent) {
  previewArtifact.value = artifact
  previewArtifactEvent.value = event
}

function closeArtifactPreview() {
  previewArtifact.value = ''
  previewArtifactEvent.value = null
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

function targetSource(candidate: Record<string, unknown>) {
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

function resolveApproval(approved: boolean) {
  if (!pendingApproval.value) return
  const approvalId = approvalIdFor(pendingApproval.value)
  if (!approvalId) return
  const epoch = beginNewVoiceIntent()
  taskVoiceEpochs.set(pendingApproval.value.task_id, epoch)
  void client.resolveApproval(approvalId, approved).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '审批提交失败'
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

function playVoiceAudio(payload: VoiceAudioPayload) {
  if (payload.voice_audio_error) {
    lastTtsError.value = ttsErrorLabel(payload.voice_audio_error)
  }
  const eventEpoch = voiceEventEpochs.get(voiceAudioKey(payload))
  if (!shouldPlayVoiceAudio(eventEpoch, voiceEpoch)) return
  void playAudioPath(payload.voice_audio_path)
}

function rememberVoiceEventEpoch(event: AgentEvent) {
  let eventEpoch = taskVoiceEpochs.get(event.task_id)
  if (event.type === 'user_message' || eventEpoch === undefined) {
    eventEpoch = voiceEpoch
    taskVoiceEpochs.set(event.task_id, eventEpoch)
  }
  if (event.voice_line?.text && isSpeakableEvent(event)) {
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
})
onBeforeUnmount(() => {
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
                @click="openArtifactPreview(artifact, task.latest)"
              >
                <div class="artifact-image-frame" :style="artifactFrameStyle(task.latest, artifact)">
                  <img :src="artifactSrc(artifact)" alt="" />
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
              >
                <strong>{{ targetRank(candidate, candidateIndex) }}. {{ targetLabel(candidate) }}</strong>
                <span>{{ targetSource(candidate) }}</span>
                <span>{{ targetRegion(candidate) }}</span>
                <span>{{ targetConfidence(candidate) }}</span>
                <span>{{ targetAmbiguity(candidate) }}</span>
                <p>{{ targetReason(candidate) }}</p>
                <button type="button" :disabled="!canSelectTargetCandidate(task.latest, candidate)" @click="selectTargetCandidate(task.latest, candidate, candidateIndex)">选择</button>
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

    <div class="artifact-modal" v-if="previewArtifact" @click.self="closeArtifactPreview">
      <div class="artifact-modal-body">
        <button type="button" class="modal-close" @click="closeArtifactPreview">关闭</button>
        <div class="artifact-modal-image-frame">
          <img :src="previewArtifactSrc" alt="" />
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
    </div>
  </main>
</template>
