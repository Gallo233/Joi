<script setup lang="ts">
import { convertFileSrc, invoke, isTauri } from '@tauri-apps/api/core'
import { getCurrentWindow, LogicalSize, type PhysicalPosition, type PhysicalSize } from '@tauri-apps/api/window'
import {
  AlertCircle,
  ArrowLeft,
  ArrowUp,
  Archive,
  AppWindow,
  AudioLines,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  Code2,
  Cpu,
  File as FileIcon,
  FileClock,
  FilePlus2,
  Folder as FolderIcon,
  FolderPlus,
  Globe2,
  Gamepad2,
  Hand,
  Image as ImageIcon,
  KeyRound,
  LoaderCircle,
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
  MessageCircle,
  Mic,
  Minimize2,
  MonitorPlay,
  Palette,
  Paperclip,
  Pause,
  Play,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings,
  Settings2,
  Shield,
  ShieldCheck,
  Sparkles,
  Trash2,
  Unplug,
  UserRound,
  WalletCards,
  Zap,
  X,
} from 'lucide-vue-next'
// Headless primitives, used only where hand-rolling the behaviour is what
// produced the bugs: focus trapping, Esc, outside-click, focus return, and
// removing a closed overlay from the tab order.
import { DialogRoot, DialogTrigger, TabsContent, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch, type Component } from 'vue'
import { CoreClient, type CoreStatus } from './api'
import JoiCharacter from './components/JoiCharacter.vue'
import CharacterLibrary from './components/CharacterLibrary.vue'
import ContextRail from './components/layout/ContextRail.vue'
import ThinkingOrb from './components/ThinkingOrb.vue'
import { normalizeCharacterMotion, type CharacterMotionRequest } from './characterMotion'
import { assistantDisplayText, isAssistantPresentationEvent } from './conversationPresentation'
import type { Live2DEmotion, Live2DRuntimeMapping } from './live2d/runtime'
import type { ActionReceipt, AgentCliListResult, AgentCliModelOption, AgentCliProfile, AgentCliRuntimeStatus, AgentCliTestResult, AgentEvent, AgentSkillDraft, AgentSkillInspection, AgentSkillInstallation, ArtifactReadResult, BackgroundContextEntry, BackgroundContextScope, BackgroundContextStatus, ByokConnectResult, ByokPreset, ByokStatus, ByokTestResult, CapabilitySession, CharacterSummary, CodexRuntimeStatus, CollaborationSnapshot, ComputerUseAuditArtifact, ComputerUseAuditEvent, CoreReadyPayload, GameAdapterManifest, JoiMcpStatus, JoiProject, JoiThread, LanguageSettings, MemoryCandidate, MemoryCandidatePage, MemoryPage, MemoryRecord, MemoryStatus, MemoryVault, NativeSkill, NativeSkillManifest, PermissionProfile, ResourceBinding, RuntimeConfigMutationResult, RuntimeProviderStatus, VoiceAudioPayload, WatchLoopStatus } from './protocol'
import { settingsSubtitle, settingsTabs, settingsTitle, type SettingsTabId } from './settings'
import { asRecord, stringValue } from './composables/safeRecord'
import { errorLabel, sourceLabel } from './composables/watchLabels'
import { useByok } from './composables/useByok'
import { usePersistentRef } from './composables/usePersistentRef'
import { useWatchLoop } from './composables/useWatchLoop'
import { useBackgroundContext } from './composables/useBackgroundContext'
import { useAgentSkills } from './composables/useAgentSkills'
import { useMemory } from './composables/useMemory'
import { ProjectsContextKey, type AttachmentKind } from './shellContext'
import {
  asrLatencyLabel,
  asrRpcTimeoutMs,
  isPlayableVoiceEventType,
  nextVoiceEpoch,
  shouldPlayVoiceAudio,
  voiceGenerationId,
  voiceAudioKey,
  type AsrLatencyBreakdown,
} from './voiceRuntime'
import { attachLipSync, detachLipSync, enqueuePcm16Chunk, unlockAudioPlayback } from './voiceLipSync'
import { VoiceRecorder } from './voiceRecorder'
import { RealtimeVoiceSession, realtimeLatencyLabel, type RealtimeVoiceEvent, type RealtimeVoiceState } from './realtimeVoice'

const input = ref('')
const status = ref<CoreStatus>('offline')
const errorText = ref('')
const coreRetrying = ref(false)
interface ComposerAttachment {
  kind: AttachmentKind
  name: string
  path: string
}
const composerAttachments = ref<ComposerAttachment[]>([])
const attachmentPickerBusy = ref(false)
const attachmentPickerError = ref('')
const composerSending = ref(false)
const events = ref<AgentEvent[]>([])
const eventCursor = ref(0)
const activeApprovalIds = ref(new Set<string>())
const historyLoading = ref(false)
const expandedTaskIds = ref(new Set<string>())
const chatScrollArea = ref<HTMLElement | null>(null)
const workspaceRef = ref<HTMLElement | null>(null)
let chatPinnedToBottom = true
let chatHasAutoScrolled = false
const developerMode = ref(false)
// The most recent realtime turn's timing label, shown only in developer mode.
const lastRealtimeLatency = ref('')
const ready = ref<CoreReadyPayload | null>(null)
interface CoreConnectionInfo {
  url: string
  token: string
  protocol_version: number
  instance_id: string
  status: string
  error?: string | null
}
let expectedCoreConnection: CoreConnectionInfo | null = null
let coreConnectionGeneration = 0
const contextRailOpen = ref(false)
const contextRailBusy = ref(false)
const contextSearch = ref('')
const newProjectName = ref('')
const projectRows = ref<JoiProject[]>([])
const threadRows = ref<JoiThread[]>([])
const resourceBindings = ref<ResourceBinding[]>([])
const activeContext = ref<NonNullable<CollaborationSnapshot['active']>>({})
const activeCapabilitySession = ref<CapabilitySession | null>(null)

/**
 * The session only while it can still be acted on.
 *
 * A finished run has nothing to pause, take over or cancel, but the old card
 * kept showing all three -- which is how a completed "已结束" session went on
 * occupying the top of the conversation.
 */
const liveCapabilitySession = computed(() => {
  const session = activeCapabilitySession.value
  if (!session) return null
  return ['completed', 'failed', 'cancelled'].includes(session.state) ? null : session
})
const editingRailItem = ref<{ type: 'project' | 'thread'; id: string } | null>(null)
const editingRailValue = ref('')
const showArchivedContext = ref(false)
const previewArtifact = ref('')
const previewArtifactEvent = ref<AgentEvent | null>(null)
type CabinId = 'workspace' | 'chat' | 'memory' | 'characters' | 'inspector'
type TurnStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'failed'
type TurnStepState = 'done' | 'current' | 'waiting' | 'failed'
interface TurnStep {
  key: string
  label: string
  detail: string
  state: TurnStepState
}
interface ConversationTurn {
  taskId: string
  /** Absent when Joi spoke first: a proactive line answers no message. */
  user?: AgentEvent
  assistant?: AgentEvent
  approval?: AgentEvent
  rows: AgentEvent[]
  status: TurnStatus
  staleApproval: boolean
  steps: TurnStep[]
  hasTrace: boolean
  updatedAt: number
}

const activeCabin = ref<CabinId>('chat')
const quickMenuOpen = ref(false)
const characterMenuOpen = ref(false)
const stageBackdropEnabled = ref(true)
// Imported VRM/Live2D characters should open in the stable full-body framing
// used by the installed Joi experience. The control still lets the user opt
// into a closer portrait crop when desired.
const characterFullBody = ref(true)
// 100% now frames the whole model with a margin, so the old 110% default --
// which existed to compensate for the character sitting small in frame -- just
// crops the feet.
const stageZoom = ref(1)

// The stage used to hold roughly 60% of the window whether or not there was a
// character in it. With Core down, or a character package that ships no model
// and no sprite, that was 60% of the window painted blank while the
// conversation was squeezed into what was left.
//
// Collapsing is therefore two things: a choice the user can make and keep, and
// a fallback the shell applies on its own when there is genuinely nothing to
// draw. `stageCollapsed` is the choice; `stageHasCharacter` is the fact.
const stageCollapsed = usePersistentRef('stageCollapsed', false)
const artifactDialog = ref<HTMLDialogElement | null>(null)
const activeSettingsTab = ref<SettingsTabId>('execution')
const settingsSearch = ref('')
const fallbackLive2DModelUrl = import.meta.env.VITE_JOI_LIVE2D_MODEL_URL || '/live2d/joi/joi.model3.json'
const skillManifest = ref<NativeSkillManifest | null>(null)
const skillRefreshLoading = ref(false)
const gameAdapterRows = ref<GameAdapterManifest[]>([])
const gameAdapterNotice = ref('')
const minecraftBusy = ref(false)
const minecraftAutonomyEnabled = ref(false)
const languageSettings = ref<LanguageSettings | null>(null)
const languageBusy = ref(false)
const languageNotice = ref('')
interface AppConfirmOptions {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}
interface AppConfirmRequest extends Required<AppConfirmOptions> {
  resolve: (accepted: boolean) => void
}
const appConfirmDialog = ref<HTMLDialogElement | null>(null)
const appConfirmRequest = ref<AppConfirmRequest | null>(null)
const minecraftMode = ref<'companion' | 'delegate'>('companion')
const minecraftConnectionDraft = ref({
  host: '127.0.0.1',
  port: 0,
  username: 'Joi',
  auth: 'offline' as 'offline' | 'microsoft',
  server_id: 'local-hmcl',
  world: 'world',
  version: '',
})
const minecraftScopeDraft = ref({
  dimensions: ['overworld'] as string[],
  max_radius: 32,
  max_actions: 40,
  max_blocks_changed: 128,
  allowed_blocks: 'oak_log,oak_planks,cobblestone,crafting_table,chest,barrel',
  allowed_players: '',
  allow_build: true,
  allow_containers: true,
})
const executionMode = ref<'local_cli' | 'byok'>('local_cli')

const selectedAgentCliId = ref('codex')
const selectedAgentCliModel = ref('默认')
const selectedAgentCliReasoning = ref('XHigh')
const agentCliRows = ref<AgentCliProfile[]>([])
const agentCliLoading = ref(false)
const agentCliTestStatus = ref<Record<string, string>>({})
const agentCliRuntime = ref<AgentCliRuntimeStatus | null>(null)
const codexRuntime = ref<CodexRuntimeStatus | null>(null)
const joiMcpStatus = ref<JoiMcpStatus | null>(null)
const joiMcpInstalling = ref(false)
const agentCliSyncing = ref(false)
const agentCliCoreUnsupported = ref(false)
let agentCliSyncTimer: number | null = null



const stageZoomLabel = computed(() => `${Math.round(stageZoom.value * 100)}%`)
// A VRM zooms by moving its camera, which stays sharp and cannot push the feet
// out of frame. Scaling the canvas in CSS would do both. Live2D and static
// sprites are raster art, so scaling them is still the right tool.
const stageCharacterStyle = computed(() => ({
  '--stage-character-scale': String(characterDisplayModelType.value === 'vrm' ? 1 : stageZoom.value),
}))
const live2DModelUrl = computed(() => {
  const character = ready.value?.character
  if (!character) return ''
  if (character?.id === 'builtin-hikari' && character?.model_type === 'live2d') return fallbackLive2DModelUrl
  if (character?.model_url) return character.model_url
  if ((character?.model_type === 'live2d' || character?.model_type === 'vrm') && character.model_path) return convertFileSrc(character.model_path)
  return character?.model_type === 'static' || character?.model_type === 'vrm' ? '' : fallbackLive2DModelUrl
})
const live2DRuntimeMapping = computed<Live2DRuntimeMapping>(() => ({
  expressions: ready.value?.character?.expression_mappings || [],
  motions: ready.value?.character?.motion_mappings || [],
  lipSync: ready.value?.character?.lip_sync || {},
  // Authored .vrma clips a VRM package shipped, keyed by the semantic motion
  // they perform. Motions without one keep using the procedural pose.
  animations: Object.fromEntries(
    (ready.value?.character?.motion_mappings || [])
      .filter((motion) => motion.animation_url && (motion.motion || motion.name))
      .map((motion) => [String(motion.motion || motion.name), String(motion.animation_url)]),
  ),
}))
const characterDisplayModelType = computed(() => {
  const character = ready.value?.character
  const configuredType = character?.model_type || 'live2d'
  const hasAuthoredMotions = Boolean(character?.motion_mappings?.some((motion) => motion.motion_group))
  if (character?.id === 'builtin-hikari' && configuredType === 'live2d' && !hasAuthoredMotions) {
    return 'procedural3d' as const
  }
  return configuredType
})
const characterRenderKey = computed(() => [
  ready.value?.character?.id || 'joi',
  characterDisplayModelType.value,
  live2DModelUrl.value,
].join(':'))
const stageBackdropStyle = computed(() => {
  const background = ready.value?.character?.background_url || ready.value?.character?.background_data_url
  if (!stageBackdropEnabled.value || !background) return {}
  return {
    backgroundImage: `linear-gradient(rgba(241, 247, 255, .16), rgba(244, 249, 255, .62)), url(${background})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  }
})

const settingsIconMap: Record<SettingsTabId, Component> = {
  execution: Settings2,
  runtime: Cpu,
  memory: Brain,
  skills: Sparkles,
  language: Globe2,
  appearance: Palette,
  developer: Code2,
}

const settingsGroupDefinitions: Array<{ label: string; tabs: SettingsTabId[] }> = [
  { label: 'Joi', tabs: ['execution', 'runtime', 'memory', 'skills'] },
  { label: '体验', tabs: ['language', 'appearance'] },
  { label: '高级', tabs: ['developer'] },
]

const filteredSettingsGroups = computed(() => {
  const query = settingsSearch.value.trim().toLocaleLowerCase()
  return settingsGroupDefinitions
    .map((group) => ({
      ...group,
      tabs: group.tabs
        .map((id) => settingsTabs.find((tab) => tab.id === id))
        .filter((tab): tab is (typeof settingsTabs)[number] => Boolean(tab))
        .filter((tab) => !query || `${tab.label} ${tab.subtitle}`.toLocaleLowerCase().includes(query)),
    }))
    .filter((group) => group.tabs.length)
})

function settingsIcon(tab: SettingsTabId) {
  return settingsIconMap[tab]
}

function openCabin(cabin: CabinId) {
  activeCabin.value = cabin
  quickMenuOpen.value = false
  characterMenuOpen.value = false
  // The project sheet belongs to Workspace and must not outlive the cabin it
  // was opened from. Its trigger is already hidden in Settings, and the modal
  // scrim makes the titlebar unclickable while it is open, so a person cannot
  // normally get here -- but a sheet left open across a cabin change would sit
  // on top of a navigation it has nothing to do with.
  contextRailOpen.value = false
  if (cabin === 'inspector' && activeSettingsTab.value === 'execution') {
    void refreshAgentClis()
    void refreshByokStatus()
  }
}

async function handleCharacterActivated(_characterId: string, readyPayload?: CoreReadyPayload) {
  if (readyPayload) ready.value = readyPayload
  events.value = []
  eventCursor.value = 0
  activeApprovalIds.value = new Set()
  memoryRows.value = []
  memoryPendingRows.value = []
  await nextTick()
  await Promise.all([refreshConversationHistory(), refreshMemoryWorkspace(), refreshCharacterIdentities()])
}

function cycleStageZoom() {
  const zoomSteps = [1, 1.1, 1.2]
  const currentIndex = zoomSteps.findIndex((value) => value === stageZoom.value)
  stageZoom.value = zoomSteps[(currentIndex + 1) % zoomSteps.length]
}

const isCompactMode = ref(false)
const compactTransitioning = ref(false)
const equippedAccessories = ref({ hat: false, glasses: false, ears: false })
const miniSpeechActive = ref(false)
const miniDashboardActive = ref(false)
let miniSpeechTimer: number | null = null

interface NormalWindowSnapshot {
  size: PhysicalSize
  position: PhysicalPosition
  maximized: boolean
}

let normalWindowSnapshot: NormalWindowSnapshot | null = null

async function toggleCompactMode() {
  if (compactTransitioning.value) return
  compactTransitioning.value = true
  const nextCompactMode = !isCompactMode.value
  try {
    clearMiniSpeechTimer()
    miniSpeechActive.value = false
    miniDashboardActive.value = false
    if (nextCompactMode) {
      isCompactMode.value = true
      document.body.classList.add('transparent-active')
      await applyWindowShellMode(true)
      return
    }
    await applyWindowShellMode(false)
    isCompactMode.value = false
    document.body.classList.remove('transparent-active')
  } finally {
    compactTransitioning.value = false
  }
}

async function applyWindowShellMode(compact: boolean) {
  try {
    const appWindow = getCurrentWindow()
    if (compact) {
      if (!normalWindowSnapshot) {
        try {
          normalWindowSnapshot = {
            size: await appWindow.outerSize(),
            position: await appWindow.outerPosition(),
            maximized: await appWindow.isMaximized(),
          }
        } catch (e) {
          normalWindowSnapshot = null
        }
      }
      if (normalWindowSnapshot?.maximized) {
        await safeWindowCall(() => appWindow.unmaximize())
      }
      await safeWindowCall(() => appWindow.setTitleBarStyle('overlay'))
      await setNativeWindowControlsVisible(false)
      await safeWindowCall(() => appWindow.setShadow(false))
      await safeWindowCall(() => appWindow.setAlwaysOnTop(true))
      await safeWindowCall(() => appWindow.setSkipTaskbar(true))
      await safeWindowCall(() => appWindow.setResizable(false))
      await safeWindowCall(() => appWindow.setSize(compactWindowSize()))
      return
    }
    await safeWindowCall(() => appWindow.setResizable(true))
    await safeWindowCall(() => appWindow.setSkipTaskbar(false))
    await safeWindowCall(() => appWindow.setAlwaysOnTop(false))
    if (normalWindowSnapshot?.maximized) {
      await safeWindowCall(() => appWindow.setPosition(normalWindowSnapshot!.position))
      await safeWindowCall(() => appWindow.maximize())
    } else if (normalWindowSnapshot) {
      await safeWindowCall(() => appWindow.setSize(normalWindowSnapshot!.size))
      await safeWindowCall(() => appWindow.setPosition(normalWindowSnapshot!.position))
    } else {
      await safeWindowCall(() => appWindow.setSize(new LogicalSize(1120, 760)))
    }
    await safeWindowCall(() => appWindow.setTitleBarStyle('overlay'))
    await setNativeWindowControlsVisible(true)
    await safeWindowCall(() => appWindow.setShadow(true))
    normalWindowSnapshot = null
  } catch (e) {
    // Browser preview fallback.
  }
}

async function setNativeWindowControlsVisible(visible: boolean) {
  try {
    await invoke('set_macos_window_controls_visible', { visible })
  } catch (e) {
    // Browser preview and non-macOS fallback.
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

function handleTitlebarDoubleClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, input, select, textarea, a, [role="button"], .topbar-actions')) return
  void toggleMaximizeWindow()
}

async function startWindowDrag(event: MouseEvent) {
  if (event.button !== 0) return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, input, select, textarea, a, [role="button"], .topbar-actions')) return
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
const lastAsrLatency = ref<AsrLatencyBreakdown>({})
const realtimeVoiceState = ref<RealtimeVoiceState>('idle')
const realtimeAssistantTranscript = ref('')
// A realtime turn has its own motion channel rather than a stored agent event:
// the session is ephemeral, and the synthetic turn cards this view builds for it
// carry no Core sequence, so they would always out-rank a real event and read as
// an interruption of the very motion they asked for.
const realtimeCharacterMotion = ref<CharacterMotionRequest | undefined>(undefined)
const lastTtsError = ref('')
const nowSeconds = ref(Date.now() / 1000)
const runtimeDraft = ref(defaultRuntimeDraft())
const runtimeDraftDirty = ref(false)
const runtimePreview = ref<RuntimeConfigMutationResult | null>(null)
const runtimePreviewLoading = ref(false)
const runtimeApplyLoading = ref(false)
const artifactDataUrls = ref<Record<string, string>>({})
const artifactLoadFailed = ref<Record<string, boolean>>({})
// Records WAV directly; see voiceRecorder.ts for why not MediaRecorder.
const voiceRecorder = new VoiceRecorder()
let realtimeVoiceSession: RealtimeVoiceSession | null = null
// One silent re-dial per user-initiated call. A provider drop used to end the
// call outright and leave the user to press the button again; retrying once
// covers the transient case without looping on a provider that is really down.
let realtimeReconnectUsed = false
let realtimeReconnectTimer: number | null = null
let realtimeVoiceMode: 'conversation' | 'minecraft' | null = null
let realtimeVoiceMinecraftSessionId = ''
const realtimeVoiceDisclosuresAccepted = new Set<'conversation' | 'minecraft'>()
let realtimePlaybackEpoch = 0
let voiceStopTimer: number | null = null
let clockTimer: number | null = null
let currentAudio: HTMLAudioElement | null = null
let voiceEpoch = 0
const taskVoiceEpochs = new Map<string, number>()
const voiceEventEpochs = new Map<string, number>()
const playedVoiceAudioKeys = new Set<string>()
const suppressedVoiceAudioKeys = new Set<string>()
let streamingVoiceAudioKey = ''
let streamingVoiceSequence = 0
let mascotDragStart: { x: number; y: number } | null = null
let mascotDragMoved = false
let mascotClickTimer: number | null = null

const client = new CoreClient({
  url: 'ws://127.0.0.1:8765',
  onStatus: (value) => (status.value = value),
  onEvent: (event) => {
    rememberVoiceEventEpoch(event)
    trackLiveApprovalEvent(event)
    mergeConversationEvents([event])
    syncMemoryFromEvent(event)
    syncBackgroundFromEvent(event)
    preloadImageArtifacts(event)
    if (event.session_id && ['tool_completed', 'tool_failed', 'task_completed', 'task_failed'].includes(event.type)) {
      void refreshCapabilitySession(event.session_id)
    }
  },
  onReady: (payload) => {
    if (!isExpectedCore(payload)) {
      errorText.value = 'Joi Core 身份校验失败。请重新启动 Joi；若仍然出现，请重新安装官方版本。'
      client.close()
      status.value = 'offline'
      return
    }
    ready.value = payload
    // Names for every installed character, so a transcript that outlived a
    // character switch still attributes each line to whoever said it.
    void refreshCharacterIdentities()
    syncCollaborationSnapshot(payload.collaboration)
    if (isTransientRuntimeNotice(errorText.value)) errorText.value = ''
    memoryStatus.value = payload.memory || memoryStatus.value
    backgroundStatus.value = payload.background || backgroundStatus.value
    skillManifest.value = payload.skills || skillManifest.value
    installedAgentSkills.value = payload.agent_skills || installedAgentSkills.value
    gameAdapterRows.value = payload.game_adapters || gameAdapterRows.value
    languageSettings.value = payload.language || languageSettings.value
    codexRuntime.value = payload.codex_runtime || codexRuntime.value
    joiMcpStatus.value = payload.joi_mcp || joiMcpStatus.value
    syncAgentCliFromReady(payload)
    syncRuntimeDraft(payload)
    syncByokFromReady(payload)
    activeApprovalIds.value = new Set(payload.active_approval_ids || [])
    void refreshConversationHistory()
  },
  onVoiceAudio: (payload) => void playVoiceAudio(payload),
  onRealtimeVoice: (payload) => realtimeVoiceSession?.handleCoreEvent(payload),
  onError: (message) => {
    if (isTransientRuntimeNotice(message)) return
    errorText.value = message
  },
})

const connected = computed(() => status.value === 'online')

// BYOK state and actions now live in composables/useByok.ts. Destructured back
// under their original names so the settings template binds exactly as before.
const {
  byokStatus,
  byokDraft,
  byokApiKey,
  byokLoading,
  byokDiscovering,
  byokAdvancedOpen,
  byokResult,
  byokNotice,
  byokDirty,
  byokPresets,
  selectedByokPreset,
  byokRequiresKey,
  byokSecretReady,
  byokCanConnect,
  byokKnownModels,
  syncByokStatus,
  syncByokFromReady,
  selectByokPreset,
  markByokDirty,
  byokStateLabel,
  byokSecretLabel,
  byokErrorLabel,
  refreshByokStatus,
  connectByok,
  testByokConnection,
  disconnectByok,
} = useByok(client, connected)

// Memory lives in composables/useMemory.ts. Destructured back under the same
// names so every template binding resolves exactly as before.
const {
  memoryStatus,
  pendingMemories,
  recentMemories,
  memoryQuery,
  memoryRows,
  memoryPendingRows,
  memoryView,
  memoryTotal,
  memoryHasMore,
  memoryOffset,
  memorySearchLoading,
  memoryVault,
  editingMemoryId,
  editingMemoryText,
  editingMemoryKind,
  memoryEnabled,
  memoryProfile,
  memoryProfileSections,
  memorySavedCount,
  memoryPendingCount,
  memoryProfileCountText,
  topPendingMemory,
  memoryAuthorizeText,
  memoryQueryText,
  displayedMemoryRows,
  displayedMemoryCandidates,
  memorySearchEmptyText,
  syncMemoryFromEvent,
  memoryPriorityLabel,
  refreshMemoryStatus,
  refreshMemoryWorkspace,
  loadMemoryPage,
  searchMemory,
  selectMemoryView,
  beginMemoryEdit,
  cancelMemoryEdit,
  saveMemoryEdit,
  memoryKindLabel,
  memoryTime,
  browseMemoryVault,
  saveMemoryCandidate,
  rejectMemoryCandidate,
  toggleMemoryEnabled,
  deleteMemory,
  clearMemory,
} = useMemory(client, errorText)

// AgentSkills lives in composables/useAgentSkills.ts. Destructured back
// under the same names so every template binding resolves as before.
const {
  agentSkillSource,
  agentSkillInspection,
  agentSkillBusy,
  agentSkillNotice,
  agentSkillScope,
  agentSkillScopeId,
  installedAgentSkills,
  agentSkillDrafts,
  agentSkillDraftSteps,
  agentSkillScopeLabel,
  chooseAgentSkillSource,
  inspectAgentSkill,
  installInspectedAgentSkill,
  updateAgentSkill,
  uninstallAgentSkill,
  toggleAgentSkill,
  approveAgentSkillDraft,
  rejectAgentSkillDraft,
} = useAgentSkills(client, ready, activeContext, refreshSkills)


// BackgroundContext lives in composables/useBackgroundContext.ts. Destructured back
// under the same names so every template binding resolves as before.
const {
  backgroundStatus,
  backgroundEnabled,
  backgroundScopeType,
  backgroundLoading,
  backgroundActive,
  backgroundScopes,
  backgroundRecentRows,
  backgroundScopeMeta,
  backgroundScopeLabel,
  backgroundScopeTypeLabel,
  backgroundStateText,
  backgroundSummaryText,
  backgroundRetentionLabel,
  backgroundRetentionText,
  backgroundEntryMeta,
  backgroundEntryTime,
  syncBackgroundFromEvent,
  refreshBackgroundStatus,
  toggleBackgroundEnabled,
  configureBackgroundScope,
  clearBackgroundContext,
} = useBackgroundContext(client, errorText)




const composerCanSubmit = computed(() => (
  connected.value
  && !composerSending.value
  && (Boolean(input.value.trim()) || composerAttachments.value.length > 0)
))
const connectionLabel = computed(() => {
  if (status.value === 'online') return 'Joi 就绪'
  if (status.value === 'connecting') return 'Joi 启动中'
  return 'Joi 离线'
})
const connectionPresenceLabel = computed(() => {
  if (status.value === 'online') return '在这里'
  if (status.value === 'connecting') return '正在启动 Joi'
  return '连接失败'
})

function isExpectedCore(payload: CoreReadyPayload) {
  const expected = expectedCoreConnection
  if (!expected || expected.status === 'external') return true
  return payload.product === 'joi-core'
    && payload.protocol_version === expected.protocol_version
    && payload.instance_id === expected.instance_id
}

async function connectToCore(restart = false) {
  const generation = ++coreConnectionGeneration
  client.close()
  status.value = 'connecting'
  errorText.value = ''
  let url = 'ws://127.0.0.1:8765'
  if (!isTauri()) {
    expectedCoreConnection = null
    client.connect(url)
    return
  }
  try {
    if (restart) await invoke<CoreConnectionInfo>('restart_core')
    // Keep first-launch platform verification bounded. The packaged Core is an
    // onedir runtime, so normal starts no longer repeat PyInstaller extraction.
    const deadline = Date.now() + 30000
    let connection: CoreConnectionInfo | null = null
    while (Date.now() < deadline && generation === coreConnectionGeneration) {
      connection = await invoke<CoreConnectionInfo>('core_connection_info')
      if (connection.status === 'error') {
        throw new Error(connection.error || 'Joi Core 无法启动')
      }
      if (connection.status === 'ready' || connection.status === 'external') break
      await new Promise((resolve) => window.setTimeout(resolve, 75))
    }
    if (generation !== coreConnectionGeneration) return
    if (!connection || !['ready', 'external'].includes(connection.status)) {
      throw new Error('Joi Core 启动超过 30 秒。请点“重新连接”；若仍失败，请退出其他 Joi 后再试。')
    }
    if (!connection.url) throw new Error('Joi Core 没有返回连接地址')
    expectedCoreConnection = connection
    const endpoint = new URL(connection.url)
    if (connection.token) endpoint.searchParams.set('token', connection.token)
    url = endpoint.toString()
  } catch (error) {
    if (generation !== coreConnectionGeneration) return
    errorText.value = error instanceof Error ? error.message : String(error || 'Joi Core 无法启动')
    status.value = 'offline'
    return
  }
  client.connect(url)
}

async function retryCoreConnection() {
  if (coreRetrying.value) return
  coreRetrying.value = true
  try {
    const connection = isTauri() ? await invoke<CoreConnectionInfo>('core_connection_info') : null
    await connectToCore(connection?.status === 'error')
  } finally {
    coreRetrying.value = false
  }
}

const activeProject = computed(() => projectRows.value.find((project) => project.id === activeContext.value.project_id))
const activeThread = computed(() => threadRows.value.find((thread) => thread.id === activeContext.value.thread_id))
const visibleProjects = computed(() => projectRows.value.filter((project) => showArchivedContext.value || !project.archived))
const visibleThreads = computed(() => {
  const query = contextSearch.value.trim().toLocaleLowerCase()
  return threadRows.value.filter((thread) => {
    if (!showArchivedContext.value && thread.archived) return false
    return !query || thread.title.toLocaleLowerCase().includes(query)
  })
})
const capabilityReceipts = computed<ActionReceipt[]>(() => activeCapabilitySession.value?.receipts || [])
const latestCapabilityReceipt = computed<ActionReceipt | null>(() => capabilityReceipts.value.at(-1) || null)
const capabilityPermissionLabel = computed(() => permissionProfileLabel(activeCapabilitySession.value?.permission_profile || 'observe'))
const capabilityStateLabel = computed(() => {
  const state = activeCapabilitySession.value?.state || ''
  return ({ running: 'Joi 正在协作', paused: '已暂停，等待交接', waiting_approval: '等待确认', completed: '本次目标已完成', failed: '需要调整后重试', cancelled: '已结束' } as Record<string, string>)[state] || '准备中'
})

function syncCollaborationSnapshot(snapshot?: CollaborationSnapshot | null) {
  if (!snapshot) return
  activeContext.value = { ...(snapshot.active || {}) }
  projectRows.value = [...(snapshot.projects || projectRows.value)]
  threadRows.value = [...(snapshot.threads || threadRows.value)]
  resourceBindings.value = [...(snapshot.bindings || resourceBindings.value)]
  activeCapabilitySession.value = snapshot.capability_session?.id ? snapshot.capability_session : null
}

async function refreshCollaboration(projectId = activeContext.value.project_id || '') {
  if (!connected.value || contextRailBusy.value) return
  contextRailBusy.value = true
  try {
    const projectsResult = await client.projectList(true) as { projects?: JoiProject[]; active?: CollaborationSnapshot['active'] }
    projectRows.value = projectsResult.projects || projectRows.value
    activeContext.value = { ...(projectsResult.active || activeContext.value) }
    const targetProjectId = projectId || activeContext.value.project_id || projectRows.value[0]?.id || ''
    if (targetProjectId) {
      const [threadsResult, bindingsResult] = await Promise.all([
        client.threadList(targetProjectId, '', true) as Promise<{ threads?: JoiThread[]; active?: CollaborationSnapshot['active'] }>,
        client.resourceBindingList(targetProjectId) as Promise<{ bindings?: ResourceBinding[] }>,
      ])
      threadRows.value = threadsResult.threads || []
      resourceBindings.value = bindingsResult.bindings || []
      activeContext.value = { ...(threadsResult.active || activeContext.value) }
    }
    if (activeContext.value.session_id) await refreshCapabilitySession(activeContext.value.session_id)
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '项目列表没有刷新成功'
  } finally {
    contextRailBusy.value = false
  }
}

async function createProjectFromRail() {
  const name = newProjectName.value.trim()
  if (!name || contextRailBusy.value) return
  contextRailBusy.value = true
  try {
    const result = await client.projectCreate(name, ready.value?.character?.id || '') as { collaboration?: CollaborationSnapshot; ready?: CoreReadyPayload }
    newProjectName.value = ''
    if (result.ready) ready.value = result.ready
    if (result.collaboration) syncCollaborationSnapshot(result.collaboration)
    await refreshCollaboration(activeContext.value.project_id)
    await resetThreadHistory()
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '项目没有创建成功'
  } finally {
    contextRailBusy.value = false
  }
}

async function switchProject(projectId: string) {
  if (!projectId || contextRailBusy.value) return
  contextRailBusy.value = true
  try {
    const result = await client.threadList(projectId, '', false) as { threads?: JoiThread[] }
    const rows = result.threads || []
    if (rows[0]) await activateThread(rows[0].id)
    else {
      const created = await client.threadCreate(projectId) as { thread?: JoiThread }
      if (created.thread) await activateThread(created.thread.id)
    }
  } finally {
    contextRailBusy.value = false
  }
}

async function createThreadFromRail() {
  const projectId = activeContext.value.project_id
  if (!projectId || contextRailBusy.value) return
  try {
    const result = await client.threadCreate(projectId, '', ready.value?.character?.id || '') as { thread?: JoiThread; collaboration?: CollaborationSnapshot }
    if (result.collaboration) syncCollaborationSnapshot(result.collaboration)
    if (result.thread) await activateThread(result.thread.id)
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '新对话没有创建成功'
  }
}

async function activateThread(threadId: string) {
  if (!threadId) return
  try {
    const result = await client.threadActivate(threadId) as { ready?: CoreReadyPayload; collaboration?: CollaborationSnapshot; active?: CollaborationSnapshot['active'] }
    if (result.ready) {
      ready.value = result.ready
      syncCollaborationSnapshot(result.ready.collaboration)
    } else if (result.collaboration) syncCollaborationSnapshot(result.collaboration)
    if (result.active) activeContext.value = { ...result.active }
    await resetThreadHistory()
    await refreshCollaboration(activeContext.value.project_id)
    contextRailOpen.value = false
    activeCabin.value = 'chat'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '对话没有切换成功'
  }
}

async function resetThreadHistory() {
  events.value = []
  eventCursor.value = 0
  historyLoading.value = false
  activeApprovalIds.value = new Set()
  await nextTick()
  await refreshConversationHistory()
}

function beginRailRename(type: 'project' | 'thread', id: string, value: string) {
  editingRailItem.value = { type, id }
  editingRailValue.value = value
}

async function saveRailRename() {
  const editing = editingRailItem.value
  const value = editingRailValue.value.trim()
  if (!editing || !value) return
  if (editing.type === 'project') await client.projectUpdate(editing.id, { name: value })
  else await client.threadUpdate(editing.id, { title: value })
  editingRailItem.value = null
  editingRailValue.value = ''
  await refreshCollaboration(activeContext.value.project_id)
}

async function archiveThreadFromRail(thread: JoiThread) {
  await client.threadArchive(thread.id, !thread.archived)
  if (thread.id === activeContext.value.thread_id && !thread.archived) {
    const fallback = threadRows.value.find((row) => row.id !== thread.id && !row.archived)
    if (fallback) await activateThread(fallback.id)
  }
  await refreshCollaboration(activeContext.value.project_id)
}

async function archiveProjectFromRail(project: JoiProject) {
  await client.projectArchive(project.id, !project.archived)
  if (project.id === activeContext.value.project_id && !project.archived) {
    const fallback = projectRows.value.find((row) => row.id !== project.id && !row.archived)
    if (fallback) await switchProject(fallback.id)
  }
  await refreshCollaboration(activeContext.value.project_id)
}

async function deleteArchivedThread(thread: JoiThread) {
  if (!thread.archived) return
  await client.threadDelete(thread.id, true)
  await refreshCollaboration(activeContext.value.project_id)
}

async function deleteArchivedProject(project: JoiProject) {
  if (!project.archived) return
  await client.projectDelete(project.id, true)
  await refreshCollaboration()
}

async function bindProjectDirectory() {
  const projectId = activeContext.value.project_id
  if (!projectId || contextRailBusy.value) return
  contextRailBusy.value = true
  try {
    const selectedPaths = await invoke<string[]>('pick_attachments', { kind: 'folder' })
    for (const path of selectedPaths || []) {
      const normalized = String(path || '').trim()
      if (!normalized || resourceBindings.value.some((binding) => binding.kind === 'directory' && binding.value === normalized)) continue
      const label = normalized.split(/[\\/]/).filter(Boolean).at(-1) || normalized
      await client.resourceBindingAdd(projectId, 'directory', normalized, label)
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || '')
    if (message && !/cancel/i.test(message)) errorText.value = message
  } finally {
    contextRailBusy.value = false
    await refreshCollaboration(projectId)
  }
}

async function addTextResourceBinding(kind: 'domain' | 'application' | 'game') {
  const projectId = activeContext.value.project_id
  if (!projectId) return
  const prompts = {
    domain: ['绑定网站域名', '例如 docs.example.com'],
    application: ['绑定应用', '例如 Safari 或 com.apple.Safari'],
    game: ['绑定游戏', '例如 Minecraft'],
  } as const
  const value = window.prompt(prompts[kind][0], prompts[kind][1])?.trim()
  if (!value) return
  const normalized = kind === 'domain' ? value.replace(/^https?:\/\//, '').replace(/\/.*$/, '') : value
  await client.resourceBindingAdd(projectId, kind, normalized, normalized)
  await refreshCollaboration(projectId)
}

async function removeResourceBinding(bindingId: string) {
  if (!bindingId) return
  await client.resourceBindingRemove(bindingId)
  await refreshCollaboration(activeContext.value.project_id)
}

async function refreshCapabilitySession(sessionId = activeContext.value.session_id || '') {
  if (!sessionId) {
    activeCapabilitySession.value = null
    return
  }
  try {
    const result = await client.capabilitySessionStatus(sessionId) as { session?: CapabilitySession }
    activeCapabilitySession.value = result.session?.id ? result.session : null
    if (result.session?.id) activeContext.value = { ...activeContext.value, session_id: result.session.id }
  } catch {
    // Capability status is supplemental; chat remains usable if it is unavailable.
  }
}

async function changeCapabilityPermission(profile: PermissionProfile) {
  const session = activeCapabilitySession.value
  if (!session || session.permission_profile === profile) return
  const result = await client.permissionGrant(session.id, profile) as { session?: CapabilitySession }
  if (result.session) activeCapabilitySession.value = result.session
}

async function toggleCapabilityPause() {
  const session = activeCapabilitySession.value
  if (!session) return
  const result = session.state === 'running'
    ? await client.capabilitySessionPause(session.id) as { session?: CapabilitySession }
    : await client.capabilitySessionResume(session.id) as { session?: CapabilitySession }
  if (result.session) activeCapabilitySession.value = result.session
}

async function takeOverCapability() {
  const session = activeCapabilitySession.value
  if (!session) return
  const result = await client.capabilitySessionPause(session.id) as { session?: CapabilitySession }
  if (result.session) activeCapabilitySession.value = result.session
}

async function cancelCapability() {
  const session = activeCapabilitySession.value
  if (!session) return
  const result = await client.capabilitySessionCancel(session.id) as { session?: CapabilitySession }
  if (result.session) activeCapabilitySession.value = result.session
}

function permissionProfileLabel(profile: PermissionProfile | string) {
  return ({ observe: '观察', collaborate: '协作', delegate: '托管' } as Record<string, string>)[profile] || '观察'
}

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
    if (!activeApprovalIds.value.has(approvalIdFor(event))) continue
    const resolved = events.value.slice(index + 1).some((later) => approvalResolvedBy(event, later))
    if (!resolved) return event
  }
  return undefined
})

const conversationTurns = computed<ConversationTurn[]>(() => {
  const grouped = new Map<string, AgentEvent[]>()
  for (const event of events.value) {
    const rows = grouped.get(event.task_id) || []
    rows.push(event)
    grouped.set(event.task_id, rows)
  }
  return [...grouped.entries()]
    .map(([taskId, rows]) => buildConversationTurn(taskId, rows))
    .filter((turn): turn is ConversationTurn => Boolean(turn))
    .sort((left, right) => turnStartedAt(left) - turnStartedAt(right))
    .slice(-8)
})

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

const terminalRows = computed(() => {
  const finalTextByTask = new Map<string, string>()
  for (const event of events.value) {
    if (event.type === 'runtime_final') finalTextByTask.set(event.task_id, terminalText(event))
  }
  return events.value
    .filter((event) => {
      if (event.type === 'plan_created' || event.type === 'audit_event') return developerMode.value
      if (event.type === 'tool_started' && toolName(event) === 'codex.run') return false
      if (event.type === 'runtime_delta' && finalTextByTask.get(event.task_id) === terminalText(event)) return false
      return Boolean(terminalText(event))
    })
    .slice(-80)
    .map((event) => ({
      event,
      role: terminalRole(event),
      label: terminalLabel(event),
      text: terminalText(event),
      detail: terminalDetail(event),
    }))
})

const latestWatchLoopStatus = computed<WatchLoopStatus | undefined>(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const loop = asRecord(events.value[index].agent_state?.watch_loop)
    if ('active' in loop || stringValue(loop.session_id)) return loop as WatchLoopStatus
  }
  return ready.value?.watch_loop
})

// WatchLoop lives in composables/useWatchLoop.ts. Destructured back
// under the same names so every template binding resolves as before.
const {
  watchCommentaryInterval,
  watchProactiveEnabled,
  watchSceneMode,
  watchSpoilerLevel,
  watchTranscriptSource,
  watchVisionInterval,
  watchLoopActive,
  watchLoopMeta,
  watchLoopSourceHealth,
  watchLoopStatus,
  watchLoopTranscript,
  startWatchLoop,
  stopWatchLoop,
} = useWatchLoop(client, errorText, ready, latestWatchLoopStatus)


watch(watchLoopStatus, (status) => {
  const source = stringValue(status.transcript_source)
  if (source === 'system_audio' || source === 'ocr_subtitle' || source === 'auto') watchTranscriptSource.value = source
  if (typeof status.proactive_enabled === 'boolean') watchProactiveEnabled.value = status.proactive_enabled
  if (status.commentary_interval_seconds) watchCommentaryInterval.value = Number(status.commentary_interval_seconds)
  if (status.mode && ['quiet', 'commentary', 'translate', 'analysis', 'accessibility'].includes(status.mode)) watchSceneMode.value = status.mode as typeof watchSceneMode.value
  if (status.spoiler_level && ['none', 'current_scene', 'full'].includes(status.spoiler_level)) watchSpoilerLevel.value = status.spoiler_level as typeof watchSpoilerLevel.value
  const visionInterval = Number(status.vision_interval_ticks)
  if (Number.isFinite(visionInterval)) watchVisionInterval.value = Math.max(0, visionInterval)
})

watch(activeCabin, async (cabin) => {
  await nextTick()
  if (workspaceRef.value) workspaceRef.value.scrollTop = 0
  if (cabin === 'memory') void refreshMemoryWorkspace()
})

watch(() => events.value.length, async () => {
  const shouldScroll = chatPinnedToBottom || !chatHasAutoScrolled
  await nextTick()
  if (!shouldScroll || activeCabin.value !== 'chat' || !chatScrollArea.value) return
  chatScrollArea.value.scrollTop = chatScrollArea.value.scrollHeight
  chatHasAutoScrolled = true
})

watch(activeCabin, async (cabin) => {
  if (cabin !== 'chat') return
  await nextTick()
  if (!chatScrollArea.value) return
  chatScrollArea.value.scrollTop = chatScrollArea.value.scrollHeight
  chatPinnedToBottom = true
  chatHasAutoScrolled = true
})

watch(activeSettingsTab, (tab) => {
  if (tab === 'execution') {
    void refreshAgentClis()
    void refreshJoiMcpStatus()
    void refreshByokStatus()
  }
  if (tab === 'memory') {
    void refreshMemoryStatus()
  }
  if (tab === 'developer') void refreshBackgroundStatus()
})

watch([executionMode, selectedAgentCliId, selectedAgentCliModel, selectedAgentCliReasoning], () => {
  queueAgentCliSync()
})

watch(executionMode, (mode) => {
  if (mode === 'byok') void refreshByokStatus()
})

const latestSpeech = computed(() => {
  const latest = [...events.value]
    .reverse()
    .find((event) => event.voice_line?.text && isSpeakableEvent(event))
  return (latest ? hideRuntimeBrand(assistantDisplayText(latest)) : '')
    || ready.value?.character?.greeting
    || '我在。要看、要玩、要做点什么，都可以直接告诉我。'
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

const activeCharacterMotion = computed<CharacterMotionRequest | undefined>(() => {
  if (realtimeCharacterMotion.value) return realtimeCharacterMotion.value
  const motionEvent = [...events.value]
    .reverse()
    .find((event) => normalizeCharacterMotion(asRecord(event.agent_state?.character_motion).name))
  if (!motionEvent) return undefined
  const motionState = asRecord(motionEvent.agent_state?.character_motion)
  const motion = normalizeCharacterMotion(motionState.name)
  if (!motion) return undefined
  const motionOrder = eventOrder(motionEvent)
  const interruptingEvent = [...events.value]
    .reverse()
    .find((event) => eventOrder(event) > motionOrder && interruptsCharacterMotion(event))
  if (interruptingEvent) {
    return {
      motion: 'idle',
      eventKey: `motion-stop:${eventOrder(interruptingEvent)}`,
      durationMs: 0,
      loop: true,
      intensity: 0.25,
    }
  }
  return {
    motion,
    eventKey: String(motionEvent.event_id || `motion:${motionOrder}`),
    durationMs: Number(motionState.duration_ms || 0),
    loop: Boolean(motionState.loop),
    intensity: Number(motionState.intensity || 0.8),
  }
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
const characterGreeting = computed(() => ready.value?.character?.greeting || '我在。今天想一起做点什么？')

/**
 * Who said each line, by character id.
 *
 * A transcript outlives the character that produced it: switch characters and
 * the previous one's replies are still on screen. Labelling every message with
 * whoever happens to be active now makes one character appear to have said
 * another's words -- and since each character has its own persona, the result
 * reads as the new character introducing itself by the old one's name.
 *
 * Every event already carries its `character_id`; this is the missing half,
 * the names to resolve them against. Seeded from the library so it also covers
 * messages written before this app run.
 */
const characterIdentities = ref<Record<string, { name: string; avatar: string }>>({})

async function refreshCharacterIdentities() {
  if (!connected.value) return
  try {
    const result = await client.characterList() as { ok?: boolean; characters?: CharacterSummary[] }
    if (!result.ok) return
    const rows: Record<string, { name: string; avatar: string }> = {}
    for (const row of result.characters || []) {
      if (row.id) rows[row.id] = { name: row.name || '', avatar: row.avatar_data_url || row.avatar_url || '' }
    }
    characterIdentities.value = rows
  } catch {
    // Names are a display nicety; failing to load them falls back to the
    // active character rather than blanking the transcript.
  }
}

/** The name to show against one message, not the name of whoever is active. */
function authorName(event?: AgentEvent) {
  const id = event?.character_id || ''
  return (id && characterIdentities.value[id]?.name) || characterName.value
}

/** The avatar to show against one message, for the same reason. */
function authorAvatar(event?: AgentEvent) {
  const id = event?.character_id || ''
  if (id && id !== ready.value?.character?.id) return characterIdentities.value[id]?.avatar || ''
  return characterAvatarSrc.value
}

const characterImageSrc = computed(() => {
  const sprites = ready.value?.character?.sprites || []
  const active = sprites.find((sprite) => sprite.id === activeSpriteId.value) || sprites[0]
  return active?.image_data_url || ready.value?.character?.portrait_url || ready.value?.character?.portrait_data_url || ''
})
const characterAvatarSrc = computed(() => (
  ready.value?.character?.avatar_url
  || ready.value?.character?.avatar_data_url
  || characterImageSrc.value
))

// Nothing to draw: no Live2D/VRM model and no sprite. True when Core is down,
// and true for a character package that ships neither.
const stageHasCharacter = computed(() => Boolean(live2DModelUrl.value || characterImageSrc.value))

// What the layout actually does. The user's choice wins when there is a
// character; with nothing to draw, the stage collapses regardless, because a
// blank half-window is not a view of anything.
const stageIsCollapsed = computed(() => stageCollapsed.value || !stageHasCharacter.value)

const currentMode = computed(() => {
  if (activeCabin.value === 'characters') return '角色库'
  if (pendingApproval.value) return '等待确认'
  if (watchLoopActive.value) return '陪看'
  if (codexRuntime.value?.status === 'running') return '运行中'
  const latest = [...events.value].reverse().find((event) => intentName(event) || toolName(event))
  const intent = latest ? intentName(latest) : ''
  const tool = latest ? toolName(latest) : ''
  if (intent === 'game_assist' || tool === 'game.ok_ww.run') return '游戏'
  if (intent === 'computer_use' || tool.startsWith('computer.')) return '电脑操作'
  if (intent === 'semantic_target' || tool === 'vision.resolve_target') return '目标定位'
  if (intent === 'watch_together' || intent === 'watch_followup' || intent === 'browser' || tool === 'watch.recall' || tool.startsWith('browser.')) return '陪看'
  return '平静'
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
const realtimeVoiceConfigured = computed(() => Boolean(ready.value?.realtime_voice?.configured))
const realtimeVoiceActive = computed(() => !['idle', 'error'].includes(realtimeVoiceState.value))
const activeMinecraftSessionId = computed(() => {
  const session = activeCapabilitySession.value
  return session?.driver === 'minecraft_game_adapter_v2' && session.state === 'running' ? session.id : ''
})
const minecraftAdapter = computed(() => gameAdapterRows.value.find((adapter) => adapter.id === 'minecraft'))
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
  if (realtimeVoiceState.value === 'connecting') return '实时语音连接中…'
  if (realtimeVoiceState.value === 'user_speaking') return '实时语音：正在听你说'
  if (realtimeVoiceState.value === 'assistant_speaking') return 'Joi 正在回答，可直接开口打断'
  // Listening, thinking, acting and paused all belong to a live realtime turn,
  // so show what that turn is doing. Falling through here reported the
  // dictation microphone instead, which is not the one that is open.
  if (realtimeVoiceActive.value) {
    const spoken = realtimeAssistantTranscript.value || '实时语音已连接，再次点击“结束实时语音”退出。'
    const latency = developerMode.value ? lastRealtimeLatency.value : ''
    return latency ? `${spoken} · ${latency}` : spoken
  }
  if (!asrConfigured.value) return 'ASR 未配置，请先在 config.yaml 中启用语音识别。'
  if (voiceState.value === 'recording') return `录音中，最长 ${voiceMaxSeconds.value} 秒。`
  if (voiceState.value === 'transcribing') return '转写中...'
  if (lastTranscript.value) {
    const latency = developerMode.value ? asrLatencyLabel(lastAsrLatency.value) : ''
    return `识别：${lastTranscript.value}${latency ? ` · ${latency}` : ''}`
  }
  return ''
})

// Once signaling Core is gone, fail closed instead of leaving a provider-side
// microphone session alive with no Joi connection status behind it.
watch(connected, (isConnected) => {
  if (!isConnected && realtimeVoiceActive.value) stopRealtimeVoice()
})

// A realtime motion outlives its session only as long as the session does.
// Provider loss ends a session without going through the stop button, and a
// motion left behind would shadow the stored conversation's own motions.
watch(realtimeVoiceActive, (active) => {
  if (!active) realtimeCharacterMotion.value = undefined
})

function eventIdentity(event: AgentEvent) {
  if (event.event_id) return event.event_id
  return [event.task_id, event.type, event.created_at, event.display_card?.title, event.display_card?.summary].join(':')
}

function compareEvents(left: AgentEvent, right: AgentEvent) {
  const leftSequence = Number(left.sequence || 0)
  const rightSequence = Number(right.sequence || 0)
  if (leftSequence > 0 && rightSequence > 0 && leftSequence !== rightSequence) return leftSequence - rightSequence
  if (left.created_at !== right.created_at) return left.created_at - right.created_at
  return eventIdentity(left).localeCompare(eventIdentity(right))
}

function mergeConversationEvents(incoming: AgentEvent[]) {
  if (!incoming.length) return
  const byId = new Map(events.value.map((event) => [eventIdentity(event), event]))
  for (const event of incoming) {
    if (!event?.task_id || !event?.type) continue
    byId.set(eventIdentity(event), event)
    eventCursor.value = Math.max(eventCursor.value, Number(event.sequence || 0))
  }
  events.value = [...byId.values()].sort(compareEvents).slice(-400)
}

async function refreshConversationHistory() {
  if (historyLoading.value || !connected.value) return
  historyLoading.value = true
  try {
    const result = (await client.conversationHistory(eventCursor.value, eventCursor.value ? 240 : 320, activeContext.value.thread_id || '')) as {
      ok?: boolean
      events?: AgentEvent[]
      latest_sequence?: number
      active_approval_ids?: string[]
    }
    if (Array.isArray(result.events)) mergeConversationEvents(result.events)
    activeApprovalIds.value = new Set(result.active_approval_ids || [])
    eventCursor.value = Math.max(eventCursor.value, Number(result.latest_sequence || 0))
  } catch (error) {
    if (!isTransientRuntimeNotice(error instanceof Error ? error.message : '')) {
      errorText.value = error instanceof Error ? error.message : '对话恢复失败'
    }
  } finally {
    historyLoading.value = false
  }
}

function approvalResolvedBy(approval: AgentEvent, later: AgentEvent) {
  if (later.task_id !== approval.task_id || later.created_at < approval.created_at) return false
  if (later.type === 'approval_required') return eventIdentity(later) !== eventIdentity(approval)
  if (['tool_started', 'tool_completed', 'tool_failed', 'task_completed', 'task_failed', 'runtime_final', 'runtime_error'].includes(later.type)) return true
  const state = later.agent_state || {}
  const approvalState = asRecord(state.approval)
  const status = stringValue(approvalState.status || state.approval_status).toLocaleLowerCase()
  return ['approved', 'rejected', 'resolved', 'cancelled', 'expired'].includes(status)
}

function trackLiveApprovalEvent(event: AgentEvent) {
  const next = new Set(activeApprovalIds.value)
  if (event.type === 'approval_required') {
    const approvalId = approvalIdFor(event)
    if (approvalId) next.add(approvalId)
  } else {
    for (const candidate of events.value) {
      if (candidate.type !== 'approval_required' || candidate.task_id !== event.task_id) continue
      if (!approvalResolvedBy(candidate, event)) continue
      const approvalId = approvalIdFor(candidate)
      if (approvalId) next.delete(approvalId)
    }
  }
  activeApprovalIds.value = next
}

function buildConversationTurn(taskId: string, sourceRows: AgentEvent[]): ConversationTurn | undefined {
  const rows = [...sourceRows].sort(compareEvents)
  const user = rows.find((event) => event.type === 'user_message')
  // A turn normally starts with what the user said. Joi speaking on her own
  // initiative has no such message, and dropping it would have made her
  // proactive lines audible but invisible.
  if (!user && !rows.some((event) => isProactiveSpeech(event))) return undefined
  const approval = [...rows]
    .reverse()
    .find((event, reverseIndex) => {
      if (event.type !== 'approval_required') return false
      if (!activeApprovalIds.value.has(approvalIdFor(event))) return false
      const index = rows.length - reverseIndex - 1
      return !rows.slice(index + 1).some((later) => approvalResolvedBy(event, later))
    })
  const decisive = [...rows].reverse().find((event) => [
    'runtime_started',
    'runtime_delta',
    'runtime_final',
    'runtime_error',
    'approval_required',
    'tool_started',
    'tool_completed',
    'tool_failed',
    'task_completed',
    'task_failed',
  ].includes(event.type))
  const staleApproval = decisive?.type === 'approval_required' && !approval
  let status: TurnStatus = 'queued'
  if (approval) status = 'waiting'
  else if (staleApproval) status = 'failed'
  else if (decisive && ['runtime_error', 'tool_failed', 'task_failed'].includes(decisive.type)) status = 'failed'
  else if (decisive && ['runtime_final', 'tool_completed', 'task_completed'].includes(decisive.type)) status = 'completed'
  else if (decisive) status = 'running'

  const assistant = [...rows].reverse().find((event) => isAssistantPresentationEvent(event.type))
  const hasExecution = rows.some((event) => [
    'plan_created', 'runtime_started', 'runtime_delta', 'runtime_error', 'approval_required',
    'skill_started', 'skill_completed', 'tool_started', 'tool_failed', 'task_completed', 'task_failed',
  ].includes(event.type) && !isCompanionChat(event) && !isCharacterMotion(event))
  const companionOnly = rows.every(isCompanionOnlyEvent)
  const simpleCompanionReply = status === 'completed'
    && Boolean(assistant && (isCompanionChat(assistant) || isCharacterMotion(assistant)))
    && !hasExecution
  return {
    taskId,
    user,
    assistant,
    approval,
    rows,
    status,
    staleApproval,
    steps: buildTurnSteps(rows, status),
    hasTrace: !companionOnly && !simpleCompanionReply && (hasExecution || status !== 'completed'),
    updatedAt: rows[rows.length - 1]?.created_at || user?.created_at || 0,
  }
}

function buildTurnSteps(rows: AgentEvent[], status: TurnStatus): TurnStep[] {
  const ordered = new Map<string, TurnStep>()
  const upsert = (key: string, label: string, detail: string) => {
    if (ordered.has(key)) ordered.delete(key)
    ordered.set(key, { key, label, detail, state: 'done' })
  }
  upsert('received', '收到请求', 'Joi 已接收这条消息')
  for (const event of rows) {
    const detail = hideRuntimeBrand(event.display_card?.summary || '')
    if (event.type === 'plan_created') upsert('understand', '理解目标', detail || '正在整理任务')
    else if (event.type === 'runtime_started') upsert('execute', '开始处理', detail || 'Joi 已开始执行')
    else if (event.type === 'runtime_delta') upsert('progress', '正在推进', detail || '状态持续更新中')
    else if (event.type === 'skill_started' || event.type === 'tool_started') upsert('execute', '使用能力', detail || '正在执行所需步骤')
    else if (event.type === 'approval_required') upsert('approval', '等待确认', detail || '需要你的确认才能继续')
    else if (event.type === 'skill_completed' || event.type === 'tool_completed') upsert('result', '整理结果', detail || '执行步骤已完成')
    else if (event.type === 'runtime_final' || event.type === 'task_completed') upsert('done', '处理完成', detail || '请求已完成')
    else if (event.type === 'runtime_error' || event.type === 'tool_failed' || event.type === 'task_failed') upsert('failed', '处理受阻', detail || '这次没有顺利完成')
  }
  let steps = [...ordered.values()]
  if (steps.length > 4) steps = [steps[0], ...steps.slice(-3)]
  const last = steps[steps.length - 1]
  if (last) {
    if (status === 'running' || status === 'queued') last.state = 'current'
    if (status === 'waiting') last.state = 'waiting'
    if (status === 'failed') last.state = 'failed'
  }
  return steps
}

function turnStatusLabel(turn: ConversationTurn) {
  if (turn.staleApproval) return '确认已失效'
  if (turn.status === 'waiting') return '需要你的确认'
  if (turn.status === 'failed') return '没有完成'
  if (turn.status === 'completed') return '已完成'
  if (turn.status === 'queued') return '已收到'
  return '正在处理'
}

function turnProgressLabel(turn: ConversationTurn) {
  const progress = [...turn.rows].reverse().find((event) => Boolean(event.agent_state?.ui_transient && event.agent_state?.ui_label))
  const label = String(progress?.agent_state?.ui_label || '').trim()
  return label || `${characterName.value} 正在想`
}

/**
 * Which orb to show for what is actually happening.
 *
 * Driven by the running tool rather than by a generic "busy", so the shape
 * carries real information: a globe being scanned while Joi reads the screen,
 * orbits while it acts on it. Anything unrecognised falls back to `working` --
 * a wrong-but-plausible animation would be presentation inventing status,
 * which is the one thing the trust surface must not do.
 */
function turnOrbState(turn: ConversationTurn) {
  if (voiceState.value === 'recording') return 'listening'
  const running = [...turn.rows].reverse().find((event) => event.agent_state?.ui_transient)
  const tool = String(running?.agent_state?.tool || toolName(running || turn.rows[turn.rows.length - 1]) || '')
  if (tool.startsWith('observe.') || tool.startsWith('watch.') || tool.startsWith('vision.')) return 'searching'
  if (tool.startsWith('computer.')) return 'working'
  if (tool.startsWith('browser.')) return 'connecting'
  if (tool.startsWith('codex.') || tool.startsWith('agent_cli.')) return 'solving'
  if (tool.startsWith('files.') || tool.startsWith('mcp.')) return 'connecting'
  if (tool === 'companion.chat' || !tool) return 'breathing'
  return 'working'
}

function turnStatusText(turn: ConversationTurn) {
  if (turn.staleApproval) return '应用已重新启动，请重试这条请求以重新确认'
  if (turn.status === 'completed' && turn.assistant) return '执行过程已收起'
  const latest = [...turn.rows].reverse().find((event) => {
    if (event.type === 'user_message' || event.type === 'audit_event') return false
    const summary = hideRuntimeBrand(event.display_card?.summary || '')
    return Boolean(summary && summary !== 'Joi 状态已更新。')
  })
  return latest ? hideRuntimeBrand(latest.display_card.summary) : '正在准备处理这条请求'
}

function turnStartedAt(turn: ConversationTurn) {
  return turn.user?.created_at ?? turn.assistant?.created_at ?? turn.rows[0]?.created_at ?? 0
}

function turnTimeLabel(turn: ConversationTurn) {
  if (turn.status === 'running' || turn.status === 'waiting' || turn.status === 'queued') {
    const seconds = Math.max(1, Math.floor(nowSeconds.value - turnStartedAt(turn)))
    if (seconds < 60) return `${seconds} 秒`
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  }
  return new Date(turn.updatedAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function isTurnExpanded(turn: ConversationTurn) {
  // Failure opens itself. A turn that worked has nothing to read, so its steps
  // stay folded and cost the conversation one line; a turn that did not is
  // exactly when the steps are the answer, and making the user hunt for a
  // disclosure triangle to find out why is the wrong default.
  if (turn.status === 'waiting' || turn.status === 'failed') return true
  return expandedTaskIds.value.has(turn.taskId)
}

function toggleTurnTrace(turn: ConversationTurn) {
  const next = new Set(expandedTaskIds.value)
  if (next.has(turn.taskId)) next.delete(turn.taskId)
  else next.add(turn.taskId)
  expandedTaskIds.value = next
}

function onChatScroll() {
  const element = chatScrollArea.value
  if (!element) return
  chatPinnedToBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120
}

async function retryTurn(turn: ConversationTurn) {
  // Nothing to resend when Joi started the exchange herself.
  if (!turn.user || !connected.value || composerSending.value) return
  composerSending.value = true
  errorText.value = ''
  beginNewVoiceIntent()
  try {
    await client.sendUserText(turn.user.display_card.summary, activeContext.value.thread_id || '')
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '重试失败'
  } finally {
    composerSending.value = false
  }
}

function toolName(event: AgentEvent) {
  const tool = event.agent_state?.tool
  return typeof tool === 'string' ? tool : ''
}

function intentName(event: AgentEvent) {
  const intent = event.agent_state?.intent
  return typeof intent === 'string' ? intent : ''
}

function skillName(event: AgentEvent) {
  const skillId = event.agent_state?.skill_id
  return typeof skillId === 'string' ? skillId : ''
}

function terminalRole(event: AgentEvent) {
  if (event.type === 'user_message') return 'human'
  if (event.type === 'approval_required') return 'approval'
  if (event.type === 'runtime_error' || event.type === 'tool_failed' || event.type === 'task_failed') return 'error'
  if (event.type === 'skill_completed' || event.type === 'tool_completed') return 'skill'
  return 'joi'
}

function terminalLabel(event: AgentEvent) {
  if (event.type === 'user_message') return '你'
  if (event.type === 'approval_required') return '需要确认'
  if (terminalRole(event) === 'skill') return 'Joi'
  return 'Joi'
}

function terminalText(event: AgentEvent) {
  const summary = hideRuntimeBrand(event.display_card?.summary || '')
  if (event.type === 'runtime_delta' && summary === 'Joi 状态已更新。') return ''
  return summary || hideRuntimeBrand(event.voice_line?.text || '')
}

function terminalDetail(event: AgentEvent) {
  const body = hideRuntimeBrand(event.display_card?.body || '')
  if (!body || body === terminalText(event)) return ''
  return body
}

function hideRuntimeBrand(value: string) {
  return String(value || '').replace(/\bCodex\b/gi, 'Joi')
}

function isCompanionChat(event: AgentEvent) {
  return toolName(event) === 'companion.chat' || (event.type === 'tool_completed' && event.display_card.title === '对话')
}

/**
 * A character motion is the character expressing itself, not work being done.
 * Reporting "收到请求 / 理解目标 / 使用能力" for a wave is describing a person
 * as a pipeline, so these turns present like speech.
 */
function isCharacterMotion(event: AgentEvent) {
  return toolName(event) === 'character.perform'
}

/** Joi speaking without being asked: her own line, not a task she ran. */
function isProactiveSpeech(event: AgentEvent) {
  return event.type === 'tool_completed' && Boolean(asRecord(event.agent_state).proactive)
}

function isCompanionOnlyEvent(event: AgentEvent) {
  return event.type === 'user_message'
    || event.type === 'audit_event'
    || isCompanionChat(event)
    || isCharacterMotion(event)
}

/** What the character shows for a turn; speech remains in the audio pipeline. */
function assistantText(event: AgentEvent) {
  return hideRuntimeBrand(assistantDisplayText(event))
}

function isTaskCardEvent(event: AgentEvent) {
  if (event.type === 'user_message' || isCompanionChat(event) || isCharacterMotion(event)) return false
  if (event.type === 'tool_started' && toolName(event) === 'codex.run') return true
  return ['approval_required', 'tool_completed', 'tool_failed', 'task_completed', 'task_failed'].includes(event.type)
}

function isSpeakableEvent(event: AgentEvent) {
  if (event.type === 'user_message' || event.type === 'plan_created' || event.type === 'tool_started' || event.type === 'audit_event') return false
  return isSafeVoiceText(event.voice_line?.text || '')
}

function isPlayableVoiceEvent(event: AgentEvent) {
  return isPlayableVoiceEventType(event.type) && isSafeVoiceText(event.voice_line?.text || '')
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
  return artifact
}

function artifactSrc(artifact: string) {
  if (artifactDataUrls.value[artifact]) return artifactDataUrls.value[artifact]
  if (/^[a-zA-Z]:[\\/]/.test(artifact) || artifact.startsWith('/')) return convertFileSrc(artifactPath(artifact))
  return ''
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


function numberTuple(value: unknown) {
  if (!Array.isArray(value) || value.length !== 4) return undefined
  const numbers = value.map((item) => Number(item))
  return numbers.every((item) => Number.isFinite(item)) ? numbers : undefined
}




function isTransientRuntimeNotice(value: string) {
  const text = String(value || '').trim().toLowerCase()
  return text === 'joi runtime is starting'
    || text === 'joi runtime connection failed'
    || text === 'core bridge is offline'
    || text === 'core bridge connection failed'
}

function expressionEmotionClass(value: string): Live2DEmotion {
  const normalized = value.trim().toLowerCase().replace(/\s+/g, '_')
  return ['happy', 'thinking', 'alert', 'worried', 'serious', 'neutral'].includes(normalized)
    ? normalized as Live2DEmotion
    : 'neutral'
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

function eventOrder(event: AgentEvent) {
  const sequence = Number(event.sequence || 0)
  return sequence > 0 ? sequence : Math.round(Number(event.created_at || 0) * 1000)
}

function interruptsCharacterMotion(event: AgentEvent) {
  if (event.type === 'user_message') return true
  if (['approval_required', 'runtime_error', 'tool_failed', 'task_failed'].includes(event.type)) return true
  const expressionIntent = asRecord(event.agent_state?.expression_intent)
  if (expressionIntent.locked) return true
  const phase = stringValue(event.public_phase || event.agent_state?.public_phase)
  return ['waiting', 'paused', 'failed'].includes(phase)
}

function trimText(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function eventTime(event: AgentEvent) {
  return new Date(event.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function attachmentName(path: string) {
  const normalized = path.replace(/[\\/]+$/, '')
  return normalized.split(/[\\/]/).pop() || normalized
}

async function addAttachments(kind: AttachmentKind) {
  if (attachmentPickerBusy.value) return
  attachmentPickerBusy.value = true
  attachmentPickerError.value = ''
  try {
    const selectedPaths = await invoke<string[]>('pick_attachments', { kind })
    if (!selectedPaths.length) return

    const existingPaths = new Set(composerAttachments.value.map((attachment) => attachment.path))
    const additions = selectedPaths
      .filter((path) => !existingPaths.has(path))
      .map((path) => ({ kind, name: attachmentName(path), path }))
    const availableSlots = Math.max(0, 20 - composerAttachments.value.length)
    composerAttachments.value.push(...additions.slice(0, availableSlots))
    if (additions.length > availableSlots) {
      attachmentPickerError.value = '单条消息最多添加 20 个文件或文件夹。'
    }
    quickMenuOpen.value = false
  } catch (error) {
    attachmentPickerError.value = error instanceof Error ? error.message : '没有打开系统选择器，请再试一次。'
  } finally {
    attachmentPickerBusy.value = false
  }
}

function removeAttachment(path: string) {
  composerAttachments.value = composerAttachments.value.filter((attachment) => attachment.path !== path)
  attachmentPickerError.value = ''
}

function attachmentPrompt(attachments: ComposerAttachment[]) {
  return attachments
    .map((attachment) => `- ${attachment.kind === 'folder' ? '文件夹' : '文件'}：${attachment.path.replace(/[\r\n]+/g, ' ')}`)
    .join('\n')
}

async function submit() {
  if (!composerCanSubmit.value) return
  const text = input.value.trim()
  const attachments = [...composerAttachments.value]
  const requestText = attachments.length
    ? `${text || '请读取并处理这些本地附件。'}\n\n本地附件（可直接读取）：\n${attachmentPrompt(attachments)}`
    : text
  if (!requestText) return
  beginNewVoiceIntent()
  errorText.value = ''
  composerSending.value = true
  try {
    await client.sendUserText(requestText, activeContext.value.thread_id || '')
    input.value = ''
    composerAttachments.value = []
    attachmentPickerError.value = ''
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '发送失败'
  } finally {
    composerSending.value = false
  }
}

function sendChatStarter(text: string) {
  if (!connected.value || composerSending.value) return
  input.value = text
  void submit()
}


function configureWatchLoop() {
  errorText.value = ''
  void client.watchLoopConfigure({
    transcript_source: watchTranscriptSource.value,
    proactive_enabled: watchProactiveEnabled.value,
    commentary_interval_seconds: watchCommentaryInterval.value,
    mode: watchSceneMode.value,
    spoiler_level: watchSpoilerLevel.value,
    vision_interval_ticks: watchVisionInterval.value,
  }).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '实时陪看设置失败'
  })
}

function changeWatchSceneMode() {
  watchProactiveEnabled.value = watchSceneMode.value !== 'quiet'
  configureWatchLoop()
}

function refreshWatchVision() {
  if (!watchLoopActive.value) return
  errorText.value = ''
  void client.watchLoopRefresh({ force_visual_summary: true }).catch((error) => {
    errorText.value = error instanceof Error ? error.message : '画面理解失败'
  })
}






























function resolveApproval(approved: boolean) {
  if (!pendingApproval.value) return
  resolveApprovalFor(pendingApproval.value, approved)
}

function resolveApprovalFor(approval: AgentEvent, approved: boolean) {
  const approvalId = approvalIdFor(approval)
  if (!approvalId) return
  const epoch = beginNewVoiceIntent()
  taskVoiceEpochs.set(approval.task_id, epoch)
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
    // The mouth reads this element's signal, so the analyser is attached
    // before playback rather than after the first frame of speech is gone.
    attachLipSync(audio)
    audio.onended = () => {
      if (currentAudio === audio) {
        currentAudio = null
        detachLipSync()
      }
    }
    await audio.play()
  } catch (error) {
    if (audio && currentAudio === audio) currentAudio = null
    // A webview refusing to start audio raises NotAllowedError, which had no
    // label and so rendered as nothing at all -- the character was silent and
    // the reason was too. Refusals now name themselves.
    const name = error instanceof Error ? error.name : ''
    lastTtsError.value = name === 'NotAllowedError' ? 'audio_blocked' : name || 'audio_play_failed'
  }
}

function playVoiceAudio(payload: VoiceAudioPayload) {
  const realtimeSessionId = stringValue(payload.realtime_session_id)
  const isRealtimeAudio = Boolean(realtimeSessionId)
  if (isRealtimeAudio) {
    if (realtimeSessionId !== realtimeVoiceSession?.sessionId) return
    const epoch = Math.max(0, Number(payload.realtime_epoch || 0))
    if (epoch !== realtimePlaybackEpoch || payload.voice_audio_source !== 'local') return
    if (payload.voice_audio_sequence === 0) realtimeVoiceState.value = 'assistant_speaking'
  } else if (!isPlayableVoiceEventType(payload.event_type)) return
  rememberVoiceLatency(payload)
  if (payload.voice_audio_error) {
    lastTtsError.value = ttsErrorLabel(payload.voice_audio_error)
  }
  const audioKey = isRealtimeAudio ? `realtime:${realtimeSessionId}:${realtimePlaybackEpoch}` : voiceAudioKey(payload)
  const isStreaming = Boolean(payload.voice_audio_pcm16_base64 || payload.voice_audio_final)
  const sequence = Math.max(0, Math.floor(Number(payload.voice_audio_sequence) || 0))
  if (isStreaming && sequence === 0 && shouldSuppressProactiveVoice(payload)) {
    suppressedVoiceAudioKeys.add(audioKey)
  }
  if (suppressedVoiceAudioKeys.has(audioKey)) {
    if (payload.voice_audio_final) suppressedVoiceAudioKeys.delete(audioKey)
    return
  }
  if (!isStreaming && shouldSuppressProactiveVoice(payload)) return
  if (!isRealtimeAudio) {
    const eventEpoch = voiceEventEpochs.get(audioKey)
    if (!shouldPlayVoiceAudio(eventEpoch, voiceEpoch)) return
  }
  if (playedVoiceAudioKeys.has(audioKey)) return
  if (isStreaming) {
    playStreamingVoiceAudio(payload, audioKey, sequence)
    if (isRealtimeAudio && payload.voice_audio_final && realtimeVoiceState.value === 'assistant_speaking') {
      realtimeVoiceState.value = 'listening'
    }
    return
  }
  rememberPlayedVoiceAudioKey(audioKey)
  void playAudioPath(payload.voice_audio_path, payload.voice_audio_data_url)
}

function rememberVoiceLatency(payload: VoiceAudioPayload) {
  const first = Number(payload.voice_audio_ttfb_ms)
  const total = Number(payload.voice_audio_total_ms)
  if (!Number.isFinite(first) && !Number.isFinite(total)) return
  const snapshot = ready.value
  if (!snapshot) return
  const tts = {
    ...(snapshot.tts || {}),
    ...(Number.isFinite(first) ? { last_ttfb_ms: Math.max(0, Math.round(first)) } : {}),
    ...(Number.isFinite(total) ? { last_total_ms: Math.max(0, Math.round(total)) } : {}),
  }
  const providers = (snapshot.runtime?.providers || []).map((row) => {
    if (row.name !== 'tts') return row
    const notes = (row.notes || []).filter((note) => !note.startsWith('first audio ') && !note.startsWith('total '))
    if (Number.isFinite(first)) notes.push(`first audio ${Math.max(0, Math.round(first))}ms`)
    if (Number.isFinite(total)) notes.push(`total ${Math.max(0, Math.round(total))}ms`)
    return { ...row, notes }
  })
  ready.value = {
    ...snapshot,
    tts,
    runtime: snapshot.runtime ? { ...snapshot.runtime, providers } : snapshot.runtime,
  }
}

function playStreamingVoiceAudio(payload: VoiceAudioPayload, audioKey: string, sequence: number) {
  if (sequence === 0) {
    stopSpokenAudio()
    streamingVoiceAudioKey = audioKey
    streamingVoiceSequence = 0
  }
  if (streamingVoiceAudioKey !== audioKey || sequence !== streamingVoiceSequence) return
  if (payload.voice_audio_pcm16_base64) {
    const accepted = enqueuePcm16Chunk(
      payload.voice_audio_pcm16_base64,
      payload.voice_audio_sample_rate || 24000,
      sequence === 0,
    )
    if (!accepted) lastTtsError.value = 'audio_play_failed'
    streamingVoiceSequence += 1
  }
  if (payload.voice_audio_final) {
    rememberPlayedVoiceAudioKey(audioKey)
    streamingVoiceAudioKey = ''
    streamingVoiceSequence = 0
  }
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
  if (realtimeVoiceActive.value) stopRealtimeVoice()
  if (voiceRecorder.active || voiceStopTimer !== null) void cleanupVoiceStream()
  voiceState.value = 'idle'
  voiceEpoch = nextVoiceEpoch(voiceEpoch)
  stopSpokenAudio()
  if (connected.value) {
    void client.cancelVoiceInput(activeContext.value.thread_id || '').catch(() => undefined)
  }
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
  const realtime = ready.value?.realtime_voice
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
      name: 'realtime_voice',
      label: 'Realtime Voice (Debug)',
      state: realtime?.configured ? 'ready' : realtime?.enabled ? 'error' : 'off',
      enabled: Boolean(realtime?.enabled),
      configured: Boolean(realtime?.configured),
      provider: realtime?.configured ? realtime?.provider || 'qwen_audio' : 'none',
      model: realtime?.configured ? realtime?.model : '',
      summary: realtime?.configured ? '实时对话与 Minecraft 协作' : realtime?.enabled ? '未配置' : '未启用',
      timeout_seconds: realtime?.timeout_seconds || 15,
      last_error: realtime?.error || '',
      notes: ['text-only cloud response', 'local GPT-SoVITS output', 'scoped Minecraft tools'],
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

const displayedAgentClis = computed(() => (agentCliRows.value.length ? agentCliRows.value : fallbackAgentClis()))

const selectedAgentCli = computed(() => {
  return displayedAgentClis.value.find((row) => row.id === selectedAgentCliId.value) || displayedAgentClis.value[0]
})

const selectedAgentCliModels = computed<AgentCliModelOption[]>(() => {
  const options = selectedAgentCli.value?.model_options || []
  if (options.length) return options
  const models = selectedAgentCli.value?.models || []
  return (models.length ? models : ['默认']).map((model) => ({ id: model, label: model }))
})

const selectedAgentCliModelSource = computed(() => {
  if (selectedAgentCli.value?.models_source === 'cli_live') return '来自 CLI 的实时列表'
  if (selectedAgentCli.value?.models_source === 'fallback') return 'CLI 列表暂不可用'
  return '内置候选列表'
})

const selectedAgentCliModelHint = computed(() => {
  if (selectedAgentCli.value?.models_source === 'cli_live') return '已从当前安装的 CLI 实时读取模型。“默认”使用 CLI 自身配置。'
  if (selectedAgentCli.value?.models_source === 'fallback') return '暂时无法读取 CLI 模型目录，当前仅使用 CLI 默认配置。点击“测试”可重新读取。'
  return '该 CLI 暂未提供模型发现接口，显示内置候选项。'
})

const selectedAgentCliReasoningOptions = computed(() => {
  const rows = selectedAgentCli.value?.reasoning || []
  return rows.length ? rows : ['默认']
})

function fallbackAgentClis(): AgentCliProfile[] {
  return [
    { id: 'claude', name: 'Claude Code', vendor: 'Anthropic official CLI', installed: false, status: 'missing', models: ['默认'], reasoning: ['默认'], run_strategy: 'prompt_arg', supports_takeover: true },
    { id: 'codex', name: 'Codex CLI', vendor: 'OpenAI official CLI', installed: false, status: 'missing', models: ['默认'], model_options: [{ id: '默认', label: '默认（使用 CLI 配置）' }], models_source: 'fallback', models_error: 'cli_not_found', reasoning: ['默认', 'Low', 'Medium', 'High', 'XHigh'], run_strategy: 'codex_exec_json', supports_takeover: true },
    { id: 'hermes', name: 'Hermes', vendor: 'ACP agent CLI', installed: false, status: 'missing', models: ['默认'], reasoning: ['默认'] },
  ]
}

function agentCliIcon(row: AgentCliProfile) {
  const id = row.id || ''
  if (id === 'codex') return 'C'
  if (id === 'claude') return 'CC'
  if (id === 'gemini') return 'G'
  if (id === 'hermes') return 'H'
  return 'AI'
}

function agentCliMeta(row: AgentCliProfile) {
  const pieces = [row.vendor || 'Agent CLI']
  if (row.version) pieces.push(row.version)
  else pieces.push(row.installed ? '已安装' : '未安装')
  return pieces.join(' · ')
}

function agentCliStatus(row: AgentCliProfile) {
  if (agentCliTestStatus.value[row.id]) return agentCliTestStatus.value[row.id]
  if (row.installed && row.probe_ok) return '测试通过'
  if (row.installed) return '已安装'
  return '未安装'
}

function agentCliTestDisabled(row: AgentCliProfile) {
  return !connected.value || agentCliLoading.value || agentCliCoreUnsupported.value || !row.installed
}

function selectAgentCli(row: AgentCliProfile) {
  selectedAgentCliId.value = row.id
  const models = (row.model_options || []).map((option) => option.id)
  const availableModels = models.length ? models : row.models || []
  selectedAgentCliModel.value = availableModels.includes(selectedAgentCliModel.value) ? selectedAgentCliModel.value : availableModels[0] || '默认'
  const reasoning = row.reasoning || []
  selectedAgentCliReasoning.value = reasoning.includes(selectedAgentCliReasoning.value) ? selectedAgentCliReasoning.value : reasoning[0] || '默认'
}

function syncAgentCliFromReady(payload: CoreReadyPayload) {
  const state = payload.agent_cli
  if (!state) return
  agentCliRuntime.value = state
  executionMode.value = state.enabled ? 'local_cli' : 'byok'
  if (state.selected) selectedAgentCliId.value = state.selected
  if (state.model) selectedAgentCliModel.value = state.model
  if (state.reasoning) selectedAgentCliReasoning.value = state.reasoning
}

function queueAgentCliSync() {
  if (agentCliSyncTimer !== null) window.clearTimeout(agentCliSyncTimer)
  agentCliSyncTimer = window.setTimeout(() => {
    agentCliSyncTimer = null
    void syncAgentCliTakeover()
  }, 120)
}

async function syncAgentCliTakeover() {
  if (!connected.value || agentCliSyncing.value) return
  if (agentCliCoreUnsupported.value) return
  agentCliSyncing.value = true
  try {
    const result = (await client.agentCliConfigure({
      enabled: executionMode.value === 'local_cli',
      mode: executionMode.value,
      selected: selectedAgentCliId.value,
      model: selectedAgentCliModel.value,
      reasoning: selectedAgentCliReasoning.value,
    })) as { ok?: boolean; agent_cli?: AgentCliRuntimeStatus; codex_runtime?: CodexRuntimeStatus; error?: string }
    agentCliCoreUnsupported.value = false
    if (result.agent_cli) agentCliRuntime.value = result.agent_cli
    if (result.codex_runtime) codexRuntime.value = result.codex_runtime
    if (result.ok === false) errorText.value = result.error || '运行模式设置没有保存'
  } catch (error) {
    if (isAgentCliRpcUnsupported(error)) {
      agentCliCoreUnsupported.value = true
      agentCliRuntime.value = { enabled: false, mode: 'byok', selected: selectedAgentCliId.value }
      errorText.value = '当前 Joi 运行时接口还没有加载，请重启桌面应用。'
    } else {
      errorText.value = error instanceof Error ? error.message : '运行模式设置失败'
    }
  } finally {
    agentCliSyncing.value = false
  }
}

function agentCliTakeoverText(row?: AgentCliProfile) {
  if (!row) return '未选择运行时'
  if (agentCliCoreUnsupported.value) return 'Joi 需要重启后才能接管'
  if (!row.supports_takeover) return '实验运行时，能力受限'
  if (executionMode.value !== 'local_cli') return 'BYOK 模式中'
  if (row.id !== 'codex') return '实验运行时'
  if (joiMcpStatus.value?.connected) return 'Joi 能力已连接'
  return codexRuntime.value?.enabled ? 'Joi 运行时已就绪' : '可作为 Joi 运行时'
}

async function refreshAgentClis() {
  agentCliLoading.value = true
  try {
    const result = (await client.agentCliList()) as AgentCliListResult
    agentCliCoreUnsupported.value = false
    if (Array.isArray(result.clis)) {
      agentCliRows.value = result.clis
      const selected = result.selected || selectedAgentCliId.value
      const row = result.clis.find((item) => item.id === selected) || result.clis.find((item) => item.installed)
      if (row) selectAgentCli(row)
    }
    if (!result.ok) errorText.value = result.error || 'Agent CLI 扫描失败'
  } catch (error) {
    if (isAgentCliRpcUnsupported(error)) {
      agentCliCoreUnsupported.value = true
      agentCliRows.value = fallbackAgentClis()
      errorText.value = '当前 Joi 运行时接口还没有加载，请重启桌面应用。'
    } else {
      errorText.value = error instanceof Error ? error.message : '运行时扫描失败'
    }
  } finally {
    agentCliLoading.value = false
  }
}

async function testAgentCli(row: AgentCliProfile) {
  if (!row.id) return
  agentCliLoading.value = true
  agentCliTestStatus.value = { ...agentCliTestStatus.value, [row.id]: '测试中' }
  try {
    const result = (await client.agentCliTest(row.id)) as AgentCliTestResult
    agentCliCoreUnsupported.value = false
    if (result.cli) {
      agentCliRows.value = displayedAgentClis.value.map((item) => (item.id === row.id ? { ...item, ...result.cli } : item))
      if (selectedAgentCliId.value === row.id) selectAgentCli({ ...row, ...result.cli })
    }
    agentCliTestStatus.value = {
      ...agentCliTestStatus.value,
      [row.id]: result.summary || (result.ok ? '测试通过' : '测试失败'),
    }
    if (!result.ok) errorText.value = result.summary || result.error || 'Agent CLI 测试失败'
  } catch (error) {
    agentCliTestStatus.value = { ...agentCliTestStatus.value, [row.id]: '测试失败' }
    if (isAgentCliRpcUnsupported(error)) {
      agentCliCoreUnsupported.value = true
      errorText.value = '当前 Joi 运行时接口还没有加载，请重启桌面应用。'
    } else {
      errorText.value = error instanceof Error ? error.message : 'Agent CLI 测试失败'
    }
  } finally {
    agentCliLoading.value = false
  }
}

function isAgentCliRpcUnsupported(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '')
  return message.includes('unknown method: agent_cli.')
}

async function refreshJoiMcpStatus() {
  if (!connected.value) return
  try {
    const result = (await client.joiMcpStatus()) as { ok?: boolean; joi_mcp?: JoiMcpStatus; error?: string }
    if (result.joi_mcp) joiMcpStatus.value = result.joi_mcp
    if (!result.ok) errorText.value = result.error || 'Joi 能力连接状态读取失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Joi 能力连接状态读取失败'
  }
}

async function installJoiMcp() {
  if (!connected.value || joiMcpInstalling.value) return
  joiMcpInstalling.value = true
  try {
    const result = (await client.joiMcpInstallCodex()) as { ok?: boolean; joi_mcp?: JoiMcpStatus; error?: string }
    if (result.joi_mcp) joiMcpStatus.value = result.joi_mcp
    if (!result.ok) errorText.value = result.error || 'Joi 能力连接失败'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Joi 能力连接失败'
  } finally {
    joiMcpInstalling.value = false
  }
}

function nativeSkills(): NativeSkill[] {
  return skillManifest.value?.skills || ready.value?.skills?.skills || []
}

function skillManifestVersion() {
  return skillManifest.value?.version || ready.value?.skills?.version || 'joi.skill_manifest.v1'
}

function skillCapabilityLabel(value?: string) {
  const labels: Record<string, string> = {
    ready: '可用',
    off: '关闭',
    unavailable: '不可用',
    degraded: '降级',
  }
  return labels[value || ''] || '未知'
}

function skillPermissionLabel(value?: string) {
  const labels: Record<string, string> = {
    low: '低风险',
    medium: '需确认',
    high: '高风险',
  }
  return labels[value || ''] || '需确认'
}

function skillMeta(skill: NativeSkill) {
  const meta: string[] = []
  if (skill.category) meta.push(skill.category)
  if (skill.permission_level) meta.push(skillPermissionLabel(skill.permission_level))
  if (skill.state_policy) meta.push(skill.state_policy)
  if (skill.audit) meta.push(skill.audit)
  if (skill.supports_dry_run) meta.push('dry-run')
  return meta
}

function skillTools(skill: NativeSkill) {
  return [...(skill.tools || []), ...(skill.rpc_methods || [])].slice(0, 10)
}

function skillEnabled(skill: NativeSkill) {
  return skill.enabled !== false
}

function skillToggleDisabled(skill: NativeSkill) {
  return !connected.value || skillRefreshLoading.value || skill.id === 'joi.runtime_config'
}

function skillActionLabel(skill: NativeSkill) {
  if (skill.id === 'joi.runtime_config') return '核心'
  return skillEnabled(skill) ? '关闭' : '开启'
}

async function setSkillEnabled(skill: NativeSkill, enabled: boolean) {
  if (!skill.id) return
  skillRefreshLoading.value = true
  try {
    const result = (await client.applyRuntimeConfig({ skills: { [skill.id]: { enabled } } })) as {
      ok?: boolean
      preview?: RuntimeConfigMutationResult
      ready?: CoreReadyPayload
    }
    if (result.preview) runtimePreview.value = result.preview
    if (result.ok === false) errorText.value = result.preview?.summary || '技能开关没有提交'
    if (result.ready) {
      ready.value = result.ready
      skillManifest.value = result.ready.skills || skillManifest.value
      syncRuntimeDraft(result.ready)
    }
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '技能开关提交失败'
  } finally {
    skillRefreshLoading.value = false
  }
}

async function refreshSkills() {
  skillRefreshLoading.value = true
  try {
    const [result, catalog, adapters, drafts] = await Promise.all([
      client.skillsList() as Promise<{ ok?: boolean; skills?: NativeSkillManifest }>,
      client.agentSkillCatalog(activeContext.value.project_id || '', ready.value?.character?.id || '', true) as Promise<{ skills?: AgentSkillInstallation[] }>,
      client.gameAdapterList() as Promise<{ adapters?: GameAdapterManifest[] }>,
      client.agentSkillDraftList(activeContext.value.project_id || '') as Promise<{ drafts?: AgentSkillDraft[] }>,
    ])
    if (result.skills) skillManifest.value = result.skills
    installedAgentSkills.value = catalog.skills || []
    gameAdapterRows.value = adapters.adapters || []
    await refreshMinecraftConnection()
    agentSkillDrafts.value = (drafts.drafts || []).filter((draft) => draft.status === 'draft')
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '技能清单刷新失败'
  } finally {
    skillRefreshLoading.value = false
  }
}












function requestAppConfirm(options: AppConfirmOptions): Promise<boolean> {
  if (appConfirmRequest.value) resolveAppConfirm(false)
  return new Promise((resolve) => {
    appConfirmRequest.value = {
      title: options.title,
      message: options.message,
      confirmLabel: options.confirmLabel || '确认',
      cancelLabel: options.cancelLabel || '取消',
      danger: Boolean(options.danger),
      resolve,
    }
    void nextTick(() => {
      const dialog = appConfirmDialog.value
      if (dialog && !dialog.open) dialog.showModal()
    })
  })
}

function resolveAppConfirm(accepted: boolean) {
  const request = appConfirmRequest.value
  if (!request) return
  appConfirmRequest.value = null
  const dialog = appConfirmDialog.value
  if (dialog?.open) dialog.close()
  request.resolve(accepted)
}

async function installGameAdapter(adapter: GameAdapterManifest) {
  const review = `${adapter.name} ${adapter.version}\n来源：${adapter.source}\n许可：${adapter.license}\n动作：${adapter.action_sets.join('、')}\n\n安装代码适配器？首次实际运行仍会经过能力会话权限。`
  if (!await requestAppConfirm({ title: `安装 ${adapter.name} 适配器`, message: review, confirmLabel: '确认安装' })) return
  const result = await client.gameAdapterInstall(adapter.id, true) as { ok?: boolean; error?: string; adapter?: GameAdapterManifest }
  gameAdapterNotice.value = result.ok ? `${adapter.name} 适配器已安装。` : result.error || '适配器安装失败'
  await refreshSkills()
}

async function uninstallGameAdapter(adapter: GameAdapterManifest) {
  if (!await requestAppConfirm({
    title: `卸载 ${adapter.name} 适配器`,
    message: `卸载 ${adapter.name} 适配器？不会删除游戏存档或聊天记录。`,
    confirmLabel: '确认卸载',
    danger: true,
  })) return
  const result = await client.gameAdapterUninstall(adapter.id, true) as { ok?: boolean; error?: string }
  gameAdapterNotice.value = result.ok ? `${adapter.name} 适配器已卸载。` : result.error || '适配器卸载失败'
  await refreshSkills()
}

async function toggleGameAdapter(adapter: GameAdapterManifest) {
  await client.gameAdapterEnable(adapter.id, !adapter.enabled)
  await refreshSkills()
}

async function inspectGameAdapterRun(adapter: GameAdapterManifest) {
  const mode = adapter.modes.includes('companion') ? 'companion' : adapter.modes[0]
  const result = await client.gameAdapterRun({ adapter_id: adapter.id, mode, goal: '连接检查', dry_run: true }) as { ready?: boolean; error?: string; detection_status?: { setup_hint?: string } }
  gameAdapterNotice.value = result.ready ? `${adapter.name} 已准备好。` : result.detection_status?.setup_hint || result.error || `${adapter.name} 还需要配置。`
}

function minecraftList(value: string) {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

const LANGUAGE_LABELS: Record<string, string> = {
  follow: '跟随我的输入',
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
  yue: '粤语',
}

function languageLabel(code: string) {
  const normalized = String(code || '').trim().toLowerCase().split(/[-_]/)[0]
  return LANGUAGE_LABELS[normalized] || (normalized ? normalized : '未设置')
}

const chatLanguageChoices = computed(() => languageSettings.value?.chat_choices?.length
  ? languageSettings.value.chat_choices
  : ['follow', 'zh', 'en', 'ja', 'ko'])

const currentChatLanguage = computed(() => String(languageSettings.value?.chat || 'zh'))

const voiceLanguageLabel = computed(() => languageLabel(String(languageSettings.value?.voice || '')))

/**
 * The chat language is Core's, not the shell's: it changes what the model is
 * told to write, so it is stored with the rest of the runtime configuration.
 */
async function saveChatLanguage(code: string) {
  if (languageBusy.value || code === currentChatLanguage.value) return
  languageBusy.value = true
  languageNotice.value = ''
  try {
    const result = (await client.applyRuntimeConfig({ language: { chat: code } })) as {
      ok?: boolean
      preview?: RuntimeConfigMutationResult
    }
    if (result.ok === false) {
      languageNotice.value = result.preview?.summary || '聊天语言没有保存。'
      return
    }
    languageSettings.value = { ...(languageSettings.value || {}), chat: code }
    languageNotice.value = `Joi 的回复语言已设为${languageLabel(code)}。`
  } catch (error) {
    languageNotice.value = error instanceof Error ? error.message : '聊天语言保存失败。'
  } finally {
    languageBusy.value = false
  }
}

function minecraftErrorLabel(code: string) {
  return {
    minecraft_version_unsupported: '这个 Minecraft 版本超出了 Joi 桥接支持的范围；请把世界换成受支持的版本，或更新桥接。',
    minecraft_connect_refused: '游戏拒绝了连接：请确认世界已「对局域网开放」，并核对端口。',
    minecraft_host_unreachable: '连不上这个地址：请确认地址填写正确，且游戏与 Joi 在同一台机器或同一网络。',
    minecraft_login_rejected: '游戏拒绝了 Joi 的登录：请核对登录方式（局域网世界用「离线 / LAN」）。',
    spawn_timeout: 'Joi 连上了但没能进入世界；请重新打开局域网后再试。',
    minecraft_connect_failed: 'Joi 无法连接 Minecraft；具体原因见 Joi Core 日志。',
    minecraft_connection_scope_unconfigured: '请先保存连接配置：服务器标签与世界标签不能为空。',
    minecraft_connection_scope_mismatch: '范围里的服务器/世界标签与已保存的连接配置不一致。',
    minecraft_bridge_not_found: '找不到已构建的 Minecraft 桥接，请先构建 minecraft-bridge。',
    minecraft_bridge_unreachable: '无法启动 Minecraft 桥接进程。',
    adapter_not_enabled: 'Minecraft 适配器还没有启用。',
  }[code] || code
}

async function refreshMinecraftConnection() {
  try {
    const result = await client.minecraftConnectionStatus() as { connection?: Record<string, unknown> } & Record<string, unknown>
    const connection = (result.connection || result) as Record<string, unknown>
    if (connection.source !== 'private_core') return
    minecraftConnectionDraft.value = {
      host: stringValue(connection.host) || '127.0.0.1',
      port: Math.max(0, Number(connection.port || 0)),
      username: stringValue(connection.username) || 'Joi',
      auth: connection.auth === 'microsoft' ? 'microsoft' : 'offline',
      server_id: stringValue(connection.server_id) || 'local-hmcl',
      world: stringValue(connection.world) || 'world',
      version: stringValue(connection.version),
    }
  } catch {
    return
  }
}

async function saveMinecraftConnection() {
  minecraftBusy.value = true
  try {
    const result = await client.minecraftConnectionConfigure({ ...minecraftConnectionDraft.value }) as { ok?: boolean; error?: string }
    gameAdapterNotice.value = result.ok ? 'Minecraft 连接配置已安全保存在 Joi Core 私有目录。' : result.error || 'Minecraft 连接配置无效。'
    if (result.ok) await refreshSkills()
  } catch (error) {
    gameAdapterNotice.value = error instanceof Error ? error.message : 'Minecraft 连接配置失败。'
  } finally {
    minecraftBusy.value = false
  }
}

function minecraftScopePayload() {
  return {
    server_id: minecraftConnectionDraft.value.server_id.trim().toLowerCase(),
    world: minecraftConnectionDraft.value.world.trim().toLowerCase(),
    dimensions: [...minecraftScopeDraft.value.dimensions],
    max_radius: Math.round(Number(minecraftScopeDraft.value.max_radius)),
    max_actions: Math.round(Number(minecraftScopeDraft.value.max_actions)),
    max_blocks_changed: Math.round(Number(minecraftScopeDraft.value.max_blocks_changed)),
    allowed_blocks: minecraftList(minecraftScopeDraft.value.allowed_blocks),
    allowed_players: minecraftList(minecraftScopeDraft.value.allowed_players),
    allow_build: Boolean(minecraftScopeDraft.value.allow_build),
    allow_containers: Boolean(minecraftScopeDraft.value.allow_containers),
  }
}

async function startMinecraftSession() {
  if (activeMinecraftSessionId.value) return
  minecraftBusy.value = true
  try {
    const scope = minecraftScopePayload()
    const budget = {
      max_steps: scope.max_actions,
      max_seconds: minecraftMode.value === 'delegate' ? 1800 : 900,
      max_failures: 3,
    }
    const request = {
      mode: minecraftMode.value,
      goal_summary: minecraftMode.value === 'delegate' ? '让 Joi 在已确认范围内单独游玩' : '与 Joi 一起游玩 Minecraft',
      scope,
      budget,
    }
    const preview = await client.minecraftSessionStart(request) as { ok?: boolean; requires_approval?: boolean; approval_id?: string; error?: string }
    if (!preview.requires_approval || !preview.approval_id) {
      gameAdapterNotice.value = minecraftErrorLabel(preview.error || '') || 'Minecraft 范围预览失败。'
      return
    }
    const dimensions = scope.dimensions.join('、')
    const confirmed = await requestAppConfirm({
      title: '确认 Minecraft 能力范围',
      message: `模式：${minecraftMode.value === 'delegate' ? 'Joi 单独玩' : '与 Joi 一起玩'}\n服务器标签：${scope.server_id}\n世界：${scope.world}\n维度：${dimensions}\n起点半径：${scope.max_radius}\n最多动作：${scope.max_actions}\n最多修改方块：${scope.max_blocks_changed}\n允许方块：${scope.allowed_blocks.join('、')}\n允许玩家：${scope.allowed_players.join('、') || '无'}\n建造：${scope.allow_build ? '允许' : '禁止'}\n容器：${scope.allow_containers ? '允许' : '禁止'}\n\n确认后 Joi 才会连接游戏；每条动作仍会单独校验和留回执。自主行为（如主动观察/评论）与你的指令共用上述动作与方块额度，且攻击只在收到你明确指令后才会执行。`,
      confirmLabel: '确认并连接',
    })
    if (!confirmed) {
      gameAdapterNotice.value = '已取消；没有连接 Minecraft。'
      return
    }
    const result = await client.minecraftSessionStart({
      ...request,
      confirmed_scope: true,
      approval_id: preview.approval_id,
    }) as { ok?: boolean; error?: string; session?: CapabilitySession; state?: string }
    if (result.session?.id) activeCapabilitySession.value = result.session
    if (result.ok) void refreshMinecraftAutonomy()
    gameAdapterNotice.value = result.ok
      ? 'Minecraft 已连接。现在可启动“实时语音 + Minecraft”。'
      : minecraftErrorLabel(result.error || '') || 'Minecraft 连接失败。'
  } catch (error) {
    gameAdapterNotice.value = error instanceof Error ? error.message : 'Minecraft 会话启动失败。'
  } finally {
    minecraftBusy.value = false
  }
}

/**
 * Joi's own initiative, off until asked for.
 *
 * It spends the same action and block budget the user approved for the
 * session, which is why this is a visible switch rather than a default.
 */
async function toggleMinecraftAutonomy() {
  if (minecraftBusy.value) return
  const enabled = !minecraftAutonomyEnabled.value
  minecraftBusy.value = true
  try {
    const result = await client.minecraftAutonomyConfigure({ enabled }) as { ok?: boolean; enabled?: boolean; error?: string }
    if (result.ok === false) {
      gameAdapterNotice.value = minecraftErrorLabel(result.error || '') || '自主行为切换失败。'
      return
    }
    minecraftAutonomyEnabled.value = Boolean(result.enabled)
    gameAdapterNotice.value = minecraftAutonomyEnabled.value
      ? 'Joi 会在空闲时主动观察和搭话，用的是你已批准的动作额度。攻击仍然只在你明确要求时才会执行。'
      : 'Joi 已停止主动行为，只在你开口时回应。'
  } catch (error) {
    gameAdapterNotice.value = error instanceof Error ? error.message : '自主行为切换失败。'
  } finally {
    minecraftBusy.value = false
  }
}

async function refreshMinecraftAutonomy() {
  try {
    const result = await client.minecraftAutonomyStatus() as { ok?: boolean; enabled?: boolean }
    if (result.ok) minecraftAutonomyEnabled.value = Boolean(result.enabled)
  } catch {
    return
  }
}

async function stopMinecraftSession() {
  const sessionId = activeMinecraftSessionId.value
  if (!sessionId) return
  if (realtimeVoiceActive.value) stopRealtimeVoice()
  minecraftBusy.value = true
  try {
    const result = await client.minecraftSessionStop(sessionId) as { ok?: boolean; error?: string; session?: CapabilitySession }
    if (result.session) activeCapabilitySession.value = result.session
    gameAdapterNotice.value = result.ok ? 'Minecraft 会话已结束，Bridge 已关闭。' : result.error || 'Minecraft 会话停止失败。'
  } finally {
    minecraftBusy.value = false
  }
}












async function detectLocalModels() {
  if (!connected.value || byokDraft.value.provider !== 'ollama' || byokDiscovering.value) return
  byokDiscovering.value = true
  byokNotice.value = ''
  try {
    const result = await client.byokModels({
      provider: 'ollama',
      base_url: byokDraft.value.base_url.trim(),
    }) as ByokTestResult
    byokResult.value = result
    const models = result.models || []
    if (result.ok && models.length) {
      if (!byokDraft.value.model.trim()) byokDraft.value.model = models[0]
      byokDirty.value = true
      byokNotice.value = `发现 ${models.length} 个本地模型，已为你选择 ${byokDraft.value.model}。`
    } else {
      byokNotice.value = byokErrorLabel(result.error || '')
    }
  } catch (error) {
    byokNotice.value = error instanceof Error ? error.message : '无法检测本地模型'
  } finally {
    byokDiscovering.value = false
  }
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
    tts_gpt_sovits_streaming_mode: 2,
    tts_provider: '',
    tts_model: '',
    tts_voice: '',
    tts_base_url: '',
    tts_timeout_seconds: 120,
    tts_optimize_text: false,
    ocr_timeout_seconds: 5,
    llm_temperature: 0.7,
    llm_provider: '',
    llm_model: '',
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
  draft.tts_gpt_sovits_streaming_mode = Math.max(1, Math.min(3, Number(settings.tts?.gpt_sovits_streaming_mode ?? draft.tts_gpt_sovits_streaming_mode)))
  // Read from the settings block rather than the status one: status reports
  // whether the voice works, settings report how it is configured, and only
  // the latter carries the endpoint and model.
  const ttsConfig = settings.tts || {}
  draft.tts_provider = String(ttsConfig.provider ?? tts.provider ?? draft.tts_provider)
  draft.tts_model = String(ttsConfig.model ?? draft.tts_model)
  draft.tts_voice = String(ttsConfig.voice ?? draft.tts_voice)
  draft.tts_base_url = String(ttsConfig.base_url ?? draft.tts_base_url)
  draft.tts_timeout_seconds = Math.max(1, Number(ttsConfig.timeout_seconds ?? draft.tts_timeout_seconds))
  draft.tts_optimize_text = Boolean(ttsConfig.optimize_text)
  const ocr = settings.ocr || runtimeProvider(payload, 'ocr')
  draft.ocr_timeout_seconds = Math.max(1, Number(ocr?.timeout_seconds || draft.ocr_timeout_seconds))
  const computerUse = settings.computer_use || {}
  const settleMs = computerUse.post_action_settle_ms ?? (parseSettleMs(runtimeProvider(payload, 'computer_use')?.limit) || draft.computer_post_action_settle_ms)
  draft.computer_post_action_settle_ms = Math.max(0, Number(settleMs))
  const llmSettings = settings.llm || {}
  const fastModel = runtimeProvider(payload, 'fast')
  draft.llm_temperature = Math.max(0, Number(llmSettings.temperature ?? draft.llm_temperature))
  draft.llm_provider = String(llmSettings.provider ?? draft.llm_provider)
  draft.llm_model = String(llmSettings.model ?? draft.llm_model)
  draft.llm_use_mock = typeof llmSettings.use_mock === 'boolean' ? llmSettings.use_mock : fastModel?.state === 'mock'
  runtimeDraft.value = draft
}

function runtimeProvider(payload: CoreReadyPayload, name: string) {
  return (payload.runtime?.providers || []).find((row) => row.name === name)
}

function parseSettleMs(value?: string) {
  const match = String(value || '').match(/settle\s+(\d+)ms/i)
  return match ? Number(match[1]) : 0
}

/** Drop the blank entries, so an untouched box changes nothing. */
function onlyFilled(values: Record<string, string>) {
  const filled: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) {
    const text = String(value ?? '').trim()
    if (text) filled[key] = text
  }
  return filled
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
      gpt_sovits_streaming_mode: safeInteger(draft.tts_gpt_sovits_streaming_mode, 2),
      timeout_seconds: safeInteger(draft.tts_timeout_seconds, 1),
      optimize_text: Boolean(draft.tts_optimize_text),
      // Text fields are sent only when filled. An empty box means "leave it
      // as it is" -- sending "" would erase a working endpoint or model for
      // anyone who opened the panel without touching them.
      ...onlyFilled({
        provider: draft.tts_provider,
        model: draft.tts_model,
        voice: draft.tts_voice,
        base_url: draft.tts_base_url,
      }),
    },
    ocr: {
      timeout_seconds: safeInteger(draft.ocr_timeout_seconds, 1),
    },
    llm: {
      temperature: safeNumber(draft.llm_temperature, 0.7),
      use_mock: Boolean(draft.llm_use_mock),
      ...onlyFilled({ provider: draft.llm_provider, model: draft.llm_model }),
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
    asr_auth_failed: '识别密钥无效',
    asr_insufficient_balance: '识别账户余额不足',
    asr_rate_limited: '识别接口限流',
    audio_format_unsupported: '录音格式不支持',
    tts_timeout: '合成超时',
    tts_service_unavailable: '服务未连接',
    tts_not_installed: '未安装 GPT-SoVITS',
    tts_service_unhealthy: '本地语音服务异常，请重启 GPT-SoVITS',
    tts_auth_failed: '语音密钥无效',
    tts_rate_limited: '语音接口限流',
    audio_blocked: '系统阻止了自动播放，点一下窗口再试',
    audio_play_failed: '音频播放失败',
    tts_config_error: 'TTS 配置有误',
    tts_failed: '合成失败',
    pillow_missing: '缺少 Pillow',
    pytesseract_missing: '缺少 pytesseract',
    tesseract_missing: '缺少 Tesseract',
    tesseract_unavailable: 'Tesseract 不可用',
    ocr_dependency_missing: 'OCR 依赖缺失',
    computer_use_windows_only: '仅 Windows 可执行',
    computer_use_desktop_only: '仅 Windows/macOS 可执行',
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
    tts_not_installed: '未安装 GPT-SoVITS',
    tts_service_unhealthy: '本地语音服务异常，请重启 GPT-SoVITS',
    tts_auth_failed: '语音密钥无效',
    tts_rate_limited: '语音接口限流',
    audio_blocked: '系统阻止了自动播放，点一下窗口再试',
    audio_play_failed: '音频播放失败',
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
  if (currentAudio) {
    currentAudio.pause()
    currentAudio.currentTime = 0
    currentAudio = null
  }
  streamingVoiceAudioKey = ''
  streamingVoiceSequence = 0
  // Barge-in must close the mouth too, or the character keeps mouthing a line
  // nobody can hear any more.
  detachLipSync()
}

function realtimeVoiceErrorLabel(error: string) {
  const labels: Record<string, string> = {
    realtime_unconfigured: '实时语音未配置，请先在 realtime_voice 中启用。',
    realtime_config_error: '实时语音配置有误。',
    realtime_auth_failed: '实时语音密钥无效或无权限。',
    realtime_rate_limited: '实时语音当前被限流，请稍后再试。',
    realtime_timeout: '实时语音连接超时，请稍后再试。',
    realtime_invalid_request: '实时语音会话参数未被服务接受。',
    realtime_provider_error: '云端实时语音服务返回错误，会话已停止，麦克风已释放。',
    microphone_unavailable: '麦克风不可用，请检查系统权限。',
    audio_capture_unavailable: '当前环境无法采集实时 PCM 音频。',
    realtime_disconnected: '实时语音连接已断开。',
    realtime_audio_overflow: '实时音频发送来不及处理，会话已安全停止。',
    minecraft_session_not_runnable: 'Minecraft 会话尚未就绪，请先连接游戏并确认范围。',
    realtime_local_tts_unavailable: '本地 GPT-SoVITS 未就绪；字幕可用，Joi 暂时静音。',
    realtime_local_tts_failed: '本地 GPT-SoVITS 合成失败；字幕仍可用。',
    realtime_unavailable: '实时语音暂时不可用。',
  }
  return labels[error] || labels.realtime_unavailable
}

/**
 * A realtime turn is a conversation, so it belongs in the conversation.
 *
 * These events are built here and never persisted: a realtime session is
 * ephemeral by design, and Core stays the only author of stored history. What
 * this fixes is a session that looked like nothing was happening -- the speech
 * Joi heard and the line she answered with only ever reached a status line.
 */
function showRealtimeTurn(epoch: number, role: 'user' | 'assistant', text: string) {
  const summary = text.trim()
  if (!summary) return
  const taskId = `realtime-turn-${Math.max(0, Math.floor(Number(epoch) || 0))}`
  // A turn renders around what the user said. If transcription produced
  // nothing, say so rather than dropping Joi's answer out of the conversation.
  if (role === 'assistant' && !events.value.some((row) => row.task_id === taskId && row.type === 'user_message')) {
    showRealtimeTurn(epoch, 'user', lastTranscript.value.trim() || '（未识别到语音）')
  }
  mergeConversationEvents([
    {
      event_id: `${taskId}-${role}`,
      // '对话' is what marks an assistant event as speech rather than work, so
      // a realtime answer renders as a reply and never as a task card.
      type: role === 'user' ? 'user_message' : 'tool_completed',
      task_id: taskId,
      created_at: Date.now() / 1000,
      display_card: { title: role === 'user' ? '语音' : '对话', summary },
      voice_line: { text: '' },
    } as AgentEvent,
  ])
}

function handleRealtimeVoiceEvent(event: RealtimeVoiceEvent) {
  if (event.kind === 'barge_in') {
    realtimePlaybackEpoch = event.epoch
    realtimeAssistantTranscript.value = ''
    stopSpokenAudio()
    // Speaking up interrupts a motion here for the same reason a typed message
    // does: whatever Joi was performing belongs to the turn the user just ended.
    if (realtimeCharacterMotion.value) {
      realtimeCharacterMotion.value = {
        motion: 'idle',
        eventKey: `realtime-motion-stop:${event.epoch}`,
        durationMs: 0,
        loop: true,
        intensity: 0.25,
      }
    }
  }
  if (event.kind === 'user_transcript' && event.final) {
    lastTranscript.value = event.text
    showRealtimeTurn(Number(event.epoch || 0), 'user', event.text)
  }
  if (event.kind === 'assistant_transcript') {
    realtimePlaybackEpoch = Math.max(realtimePlaybackEpoch, Number(event.epoch || 0))
    realtimeAssistantTranscript.value = event.final
      ? event.text
      : `${realtimeAssistantTranscript.value}${event.text}`.slice(-8000)
    if (event.final) showRealtimeTurn(Number(event.epoch || 0), 'assistant', realtimeAssistantTranscript.value)
  }
  if (event.kind === 'game_action') {
    realtimeAssistantTranscript.value = event.status === 'acting'
      ? `Joi 正在执行 ${event.action || 'Minecraft 操作'}…`
      : `Minecraft 操作：${event.status}`
  }
  if (event.kind === 'character_motion') {
    realtimeCharacterMotion.value = {
      motion: event.motion,
      eventKey: `realtime-motion:${event.epoch}:${event.motion}`,
      durationMs: event.durationMs,
      loop: event.loop,
      intensity: event.intensity,
    }
  }
  if (event.kind === 'skill_action') realtimeAssistantTranscript.value = realtimeSkillLabel(event)
  // Timings only, and only where the developer asked to see them: this is the
  // one place the realtime path can be told apart from a slow provider.
  if (event.kind === 'latency') lastRealtimeLatency.value = realtimeLatencyLabel(event)
  if (event.kind === 'tts_state') errorText.value = realtimeVoiceErrorLabel(event.error)
  if (event.kind === 'error') {
    errorText.value = realtimeVoiceErrorLabel(event.error)
    scheduleRealtimeReconnect(event.error)
  }
  if (event.kind === 'state' && (event.state === 'error' || event.state === 'recovery_required')) {
    scheduleRealtimeReconnect('realtime_disconnected')
  }
}

/** Transport faults worth one silent retry; a refusal or a bad key is not. */
const REALTIME_RETRYABLE = new Set([
  'realtime_disconnected',
  'realtime_provider_error',
  'realtime_audio_overflow',
  'realtime_timeout',
  'realtime_unavailable',
])

function scheduleRealtimeReconnect(error: string) {
  if (realtimeReconnectUsed || !REALTIME_RETRYABLE.has(error) || !realtimeVoiceMode) return
  realtimeReconnectUsed = true
  if (realtimeReconnectTimer !== null) window.clearTimeout(realtimeReconnectTimer)
  realtimeReconnectTimer = window.setTimeout(() => {
    realtimeReconnectTimer = null
    // Only if the user has not since taken the microphone back themselves.
    if (!realtimeVoiceMode || realtimeVoiceActive.value || !connected.value) return
    errorText.value = '实时语音连接中断，正在自动重连…'
    void startRealtimeVoiceSession(realtimeVoiceMode, realtimeVoiceMinecraftSessionId)
  }, 1200)
}

const REALTIME_SKILL_NAMES: Record<string, string> = {
  character_motion: '角色动作',
  computer_use: '电脑操作',
  browser: '浏览器',
  screen: '看屏幕',
  code: '写代码',
  game: '游戏技能',
  local_skill: '本地技能',
}

const REALTIME_SKILL_REFUSALS: Record<string, string> = {
  realtime_skill_unavailable: '本地技能当前不可用。',
  realtime_skill_unsupported: '这个动作还没有准备好。',
  realtime_skill_not_understood: '没听清这次要做什么，请再说一遍。',
  realtime_skill_not_actionable: '这一轮听起来像聊天，没有启动技能；需要执行请说得更具体，或改用文字输入。',
  realtime_skill_disabled: '这个技能已在设置里停用。',
  realtime_action_ambiguous: '这一轮提出了不止一个动作，已全部放弃。',
  realtime_action_invalid: '这个提案没有通过本地检查。',
}

/**
 * What the status line says about a skill Joi's voice just proposed.
 *
 * "started" is deliberately not "done": Core has only accepted the request, and
 * anything that touches this machine still waits on the approval card.
 */
function realtimeSkillLabel(event: { skill: string; status: string; requiresConfirmation: boolean; error?: string }) {
  const name = REALTIME_SKILL_NAMES[event.skill] || REALTIME_SKILL_NAMES.local_skill
  if (event.status === 'started') {
    return event.requiresConfirmation ? `Joi 已交给${name}，请在下面确认后执行。` : `Joi 已交给${name}。`
  }
  return REALTIME_SKILL_REFUSALS[event.error || ''] || `${name}这次没有启动。`
}

/**
 * What consent must say about reading the game screen, on this machine.
 *
 * It used to promise the frame never leaves this computer. That holds only
 * without a vision model: with one configured, the frame itself is sent to it
 * for the scene summary, so the promise was false in the normal setup. Core
 * reports which of the two is running and this says that, rather than assuming.
 */
function screenEvidenceDisclosure() {
  const route = stringValue(ready.value?.realtime_voice?.screen_evidence) || 'off'
  if (route === 'vision_model') {
    return 'Joi 还可能读取当前游戏画面来理解你的意图：这台电脑配置了视觉模型，所以截图本身会发送给它做画面理解，之后立即从本机删除；除此之外原图不会留存。如果不希望画面上传，请在设置里关闭视觉模型，Joi 会只做本机文字识别。\n\n'
  }
  if (route === 'local_ocr') {
    return 'Joi 还可能读取当前游戏画面来理解你的意图：当前没有配置视觉模型，截图只在本机做文字识别，识别后立即删除，云端只收到一段文字摘要，原图不会上传也不会留存。\n\n'
  }
  return ''
}

async function toggleRealtimeVoice(useMinecraft = false) {
  if (realtimeVoiceActive.value) {
    stopRealtimeVoice()
    return
  }
  if (!realtimeVoiceConfigured.value) {
    errorText.value = realtimeVoiceErrorLabel('realtime_unconfigured')
    return
  }
  const mode: 'conversation' | 'minecraft' = useMinecraft ? 'minecraft' : 'conversation'
  const minecraftSessionId = mode === 'minecraft' ? activeMinecraftSessionId.value : ''
  if (mode === 'minecraft' && !minecraftSessionId) {
    errorText.value = realtimeVoiceErrorLabel('minecraft_session_not_runnable')
    return
  }
  if (!realtimeVoiceDisclosuresAccepted.has(mode)) {
    const localSkillDisclosure = '你也可以直接开口让 Joi 做动作或使用本机技能（打开应用、点击输入、上网搜索、看当前屏幕、写代码）。模型只能提出“这一轮是请求”，具体做什么由 Joi Core 用你自己说的原话重新规划；角色动作是纯本机动画，凡是会操作这台电脑的动作都仍然要你在界面上点确认。'
    const disclosure = mode === 'minecraft'
      ? `实时语音会把会话期间的麦克风音频发送给阿里云 Qwen Audio。模型可以提出一条 Minecraft 操作，但只能在你已确认的服务器、世界、维度、半径、方块和预算范围内执行；Joi Core 会逐条校验并保留回执。\n\n${localSkillDisclosure}\n\n${screenEvidenceDisclosure()}是否开始？`
      : `实时语音会把会话期间的麦克风音频发送给阿里云 Qwen Audio。云端只返回文本，Joi 仍使用本地 GPT-SoVITS 发声；本模式不执行 Minecraft 操作。\n\n${localSkillDisclosure}\n\n是否开始？`
    const accepted = await requestAppConfirm({
      title: mode === 'minecraft' ? '启动实时语音 + Minecraft' : '启动实时语音',
      message: disclosure,
      confirmLabel: '允许并开始',
    })
    if (!accepted) return
    realtimeVoiceDisclosuresAccepted.add(mode)
  }
  if (voiceRecorder.active) await cleanupVoiceStream()
  voiceState.value = 'idle'
  beginNewVoiceIntent()
  lastTranscript.value = ''
  realtimeAssistantTranscript.value = ''
  errorText.value = ''
  realtimeVoiceMode = mode
  realtimeVoiceMinecraftSessionId = minecraftSessionId
  realtimeReconnectUsed = false
  await startRealtimeVoiceSession(mode, minecraftSessionId)
}

/** Open one realtime session. Shared by the button and the automatic re-dial. */
async function startRealtimeVoiceSession(mode: 'conversation' | 'minecraft', minecraftSessionId: string) {
  realtimeVoiceSession = new RealtimeVoiceSession({
    mode,
    minecraftSessionId,
    startSession: async () => client.startRealtimeVoiceSession(
      mode,
      minecraftSessionId,
      Math.max(1000, Number(ready.value?.realtime_voice?.timeout_seconds || 15) * 1000 + 5000),
    ) as Promise<{ ok?: boolean; session_id?: string; state?: string; error?: string }>,
    appendAudio: (params) => {
      if (!client.appendRealtimeVoiceAudio(params)) throw new Error('realtime_disconnected')
    },
    stopSession: (sessionId) => {
      void client.stopRealtimeVoiceSession(sessionId).catch(() => undefined)
    },
    onState: (state) => {
      realtimeVoiceState.value = state
    },
    onEvent: handleRealtimeVoiceEvent,
  })
  try {
    await realtimeVoiceSession.start()
  } catch (error) {
    const code = error instanceof Error ? error.message : 'realtime_unavailable'
    errorText.value = realtimeVoiceErrorLabel(code)
  }
}

function stopRealtimeVoice() {
  realtimeVoiceMode = null
  realtimeVoiceMinecraftSessionId = ''
  if (realtimeReconnectTimer !== null) {
    window.clearTimeout(realtimeReconnectTimer)
    realtimeReconnectTimer = null
  }
  realtimeVoiceSession?.stop()
  realtimeVoiceSession = null
  realtimePlaybackEpoch += 1
  stopSpokenAudio()
  realtimeVoiceState.value = 'idle'
  realtimeAssistantTranscript.value = ''
  // Hand the character back to the stored conversation's own motions.
  realtimeCharacterMotion.value = undefined
}

async function controlRealtimeMinecraft(action: 'pause' | 'resume' | 'cancel') {
  const sessionId = realtimeVoiceSession?.sessionId || ''
  if (!sessionId) return
  try {
    const result = await client.controlRealtimeMinecraft(sessionId, action) as { ok?: boolean; state?: string; error?: string; recovery_required?: boolean }
    if (!result.ok) {
      errorText.value = result.recovery_required ? 'Minecraft Bridge 已进入恢复等待；请结束当前游戏会话后重新连接。' : result.error || '当前没有可控制的 Minecraft 动作。'
      return
    }
    realtimeVoiceState.value = action === 'pause' ? 'paused' : action === 'resume' ? 'acting' : 'listening'
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Minecraft 动作控制失败。'
  }
}

async function toggleVoiceInput() {
  if (voiceState.value === 'recording') {
    void stopVoiceRecording()
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
    if (!navigator.mediaDevices?.getUserMedia) {
      errorText.value = '当前 WebView 不支持麦克风录音'
      return
    }
    beginNewVoiceIntent()
    lastTranscript.value = ''
    lastAsrLatency.value = {}
    // Recorded as WAV rather than through MediaRecorder: that gives webm in
    // Chromium and mp4 in the packaged WKWebView, and the recogniser reads
    // neither. Getting the container wrong fails only after upload.
    await voiceRecorder.start()
    voiceStopTimer = window.setTimeout(() => void stopVoiceRecording(), voiceMaxSeconds.value * 1000)
    voiceState.value = 'recording'
  } catch {
    await cleanupVoiceStream()
    voiceState.value = 'idle'
    errorText.value = '麦克风启动失败'
  }
}

async function stopVoiceRecording() {
  if (!voiceRecorder.active) {
    await cleanupVoiceStream()
    voiceState.value = 'idle'
    return
  }
  voiceState.value = 'transcribing'
  if (voiceStopTimer !== null) {
    window.clearTimeout(voiceStopTimer)
    voiceStopTimer = null
  }
  const recording = await voiceRecorder.stop()
  if (!recording) {
    voiceState.value = 'idle'
    errorText.value = '我没有录到声音，请再说一次。'
    return
  }
  await transcribeVoiceBlob(recording.blob, recording.mimeType, recording.prepareMs)
}

async function cleanupVoiceStream() {
  if (voiceStopTimer !== null) {
    window.clearTimeout(voiceStopTimer)
    voiceStopTimer = null
  }
  if (voiceRecorder.active) await voiceRecorder.stop()
}

async function transcribeVoiceBlob(blob: Blob, mimeType: string, prepareMs = 0) {
  const perceivedStarted = performance.now()
  const generationId = voiceGenerationId(voiceEpoch)
  try {
    if (blob.size <= 0) {
      errorText.value = '我没有录到声音，请再说一次。'
      return
    }
    if (blob.size > voiceMaxBytes.value) {
      errorText.value = '这段语音太长了，我没有发送出去。'
      return
    }
    const encodeStarted = performance.now()
    const audioBase64 = await blobToBase64(blob)
    const encodeMs = Math.max(0, Math.round(performance.now() - encodeStarted))
    const rpcStarted = performance.now()
    const result = (await client.transcribeVoice(
      audioBase64,
      mimeType,
      voiceTranscribeTimeoutMs.value,
      activeContext.value.thread_id || '',
      generationId,
    )) as {
      ok?: boolean
      transcript?: string
      error?: string
      message?: string
      generation_id?: string
      stale?: boolean
      latency?: AsrLatencyBreakdown
    }
    const rpcMs = Math.max(0, Math.round(performance.now() - rpcStarted))
    const current = result.generation_id === generationId && !result.stale && generationId === voiceGenerationId(voiceEpoch)
    if (current) {
      lastAsrLatency.value = {
        prepare_ms: Math.max(0, Math.round(prepareMs)),
        encode_ms: encodeMs,
        rpc_ms: rpcMs,
        decode_ms: result.latency?.decode_ms,
        provider_ms: result.latency?.provider_ms,
        total_ms: Math.max(0, Math.round(prepareMs + performance.now() - perceivedStarted)),
      }
    }
    if (result.ok && result.transcript && current) lastTranscript.value = result.transcript
    if (!result.ok && current) errorText.value = result.message || result.error || '没有识别到语音'
  } catch (error) {
    if (generationId === voiceGenerationId(voiceEpoch)) {
      errorText.value = error instanceof Error ? error.message : '语音转写失败'
    }
  } finally {
    if (generationId === voiceGenerationId(voiceEpoch)) voiceState.value = 'idle'
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
  void safeWindowCall(() => getCurrentWindow().setTitleBarStyle('overlay'))
  void setNativeWindowControlsVisible(true)
  void connectToCore()
  clockTimer = window.setInterval(() => {
    nowSeconds.value = Date.now() / 1000
  }, 5000)
  window.addEventListener('dragstart', preventNativeAssetDrag, true)
  window.addEventListener('selectstart', preventCompactSelection, true)
  // The character's replies arrive over a socket, which is never a user
  // gesture, and a packaged webview may refuse to start audio outside one.
  // The first thing the user touches lifts that for the rest of the session.
  window.addEventListener('pointerdown', unlockAudioPlayback, { once: true, capture: true })
  window.addEventListener('keydown', unlockAudioPlayback, { once: true, capture: true })
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
  if (agentCliSyncTimer !== null) {
    window.clearTimeout(agentCliSyncTimer)
    agentCliSyncTimer = null
  }
  void cleanupVoiceStream()
  stopRealtimeVoice()
  stopSpokenAudio()
  resolveAppConfirm(false)
  client.close()
})

// The sheet's markup lives in components/layout/ContextRail.vue; its state
// stays here. See shellContext.ts for why the split runs this way round.
provide(ProjectsContextKey, {
  connected,
  characterName,
  activeProject,
  activeThread,
  activeContext,
  visibleProjects,
  visibleThreads,
  resourceBindings,
  contextSearch,
  contextRailBusy,
  showArchivedContext,
  newProjectName,
  editingRailItem,
  editingRailValue,
  switchProject,
  activateThread,
  createProjectFromRail,
  createThreadFromRail,
  archiveProjectFromRail,
  archiveThreadFromRail,
  deleteArchivedProject,
  deleteArchivedThread,
  beginRailRename,
  saveRailRename,
  bindProjectDirectory,
  addTextResourceBinding,
  removeResourceBinding,
})
</script>

<template>
  <main
    class="shell"
    :class="{
      'compact-active': isCompactMode,
      'mini-dashboard-active': isCompactMode && miniDashboardActive,
      'mini-speech-active': isCompactMode && miniSpeechActive,
      'settings-active': activeCabin === 'inspector',
      'stage-collapsed': stageIsCollapsed && activeCabin !== 'inspector',
    }"
    @dragstart.capture="preventNativeAssetDrag"
    @drop.capture.prevent
  >
    <!--
      DialogRoot spans the titlebar and the sheet because DialogTrigger has to
      be inside it. That is not a formality: the trigger is how Reka knows
      where to put focus back when the sheet closes. Driving `open` from a
      plain button instead left focus on <body> after Esc, which strands
      keyboard users at the top of the document.

      DialogRoot renders no element of its own, so the titlebar stays a direct
      child of .shell and the layout is untouched.
    -->
    <DialogRoot v-model:open="contextRailOpen">
    <!-- Header Titlebar -->
    <header class="titlebar" data-tauri-drag-region @mousedown="startWindowDrag" @dblclick="handleTitlebarDoubleClick">
      <!--
        Hidden in Settings. Settings is a full-window layer with a nav column of
        its own, and opening the project sheet on top of it put two navigations
        on screen at once, the sheet covering the settings categories entirely.
        PRD 9.1 puts Projects inside Workspace; it is not a peer of Settings.
      -->
      <DialogTrigger
        v-if="!isCompactMode && activeCabin !== 'inspector'"
        as-child
        @mousedown.stop
        @click.stop="!contextRailOpen && refreshCollaboration()"
      >
        <button type="button" class="context-rail-trigger" :class="{ active: contextRailOpen }" aria-label="打开项目与对话">
          <Menu :size="17" :stroke-width="1.8" />
          <span>{{ activeProject?.name || '项目' }}</span>
        </button>
      </DialogTrigger>
      <button type="button" class="window-title" title="返回对话" @mousedown.stop @click.stop="openCabin('chat')">Joi</button>

      <div class="topbar-actions">
        <button
          type="button"
          class="luna-title-action"
          :class="{ active: activeCabin === 'inspector' }"
          @mousedown.stop
          @click.stop="openCabin(activeCabin === 'inspector' ? 'chat' : 'inspector')"
        >
          <MessageCircle v-if="activeCabin === 'inspector'" :size="17" :stroke-width="1.8" />
          <Settings2 v-else :size="17" :stroke-width="1.8" />
          <span>{{ activeCabin === 'inspector' ? '返回对话' : '设置' }}</span>
        </button>
        <div class="luna-role-menu" v-if="activeCabin !== 'inspector'">
          <button
            type="button"
            class="luna-title-action role-trigger"
            :class="{ active: activeCabin === 'characters' }"
            @mousedown.stop
            @click.stop="openCabin('characters')"
          >
            <Bot :size="17" :stroke-width="1.8" />
            {{ characterName }}
          </button>
        </div>
      </div>
    </header>

    <!--
      A real modal sheet, not a panel parked off-screen.

      This used to be an <aside> that stayed mounted and slid out of view with
      `translateX(-100%)`. Off-screen is not gone: the panel kept ten focusable
      controls in the tab order, so tabbing from the titlebar walked into a
      dialog nobody could see. It also had no Esc, no focus trap, no focus
      return, and its backdrop was a full-window <button> that was itself a
      phantom tab stop.

      DialogRoot supplies all of that, and unmounts the content when closed --
      which is the actual fix. See tests/context-rail-modal.test.mjs.
    -->
      <ContextRail v-if="!isCompactMode" />
    </DialogRoot>

    <section ref="workspaceRef" class="workspace" :class="`cabin-${activeCabin}`">
      <div class="topbar" v-if="activeCabin !== 'inspector'">
        <div>
          <span class="brand">Joi</span>
          <span class="mode">{{ currentMode }}</span>
        </div>
        <div class="top-actions">
          <button type="button" class="ghost-button workspace-chat-action" @click="openCabin('chat')" v-if="activeCabin !== 'chat'">
            <MessageCircle :size="17" :stroke-width="1.8" />
            <span>返回对话</span>
          </button>
          <button type="button" class="ghost-button watch-loop-action" @click="watchLoopActive ? stopWatchLoop() : startWatchLoop()" v-if="activeCabin === 'workspace'">
            {{ watchLoopActive ? '停止陪看' : '实时陪看' }}
          </button>
          <button type="button" class="ghost-button" @click="developerMode = !developerMode" v-if="activeCabin === 'workspace'">
            {{ developerMode ? '隐藏审计' : '显示审计' }}
          </button>
          <span class="status" :class="{ online: connected }">{{ connectionLabel }}</span>
        </div>
      </div>

      <p class="error" v-if="errorText && activeCabin !== 'inspector'">{{ errorText }}</p>

      <!-- Capability session controls live in Workspace, not in the
           conversation: PRD 9.1 puts "任务卡和能力会话控制" here and leaves
           Conversation for dialogue and results. In the chat they took
           permanent vertical space and stayed after the session had already
           finished. Shown only while a session can still be acted on -- a
           finished run needs no pause button, and that was most of the
           clutter. -->
      <section class="capability-session-strip" v-if="activeCabin === 'workspace' && liveCapabilitySession">
        <div class="capability-session-main">
          <span class="capability-session-dot" :class="`state-${liveCapabilitySession.state}`"></span>
          <div>
            <strong>{{ capabilityStateLabel }}</strong>
            <span>{{ liveCapabilitySession.goal || '当前能力会话' }}</span>
          </div>
          <span class="capability-session-driver">{{ liveCapabilitySession.driver === 'cua' ? '后台 CUA' : 'Joi 原生' }}</span>
        </div>
        <div class="capability-session-controls">
          <div class="capability-permissions" aria-label="能力权限">
            <button
              v-for="profile in (['observe', 'collaborate', 'delegate'] as PermissionProfile[])"
              :key="profile"
              type="button"
              :class="{ active: liveCapabilitySession.permission_profile === profile }"
              :aria-pressed="liveCapabilitySession.permission_profile === profile"
              @click="changeCapabilityPermission(profile)"
            >{{ permissionProfileLabel(profile) }}</button>
          </div>
          <div class="capability-session-actions">
            <button type="button" @click="toggleCapabilityPause">
              <Play v-if="liveCapabilitySession.state === 'paused'" :size="14" />
              <Pause v-else :size="14" />
              {{ liveCapabilitySession.state === 'paused' ? '继续' : '暂停' }}
            </button>
            <button type="button" v-if="liveCapabilitySession.state === 'running'" @click="takeOverCapability"><Hand :size="14" />我来接管</button>
            <button type="button" class="danger" @click="cancelCapability"><X :size="14" />取消</button>
          </div>
        </div>
      </section>

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
            <span>模式</span>
            <select v-model="watchSceneMode" @change="changeWatchSceneMode">
              <option value="quiet">安静共看</option>
              <option value="commentary">轻声解说</option>
              <option value="translate">即时翻译</option>
              <option value="analysis">片段分析</option>
              <option value="accessibility">无障碍描述</option>
            </select>
          </label>
          <label>
            <span>剧透</span>
            <select v-model="watchSpoilerLevel" @change="configureWatchLoop">
              <option value="none">不剧透</option>
              <option value="current_scene">仅当前片段</option>
              <option value="full">允许完整讨论</option>
            </select>
          </label>
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
            <span>允许插话</span>
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
          <small>默认不保存原始画面或音频</small>
        </div>
        <button type="button" class="ghost-button watch-session-stop" @click="watchLoopActive ? stopWatchLoop() : startWatchLoop()">
          {{ watchLoopActive ? '停止' : '重新开始' }}
        </button>
      </section>

      <section class="runtime-console" v-if="activeCabin === 'workspace'">
        <div class="section-title">
          <h2>Joi</h2>
          <span>{{ terminalRows.length ? `${terminalRows.length} 条` : '待命' }}</span>
        </div>
        <div class="console-scroll" v-if="terminalRows.length">
          <div
            v-for="row in terminalRows"
            :key="`${row.event.task_id}-${row.event.created_at}-${row.event.type}`"
            class="console-line"
            :class="row.role"
          >
            <span>{{ row.label }}</span>
            <div>
              <p>{{ row.text }}</p>
              <pre v-if="row.detail">{{ row.detail }}</pre>
            </div>
          </div>
        </div>
        <div class="console-empty" v-else>
          <strong>{{ characterName }} 已经准备好。</strong>
          <span>{{ ready?.character?.greeting || '直接说你想做什么，也可以打开实时陪看。' }}</span>
        </div>
        <div class="approval-actions console-approval" v-if="pendingApproval && approvalIdFor(pendingApproval)">
          <button type="button" @click="resolveApproval(true)">允许执行</button>
          <button type="button" class="secondary" @click="resolveApproval(false)">停在这里</button>
        </div>
      </section>

      <section class="task-section" v-if="activeCabin === 'workspace' && developerMode && taskRows.length">
        <div class="section-title">
          <h2>开发审计</h2>
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
        <header class="chat-presence" :class="{ offline: status === 'offline' }">
          <span class="chat-presence-avatar" aria-hidden="true">
            <img v-if="characterAvatarSrc" :src="characterAvatarSrc" alt="" />
            <Sparkles v-else :size="17" />
          </span>
          <div>
            <strong>{{ characterName }}</strong>
            <span><i :class="{ online: connected }"></i>{{ connectionPresenceLabel }}</span>
          </div>
          <button
            v-if="status === 'offline'"
            type="button"
            class="chat-reconnect-button"
            :disabled="coreRetrying"
            @click="retryCoreConnection"
          >
            <RefreshCw :size="13" :class="{ spinning: coreRetrying }" />
            {{ coreRetrying ? '重启中' : '重新连接' }}
          </button>
        </header>
        <div class="chat-connection-notice" role="alert" v-if="status === 'offline' && errorText">
          <AlertCircle :size="15" />
          <span>{{ errorText }}</span>
        </div>
        <!-- The capability-session card used to sit here. It took permanent
             vertical space above the message list and stayed after a session
             had already finished, so a completed run kept squeezing the chat.
             Its pause / take-over / cancel controls need a home that does not
             cost the conversation room -- see the chat redesign plan. -->
        <div
          ref="chatScrollArea"
          class="chat-scroll-area"
          v-if="conversationTurns.length"
          aria-live="polite"
          @scroll.passive="onChatScroll"
        >
          <article v-for="turn in conversationTurns" :key="turn.taskId" class="conversation-turn">
            <!-- No "你" label: a right-aligned bubble already says who wrote
                 it, and repeating it on every turn is noise GPT and Claude
                 both dropped. The timestamp moves to a tooltip. -->
            <div class="message-row human" v-if="turn.user" :title="turnTimeLabel(turn)">
              <div class="message-bubble">{{ turn.user.display_card.summary }}</div>
            </div>

            <div
              v-if="(turn.status === 'running' || turn.status === 'queued') && !turn.assistant"
              class="thinking-companion"
              role="status"
              aria-live="polite"
            >
              <span class="thinking-avatar">
                <img v-if="characterAvatarSrc" :src="characterAvatarSrc" alt="" />
                <Sparkles v-else :size="15" />
              </span>
              <span class="thinking-copy">
                <strong>{{ turnProgressLabel(turn) }}</strong>
                <ThinkingOrb :state="turnOrbState(turn)" :size="20" :label="turnProgressLabel(turn)" />
              </span>
            </div>

            <div class="message-row joi" v-if="turn.assistant">
              <span class="assistant-avatar" aria-hidden="true">
                <img v-if="authorAvatar(turn.assistant)" :src="authorAvatar(turn.assistant)" alt="" />
                <Sparkles v-else :size="15" />
              </span>
              <div class="assistant-message">
                <!-- The character who said it, not the one active now: after a
                     switch the old replies are still on screen, and naming
                     them after the new character makes it look like the new
                     one introduced itself by the old one's name. -->
                <span class="chat-name">{{ authorName(turn.assistant) }}</span>
                <div class="message-bubble">{{ assistantText(turn.assistant) }}</div>
              </div>
            </div>

            <section
              v-if="turn.hasTrace && turn.status !== 'running' && turn.status !== 'queued'"
              class="execution-rail"
              :class="`status-${turn.status}`"
              :aria-label="`任务状态：${turnStatusLabel(turn)}`"
            >
              <!-- One line, not a card. A routine success used to occupy more
                   height than the reply it belonged to; the timestamp moved to
                   a tooltip because it is almost never what the user is after.
                   Failure keeps its icon and colour, and opens itself. -->
              <button
                type="button"
                class="execution-rail-head"
                :aria-expanded="isTurnExpanded(turn)"
                :title="turnTimeLabel(turn)"
                @click="toggleTurnTrace(turn)"
              >
                <AlertCircle v-if="turn.status === 'waiting' || turn.status === 'failed'" class="execution-status-icon" :size="14" />
                <CheckCircle2 v-else class="execution-status-icon" :size="14" />
                <strong>{{ turnStatusLabel(turn) }}</strong>
                <span class="execution-step-count" v-if="turn.steps.length">&nbsp;· {{ turn.steps.length }} 步</span>
                <ChevronDown class="execution-chevron" :class="{ open: isTurnExpanded(turn) }" :size="14" />
              </button>
              <p class="execution-current" v-if="isTurnExpanded(turn) || turn.status === 'waiting' || turn.status === 'failed'">{{ turnStatusText(turn) }}</p>
              <ol class="execution-steps" v-if="isTurnExpanded(turn)">
                <li v-for="step in turn.steps" :key="step.key" :class="`step-${step.state}`">
                  <span class="execution-step-marker" aria-hidden="true"></span>
                  <div>
                    <strong>{{ step.label }}</strong>
                    <p>{{ step.detail }}</p>
                  </div>
                </li>
              </ol>
              <div class="execution-approval" v-if="turn.approval && approvalIdFor(turn.approval)">
                <p>{{ turn.approval.display_card.summary }}</p>
                <div>
                  <button type="button" @click="resolveApprovalFor(turn.approval, true)">允许并继续</button>
                  <button type="button" class="secondary" @click="resolveApprovalFor(turn.approval, false)">停在这里</button>
                </div>
              </div>
              <button v-if="turn.status === 'failed'" type="button" class="execution-retry" :disabled="composerSending" :aria-busy="composerSending" @click="retryTurn(turn)">
                <RotateCcw :size="14" />
                重试这条请求
              </button>
            </section>
          </article>
        </div>
        <div class="chat-placeholder" v-else>
          <span class="placeholder-avatar" aria-hidden="true">
            <img v-if="characterAvatarSrc" :src="characterAvatarSrc" alt="" />
            <Sparkles v-else :size="21" />
          </span>
          <span class="chat-name">{{ characterName }}</span>
          <p>{{ characterGreeting }}</p>
          <div class="chat-starters" aria-label="对话建议">
            <button type="button" :disabled="!connected" @click="sendChatStarter('陪我聊聊今天发生的事')">聊聊今天</button>
            <button type="button" :disabled="!connected" @click="sendChatStarter('帮我整理一下接下来最重要的三件事')">整理计划</button>
            <button type="button" :disabled="!connected" @click="sendChatStarter('你看到了什么？')">看看屏幕</button>
          </div>
        </div>

        <form class="chat-composer-area" @submit.prevent="submit">
          <div class="composer-attachments" v-if="composerAttachments.length || attachmentPickerError" aria-live="polite">
            <div class="attachment-list" v-if="composerAttachments.length">
              <div
                v-for="attachment in composerAttachments"
                :key="attachment.path"
                class="attachment-chip"
                :title="attachment.path"
              >
                <FolderIcon v-if="attachment.kind === 'folder'" :size="15" :stroke-width="1.8" />
                <FileIcon v-else :size="15" :stroke-width="1.8" />
                <span>{{ attachment.name }}</span>
                <button
                  type="button"
                  :aria-label="`移除 ${attachment.name}`"
                  @click="removeAttachment(attachment.path)"
                >
                  <X :size="13" :stroke-width="1.9" />
                </button>
              </div>
            </div>
            <p class="attachment-error" v-if="attachmentPickerError">{{ attachmentPickerError }}</p>
          </div>
          <div class="luna-quick-menu">
            <button
              type="button"
              class="composer-icon-btn"
              title="打开工具"
              aria-haspopup="true"
              :aria-expanded="quickMenuOpen"
              @click="quickMenuOpen = !quickMenuOpen; characterMenuOpen = false"
            >
              <Paperclip :size="20" :stroke-width="1.75" />
            </button>
            <div class="luna-popover quick-popover" v-if="quickMenuOpen">
              <button type="button" :disabled="attachmentPickerBusy" :aria-busy="attachmentPickerBusy" @click="addAttachments('file')">
                <FilePlus2 :size="18" :stroke-width="1.75" />
                <span>{{ attachmentPickerBusy ? '正在打开选择器…' : '添加文件' }}</span>
              </button>
              <button type="button" :disabled="attachmentPickerBusy" :aria-busy="attachmentPickerBusy" @click="addAttachments('folder')">
                <FolderPlus :size="18" :stroke-width="1.75" />
                <span>添加文件夹</span>
              </button>
              <div class="quick-menu-divider" role="separator"></div>
              <button type="button" @click="openCabin('memory')">
                <Brain :size="18" :stroke-width="1.75" />
                <span>记忆</span>
              </button>
              <button type="button" @click="watchLoopActive ? stopWatchLoop() : startWatchLoop(); quickMenuOpen = false">
                <MonitorPlay :size="18" :stroke-width="1.75" />
                <span>{{ watchLoopActive ? '停止实时陪看' : '开始实时陪看' }}</span>
              </button>
              <button type="button" :disabled="!connected || (!realtimeVoiceConfigured && !realtimeVoiceActive)" @click="toggleRealtimeVoice(); quickMenuOpen = false">
                <AudioLines :size="18" :stroke-width="1.75" />
                <span>{{ realtimeVoiceActive ? '结束实时语音' : '开始实时语音' }}</span>
              </button>
              <button v-if="activeMinecraftSessionId && !realtimeVoiceActive" type="button" :disabled="!connected || !realtimeVoiceConfigured" @click="toggleRealtimeVoice(true); quickMenuOpen = false">
                <Gamepad2 :size="18" :stroke-width="1.75" />
                <span>实时语音 + Minecraft</span>
              </button>
              <button type="button" :disabled="compactTransitioning" @click="toggleCompactMode(); quickMenuOpen = false">
                <Minimize2 :size="18" :stroke-width="1.75" />
                <span>切换微缩模式</span>
              </button>
              <button type="button" @click="openCabin('inspector')">
                <Settings :size="18" :stroke-width="1.75" />
                <span>完整设置</span>
              </button>
            </div>
          </div>
          <input
            v-model="input"
            class="composer-text-input"
            :disabled="!connected || composerSending" :aria-busy="composerSending"
            placeholder="说点什么..."
          />
          <button
            type="button"
            class="composer-icon-btn composer-mic-btn"
            :class="{ recording: voiceState === 'recording' }"
            :disabled="!connected || !asrConfigured || voiceState === 'transcribing' || realtimeVoiceActive"
            @click="toggleVoiceInput"
            title="语音说话"
          >
            <Mic :size="20" :stroke-width="1.75" />
          </button>
          <button class="composer-send-btn" :disabled="!composerCanSubmit" title="发送消息">
            <ArrowUp :size="22" :stroke-width="2" />
          </button>
        </form>
      </section>

      <section class="character-library-section" v-if="activeCabin === 'characters'">
        <CharacterLibrary :client="client" :connected="connected" @activated="handleCharacterActivated" @close="openCabin('chat')" />
      </section>

      <section class="memory-section" v-if="activeCabin === 'memory'">
        <header class="memory-workspace-head">
          <div class="memory-workspace-title">
            <span class="memory-title-icon"><Brain :size="21" :stroke-width="1.8" /></span>
            <div>
              <h2>Joi 对你的理解</h2>
              <p>{{ memoryProfile?.summary || '对话中形成的长期偏好，会在你确认后出现在这里。' }}</p>
            </div>
          </div>
          <div class="memory-workspace-actions">
            <label class="memory-switch" title="长期记忆开关">
              <input type="checkbox" :checked="memoryEnabled" @change="toggleMemoryEnabled" />
              <span aria-hidden="true"></span>
              <em>{{ memoryEnabled ? '记忆开启' : '记忆关闭' }}</em>
            </label>
            <button type="button" class="memory-icon-button" :disabled="memorySearchLoading" :aria-busy="memorySearchLoading" title="刷新记忆" @click="refreshMemoryWorkspace">
              <RefreshCw :size="18" :class="{ spinning: memorySearchLoading }" />
            </button>
            <details class="memory-more-menu">
              <summary title="更多记忆操作"><ChevronDown :size="18" /></summary>
              <button type="button" :disabled="!memorySavedCount && !memoryPendingCount" @click="clearMemory">
                <Trash2 :size="16" />清空全部记忆
              </button>
            </details>
          </div>
        </header>

        <div class="memory-profile-rail" aria-label="记忆画像">
          <article v-for="section in memoryProfileSections" :key="section.key">
            <span>{{ section.label }}</span>
            <p v-if="section.rows.length">{{ section.rows[0] }}</p>
            <p v-else class="empty">等待更多线索</p>
            <small v-if="section.rows.length > 1">另有 {{ section.rows.length - 1 }} 条</small>
          </article>
        </div>

        <section class="memory-library">
          <header class="memory-library-head">
            <div class="memory-tabs" role="tablist" aria-label="记忆列表">
              <button type="button" role="tab" :aria-selected="memoryView === 'saved'" :class="{ active: memoryView === 'saved' }" @click="selectMemoryView('saved')">
                已保存 <span>{{ memorySavedCount }}</span>
              </button>
              <button type="button" role="tab" :aria-selected="memoryView === 'pending'" :class="{ active: memoryView === 'pending' }" @click="selectMemoryView('pending')">
                待确认 <span>{{ memoryPendingCount }}</span>
              </button>
            </div>
            <form v-if="memoryView === 'saved'" class="memory-search-inline" @submit.prevent="searchMemory">
              <Search :size="17" />
              <input v-model="memoryQuery" type="search" placeholder="搜索记忆" @search="searchMemory" />
              <button type="submit" :disabled="memorySearchLoading" :aria-busy="memorySearchLoading" title="搜索">搜索</button>
            </form>
          </header>

          <div v-if="memoryView === 'saved'" class="memory-record-list">
            <article v-for="memory in displayedMemoryRows" :key="memory.id" class="memory-record">
              <template v-if="editingMemoryId !== memory.id">
                <div class="memory-record-copy">
                  <div class="memory-record-meta">
                    <span>{{ memoryKindLabel(memory.kind) }}</span>
                    <small>{{ memoryTime(memory.updated_at || memory.created_at) }}</small>
                  </div>
                  <p>{{ memory.text }}</p>
                </div>
                <div class="memory-record-actions">
                  <button type="button" title="编辑记忆" @click="beginMemoryEdit(memory)"><Pencil :size="16" /></button>
                  <button type="button" class="danger" title="删除记忆" @click="deleteMemory(memory.id)"><Trash2 :size="16" /></button>
                </div>
              </template>
              <form v-else class="memory-edit-form" @submit.prevent="saveMemoryEdit(memory.id)">
                <select v-model="editingMemoryKind" aria-label="记忆类型">
                  <option value="preference">偏好</option>
                  <option value="habit">习惯</option>
                  <option value="relationship">关系</option>
                  <option value="project">关注</option>
                  <option value="note">笔记</option>
                </select>
                <textarea v-model="editingMemoryText" rows="3" maxlength="1200" aria-label="记忆内容"></textarea>
                <div><button type="button" @click="cancelMemoryEdit">取消</button><button type="submit" class="primary">保存</button></div>
              </form>
            </article>
            <p v-if="!displayedMemoryRows.length && !memorySearchLoading" class="memory-empty-state">{{ memorySearchEmptyText }}</p>
          </div>

          <div v-else class="memory-record-list">
            <article v-for="candidate in displayedMemoryCandidates" :key="candidate.id" class="memory-record pending">
              <div class="memory-record-copy">
                <div class="memory-record-meta"><span>{{ memoryKindLabel(candidate.kind) }}</span><small>{{ memoryPriorityLabel(candidate.priority) }}</small></div>
                <p>{{ candidate.text }}</p>
                <small class="memory-record-reason">{{ candidate.priority_reason || '由对话中提取，保存前需要你的确认' }}</small>
              </div>
              <div class="memory-candidate-actions">
                <button type="button" @click="rejectMemoryCandidate(candidate.id)">忽略</button>
                <button type="button" class="primary" @click="saveMemoryCandidate(candidate.id)">记住</button>
              </div>
            </article>
            <p v-if="!displayedMemoryCandidates.length && !memorySearchLoading" class="memory-empty-state">没有待确认的记忆</p>
          </div>

          <button v-if="memoryHasMore" type="button" class="memory-load-more" :disabled="memorySearchLoading" :aria-busy="memorySearchLoading" @click="loadMemoryPage(false)">
            {{ memorySearchLoading ? '正在读取…' : `再加载 ${Math.min(12, memoryTotal - memoryOffset)} 条` }}
          </button>

          <footer class="memory-storage-row">
            <span><span class="memory-storage-dot"></span>本地存储</span>
            <small>{{ memoryVault?.path_label || memoryStatus?.vault_label || 'joi_memory_vault.md' }}</small>
          </footer>
        </section>
      </section>

      <section class="debug-section open-settings" v-if="activeCabin === 'inspector'">
        <div class="settings-shell">
          <aside class="settings-sidebar" aria-label="设置导航">
            <button type="button" class="settings-back-button" @click="openCabin('chat')">
              <ArrowLeft :size="19" :stroke-width="1.75" />
              <span>返回对话</span>
            </button>
            <label class="settings-search-field">
              <Search :size="18" :stroke-width="1.75" />
              <input v-model="settingsSearch" type="search" placeholder="搜索设置..." @keydown.esc="settingsSearch = ''" />
            </label>
            <nav class="settings-nav-groups">
              <section v-for="group in filteredSettingsGroups" :key="group.label" class="settings-nav-group">
                <h2>{{ group.label }}</h2>
                <button
                  v-for="tab in group.tabs"
                  :key="tab.id"
                  type="button"
                  class="settings-nav-row"
                  :class="{ active: activeSettingsTab === tab.id }"
                  :aria-current="activeSettingsTab === tab.id ? 'page' : undefined"
                  @click="activeSettingsTab = tab.id"
                >
                  <component :is="settingsIcon(tab.id)" class="settings-nav-icon" :size="19" :stroke-width="1.75" />
                  <span>{{ tab.label }}</span>
                </button>
              </section>
              <p class="settings-search-empty" v-if="!filteredSettingsGroups.length">没有匹配的设置</p>
            </nav>
          </aside>

          <section class="settings-main">
            <header class="settings-open-header">
              <div>
                <span class="settings-header-kicker">
                  <component :is="settingsIcon(activeSettingsTab)" :size="17" :stroke-width="1.75" />
                  设置
                </span>
                <h1>{{ settingsTitle(activeSettingsTab) }}</h1>
                <p>{{ settingsSubtitle(activeSettingsTab) }}</p>
              </div>
              <!--
                No close button here. It sat about 90px directly below the
                titlebar's "返回对话", top-right, doing the identical thing --
                three controls with the same label were on screen at once.
                What remains is two, in two regions: back at the top of the
                settings nav, and the global toggle in the titlebar.
              -->
            </header>

            <template v-if="activeSettingsTab === 'execution'">
              <TabsRoot v-model="executionMode" class="execution-tabs">
              <!--
                Reka Tabs, not hand-written ARIA. This carried
                `role="tablist"` while its two children were plain buttons
                with no `role="tab"` and no `aria-selected` -- a tab list
                that announces itself and then contains no tabs, which is
                worse for a screen reader than no role at all. Arrow-key
                movement and roving tabindex were missing too.
              -->
              <TabsList class="execution-segment" aria-label="执行模式">
                <TabsTrigger value="local_cli">本机 CLI</TabsTrigger>
                <TabsTrigger value="byok">BYOK</TabsTrigger>
              </TabsList>
              <TabsContent value="local_cli" class="settings-execution-pane">
                <div class="settings-section-head">
                  <div>
                    <strong>你的 CLI（{{ displayedAgentClis.length }}）</strong>
                  <span>选择接管 Joi 请求的本机运行时。</span>
                  </div>
                  <button type="button" class="settings-outline-button" :disabled="agentCliLoading || !connected" :aria-busy="agentCliLoading" @click="refreshAgentClis">
                    {{ agentCliLoading ? '扫描中' : '重新扫描' }}
                  </button>
                </div>

                <div class="agent-cli-list">
                  <div v-if="agentCliCoreUnsupported" class="agent-cli-warning">
                    <strong>Joi 运行时未刷新</strong>
                    <span>请重启桌面应用后再扫描 CLI。</span>
                  </div>
                  <article
                    v-for="row in displayedAgentClis"
                    :key="row.id"
                    class="agent-cli-card"
                    :class="{ selected: selectedAgentCliId === row.id, missing: !row.installed }"
                    @click="selectAgentCli(row)"
                  >
                    <div class="agent-cli-icon" :class="row.id">{{ agentCliIcon(row) }}</div>
                    <div class="agent-cli-copy">
                      <strong>{{ row.name }}</strong>
                      <span>{{ agentCliMeta(row) }}</span>
                    </div>
                    <button
                      type="button"
                      class="agent-cli-test"
                      :disabled="agentCliTestDisabled(row)"
                      @click.stop="testAgentCli(row)"
                    >
                      测试
                    </button>
                  </article>
                </div>

                <div class="settings-config-card">
                  <div class="settings-config-title">
                    <span>模型：</span>
                    <strong>{{ selectedAgentCli?.name || 'Codex CLI' }}</strong>
                    <em :class="{ warning: selectedAgentCli?.models_source === 'fallback' }">{{ selectedAgentCliModelSource }}</em>
                  </div>
                  <label class="settings-select-row">
                    <span>模型</span>
                    <select v-model="selectedAgentCliModel">
                      <option v-for="model in selectedAgentCliModels" :key="model.id" :value="model.id">{{ model.label }}</option>
                    </select>
                  </label>
                  <p>{{ selectedAgentCliModelHint }}</p>
                  <label class="settings-select-row">
                    <span>推理强度</span>
                    <select v-model="selectedAgentCliReasoning">
                      <option v-for="option in selectedAgentCliReasoningOptions" :key="option" :value="option">{{ option }}</option>
                    </select>
                  </label>
                  <div class="agent-cli-status-line">
                    <span>{{ agentCliTakeoverText(selectedAgentCli) }}</span>
                    <strong>{{ agentCliSyncing ? '同步中' : selectedAgentCli ? agentCliStatus(selectedAgentCli) : '未选择' }}</strong>
                  </div>
                  <div class="agent-cli-status-line mcp-connect-line" v-if="selectedAgentCliId === 'codex'">
                    <span>{{ joiMcpStatus?.connected ? 'Joi 能力已连接到当前运行时' : '连接 Joi 的陪看、记忆和技能能力' }}</span>
                    <button type="button" class="settings-outline-button" :disabled="!connected || joiMcpInstalling" :aria-busy="joiMcpInstalling" @click="installJoiMcp">
                      {{ joiMcpInstalling ? '连接中' : joiMcpStatus?.connected ? '重新连接' : '一键连接' }}
                    </button>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="byok" class="settings-execution-pane">
                <section class="byok-connect-card" :class="{ connected: byokStatus?.configured }">
                  <header class="byok-connect-head">
                    <div class="byok-status-icon">
                      <CheckCircle2 v-if="byokStatus?.configured" :size="21" :stroke-width="1.9" />
                      <KeyRound v-else :size="21" :stroke-width="1.8" />
                    </div>
                    <div>
                      <strong>{{ byokStateLabel() }}</strong>
                      <span v-if="byokStatus?.configured">{{ selectedByokPreset?.label }} · {{ byokStatus?.model }}</span>
                      <span v-else>三步完成连接，密钥不会写入项目文件。</span>
                    </div>
                    <button v-if="byokStatus?.configured" type="button" class="byok-subtle-action" :disabled="byokLoading" :aria-busy="byokLoading" @click="testByokConnection">
                      {{ byokLoading ? '测试中' : '重新测试' }}
                    </button>
                  </header>

                  <div class="byok-progress" aria-label="BYOK 配置进度">
                    <span class="done"><b>1</b>供应商</span>
                    <span :class="{ done: byokSecretReady }"><b>2</b>{{ byokRequiresKey ? '密钥' : '本地服务' }}</span>
                    <span :class="{ done: byokStatus?.configured }"><b>3</b>连接</span>
                  </div>
                </section>

                <form class="byok-form" @submit.prevent="connectByok">
                  <section class="byok-form-section">
                    <div class="byok-section-title">
                      <span>1</span>
                      <div><strong>选择供应商</strong><small>优先选择最接近你的使用方式</small></div>
                    </div>
                    <div class="byok-provider-options">
                      <button
                        v-for="preset in byokPresets"
                        :key="preset.id"
                        type="button"
                        :class="{ selected: byokDraft.provider === preset.id }"
                        @click="selectByokPreset(preset)"
                      >
                        <span class="byok-provider-mark">
                          <WalletCards v-if="preset.id === 'openai'" :size="19" :stroke-width="1.8" />
                          <Server v-else-if="preset.id === 'ollama'" :size="19" :stroke-width="1.8" />
                          <Zap v-else :size="19" :stroke-width="1.8" />
                        </span>
                        <strong>{{ preset.label }}</strong>
                        <small>{{ preset.description }}</small>
                        <em>{{ preset.cost_hint }}</em>
                      </button>
                    </div>
                  </section>

                  <section class="byok-form-section">
                    <div class="byok-section-title">
                      <span>2</span>
                      <div><strong>{{ byokRequiresKey ? '填写连接信息' : '选择本地模型' }}</strong><small>{{ byokSecretLabel() }}</small></div>
                    </div>
                    <div class="byok-fields">
                      <label v-if="byokRequiresKey" class="byok-field byok-field-wide">
                        <span>API Key <em v-if="byokStatus?.secret?.stored">已保存，留空即可沿用</em></span>
                        <input v-model="byokApiKey" type="password" autocomplete="new-password" spellcheck="false" :placeholder="byokStatus?.secret?.stored ? '••••••••••••••••（已安全保存）' : '粘贴 API Key'" @input="markByokDirty" />
                      </label>
                      <label class="byok-field byok-field-wide">
                        <span>模型 ID <em v-if="byokDraft.provider === 'ollama'">可自动检测</em></span>
                        <div class="byok-field-with-action">
                          <input v-model="byokDraft.model" list="byok-known-models" spellcheck="false" :placeholder="byokDraft.provider === 'ollama' ? '例如 qwen3:8b' : '例如 gpt-5.6-luna'" @input="markByokDirty" />
                          <button v-if="byokDraft.provider === 'ollama'" type="button" :disabled="byokDiscovering || !connected" @click="detectLocalModels">
                            {{ byokDiscovering ? '检测中…' : '自动检测' }}
                          </button>
                        </div>
                        <datalist id="byok-known-models">
                          <option v-for="model in byokKnownModels" :key="model" :value="model" />
                        </datalist>
                      </label>
                      <label class="byok-field byok-field-wide" v-if="byokDraft.provider !== 'openai' || byokAdvancedOpen">
                        <span>API 端点</span>
                        <input v-model="byokDraft.base_url" type="url" spellcheck="false" placeholder="https://example.com/v1" @input="markByokDirty" />
                      </label>
                    </div>
                  </section>

                  <section class="byok-form-section byok-advanced-section">
                    <button type="button" class="byok-advanced-toggle" :aria-expanded="byokAdvancedOpen" @click="byokAdvancedOpen = !byokAdvancedOpen">
                      <ChevronDown :size="17" :class="{ open: byokAdvancedOpen }" />
                      高级设置
                    </button>
                    <div v-if="byokAdvancedOpen" class="byok-fields byok-advanced-fields">
                      <label class="byok-field">
                        <span>温度</span>
                        <input v-model.number="byokDraft.temperature" type="number" min="0" max="2" step="0.1" @input="markByokDirty" />
                      </label>
                      <label v-if="byokDraft.provider === 'openai'" class="byok-field">
                        <span>官方端点</span>
                        <input value="https://api.openai.com/v1" disabled />
                      </label>
                    </div>
                  </section>

                  <div class="byok-cost-note">
                    <Shield :size="18" :stroke-width="1.8" />
                    <span v-if="byokDraft.provider === 'ollama'"><strong>本地运行，零 API 费用</strong> 自动检测只读取 Ollama 模型列表，不生成内容、不消耗模型 token。</span>
                    <span v-else><strong>安全且省额度</strong> 密钥存入系统密钥库；连接测试只读取模型列表，不生成内容、不消耗模型 token。</span>
                  </div>

                  <div class="byok-actions">
                    <button type="submit" class="byok-primary-action" :disabled="!byokCanConnect">
                      <Zap :size="17" :stroke-width="1.9" />
                      {{ byokLoading ? '保存并测试中…' : byokStatus?.configured ? '保存并重新测试' : '保存并连接' }}
                    </button>
                    <button v-if="byokStatus?.configured" type="button" class="byok-danger-action" :disabled="byokLoading" :aria-busy="byokLoading" @click="disconnectByok">
                      <Unplug :size="16" :stroke-width="1.8" />断开
                    </button>
                  </div>

                  <p v-if="byokNotice" class="byok-notice" :class="{ success: byokResult?.ok }" aria-live="polite">{{ byokNotice }}</p>
                </form>

                <details class="byok-runtime-details">
                  <summary>查看全部运行能力</summary>
                  <div class="provider-grid">
                    <div v-for="row in runtimeStatusRows()" :key="row.name" class="provider-card" :class="row.state">
                      <header><strong>{{ row.label || row.name }}</strong><span>{{ providerStateLabel(row.state) }}</span></header>
                      <p>{{ providerSummary(row) }}</p>
                    </div>
                  </div>
                </details>
              </TabsContent>
              </TabsRoot>
            </template>

            <div class="settings-runtime-pane" v-else-if="activeSettingsTab === 'runtime'">
              <div class="provider-grid settings-provider-grid">
                <article v-for="row in runtimeStatusRows()" :key="row.name" class="provider-card" :class="row.state">
                  <header><strong>{{ row.label || row.name }}</strong><span>{{ providerStateLabel(row.state) }}</span></header>
                  <p>{{ providerSummary(row) }}</p>
                </article>
              </div>
              <details class="runtime-advanced-card">
                <summary><span><strong>运行参数</strong><small>语音、识别和模型行为</small></span><ChevronDown :size="18" /></summary>
                <div class="runtime-controls">
                  <label><span>语音识别</span><input v-model="runtimeDraft.asr_enabled" type="checkbox" @change="markRuntimeDraftDirty" /></label>
                  <label><span>语音回复</span><input v-model="runtimeDraft.tts_enabled" type="checkbox" @change="markRuntimeDraftDirty" /></label>
                  <label><span>音量</span><input v-model.number="runtimeDraft.tts_volume" type="number" min="0" max="2" step="0.05" @input="markRuntimeDraftDirty" /></label>
                  <label><span>语速</span><input v-model.number="runtimeDraft.tts_speed_factor" type="number" min="0.5" max="2" step="0.05" @input="markRuntimeDraftDirty" /></label>
                  <label><span>模型温度</span><input v-model.number="runtimeDraft.llm_temperature" type="number" min="0" max="2" step="0.05" @input="markRuntimeDraftDirty" /></label>
                </div>
                <div class="runtime-actions">
                  <button type="button" :disabled="!connected || runtimePreviewLoading" :aria-busy="runtimePreviewLoading" @click="previewRuntimeSettings">{{ runtimePreviewLoading ? '检查中' : '检查更改' }}</button>
                  <button type="button" class="secondary" :disabled="!connected || runtimeApplyLoading || !runtimePreview?.ok || !runtimePreview?.changed" :aria-busy="runtimeApplyLoading" @click="applyRuntimeSettings">{{ runtimeApplyLoading ? '提交中' : '应用' }}</button>
                </div>
                <p class="runtime-preview-summary" v-if="runtimePreview">{{ runtimePreview.summary }}</p>
              </details>
            </div>

            <div class="runtime-settings skill-manifest-section" v-else-if="activeSettingsTab === 'skills'">
              <div class="runtime-settings-head">
                <div><strong>Skills</strong><span>可复用工作流；代码扩展必须作为 Adapter 安装。</span></div>
                <div class="memory-head-actions">
                  <button type="button" class="memory-link-button" :disabled="skillRefreshLoading" :aria-busy="skillRefreshLoading" @click="refreshSkills">
                    {{ skillRefreshLoading ? '刷新中' : '刷新' }}
                  </button>
                </div>
              </div>

              <section class="agent-skill-import">
                <header><div><strong>导入 Skill</strong><span>本地目录、ZIP 或 Git 仓库</span></div><ShieldCheck :size="20" /></header>
                <div class="agent-skill-source-row">
                  <input v-model="agentSkillSource" spellcheck="false" placeholder="粘贴路径或 Git URL" @keydown.enter.prevent="inspectAgentSkill" />
                  <button type="button" @click="chooseAgentSkillSource('folder')"><FolderPlus :size="15" />目录</button>
                  <button type="button" @click="chooseAgentSkillSource('file')"><FilePlus2 :size="15" />ZIP</button>
                  <button type="button" :disabled="!agentSkillSource.trim() || agentSkillBusy" :aria-busy="agentSkillBusy" @click="inspectAgentSkill">{{ agentSkillBusy ? '检查中' : '预览' }}</button>
                </div>
                <article v-if="agentSkillInspection" class="agent-skill-review">
                  <header>
                    <div><strong>{{ agentSkillInspection.name }}</strong><span>{{ agentSkillInspection.version }} · {{ agentSkillInspection.author || '作者未声明' }}</span></div>
                    <code>{{ agentSkillInspection.digest.slice(0, 20) }}…</code>
                  </header>
                  <p>{{ agentSkillInspection.description }}</p>
                  <div class="provider-meta">
                    <span>{{ agentSkillInspection.license || '无许可证' }}</span>
                    <span>{{ agentSkillInspection.scripts?.length || 0 }} 个脚本</span>
                    <span>{{ agentSkillInspection.references?.length || 0 }} 份参考</span>
                    <span>{{ agentSkillInspection.assets?.length || 0 }} 个资源</span>
                  </div>
                  <p v-for="warning in agentSkillInspection.warnings || []" :key="warning" class="agent-skill-warning">{{ warning }}</p>
                  <footer>
                    <label><span>可见范围</span><select v-model="agentSkillScope"><option value="project">当前项目</option><option value="character">当前角色</option><option value="global">全局</option></select></label>
                    <button type="button" :disabled="agentSkillBusy" :aria-busy="agentSkillBusy" @click="installInspectedAgentSkill"><Plus :size="15" />审核后安装</button>
                  </footer>
                </article>
                <p v-if="agentSkillNotice" class="agent-skill-notice">{{ agentSkillNotice }}</p>
              </section>

              <section class="agent-skill-drafts" v-if="agentSkillDrafts.length">
                <header><div><strong>待审阅草稿</strong><span>成功的操作只会写成草稿，安装前不会自动运行</span></div><FileClock :size="19" /></header>
                <article v-for="draft in agentSkillDrafts" :key="draft.id">
                  <div class="agent-skill-draft-head">
                    <strong>{{ draft.name }}</strong>
                    <span>{{ draft.payload?.description || '来自一次成功的操作' }}</span>
                  </div>
                  <ol v-if="agentSkillDraftSteps(draft).length" class="agent-skill-draft-steps">
                    <li v-for="(step, index) in agentSkillDraftSteps(draft)" :key="index">{{ step }}</li>
                  </ol>
                  <p v-else class="memory-empty">这份草稿还没有记录可复用的步骤。</p>
                  <footer>
                    <button type="button" :disabled="agentSkillBusy" :aria-busy="agentSkillBusy" @click="approveAgentSkillDraft(draft)"><Plus :size="15" />审核后安装</button>
                    <button type="button" class="danger" @click="rejectAgentSkillDraft(draft)"><Trash2 :size="14" />丢弃</button>
                  </footer>
                </article>
              </section>

              <section class="agent-skill-installations">
                <header><strong>已安装</strong><span>{{ installedAgentSkills.length }}</span></header>
                <div v-if="installedAgentSkills.length" class="agent-skill-list">
                  <article v-for="skill in installedAgentSkills" :key="skill.id" :class="{ disabled: !skill.enabled }">
                    <div class="agent-skill-mark"><Sparkles :size="17" /></div>
                    <div><strong>{{ skill.name }}</strong><span>{{ agentSkillScopeLabel(skill.scope) }} · {{ skill.version }}<template v-if="skill.manifest?.code_bearing"> · 显式脚本</template></span></div>
                    <button type="button" @click="toggleAgentSkill(skill)">{{ skill.enabled ? '停用' : '启用' }}</button>
                    <button type="button" :disabled="agentSkillBusy" :aria-busy="agentSkillBusy" @click="updateAgentSkill(skill)"><RefreshCw :size="14" /></button>
                    <button type="button" class="danger" @click="uninstallAgentSkill(skill)"><Trash2 :size="14" /></button>
                  </article>
                </div>
                <p v-else class="memory-empty">还没有安装第三方 Skill。Joi 的原生工具仍可正常使用。</p>
              </section>

              <section class="game-adapter-settings">
                <header><div><strong>游戏适配器</strong><span>独立代码扩展，不会伪装成 Skill</span></div><Gamepad2 :size="19" /></header>
                <div v-if="minecraftAdapter?.installed && minecraftAdapter.enabled" class="minecraft-setup-card">
                  <header>
                    <div><strong>Minecraft Skill</strong><span>HMCL / Java 版 · Mineflayer V2 Bridge</span></div>
                    <em :class="{ ready: activeMinecraftSessionId }">{{ activeMinecraftSessionId ? '已连接' : '待连接' }}</em>
                  </header>
                  <div class="minecraft-mode-picker" role="group" aria-label="Minecraft 游玩模式">
                    <button type="button" :class="{ active: minecraftMode === 'companion' }" :aria-pressed="minecraftMode === 'companion'" :disabled="Boolean(activeMinecraftSessionId)" @click="minecraftMode = 'companion'">与 Joi 一起玩</button>
                    <button type="button" :class="{ active: minecraftMode === 'delegate' }" :aria-pressed="minecraftMode === 'delegate'" :disabled="Boolean(activeMinecraftSessionId)" @click="minecraftMode = 'delegate'">让 Joi 单独玩</button>
                  </div>
                  <details :open="!activeMinecraftSessionId">
                    <summary>1. HMCL 局域网连接</summary>
                    <div class="minecraft-field-grid">
                      <label><span>地址</span><input v-model.trim="minecraftConnectionDraft.host" spellcheck="false" placeholder="127.0.0.1" /></label>
                      <label><span>LAN 端口</span><input v-model.number="minecraftConnectionDraft.port" type="number" min="1" max="65535" placeholder="打开局域网后显示" /></label>
                      <label><span>Joi 玩家名</span><input v-model.trim="minecraftConnectionDraft.username" spellcheck="false" /></label>
                      <label><span>登录方式</span><select v-model="minecraftConnectionDraft.auth"><option value="offline">离线 / LAN</option><option value="microsoft">Microsoft</option></select></label>
                      <label><span>服务器标签</span><input v-model.trim="minecraftConnectionDraft.server_id" spellcheck="false" /></label>
                      <label><span>世界标签</span><input v-model.trim="minecraftConnectionDraft.world" spellcheck="false" /></label>
                      <label><span>MC 版本（可留空）</span><input v-model.trim="minecraftConnectionDraft.version" spellcheck="false" placeholder="自动检测" /></label>
                    </div>
                    <button type="button" :disabled="minecraftBusy || minecraftConnectionDraft.port < 1" :aria-busy="minecraftBusy" @click="saveMinecraftConnection">保存连接配置</button>
                  </details>
                  <details :open="!activeMinecraftSessionId">
                    <summary>2. 确认世界能力范围</summary>
                    <div class="minecraft-field-grid">
                      <label><span>维度</span><select v-model="minecraftScopeDraft.dimensions" multiple><option value="overworld">主世界</option><option value="the_nether">下界</option><option value="the_end">末地</option></select></label>
                      <label><span>起点最大半径</span><input v-model.number="minecraftScopeDraft.max_radius" type="number" min="4" max="128" /></label>
                      <label><span>最多动作</span><input v-model.number="minecraftScopeDraft.max_actions" type="number" min="1" max="200" /></label>
                      <label><span>最多修改方块</span><input v-model.number="minecraftScopeDraft.max_blocks_changed" type="number" min="0" max="512" /></label>
                      <label class="wide"><span>允许方块（逗号分隔）</span><input v-model="minecraftScopeDraft.allowed_blocks" spellcheck="false" /></label>
                      <label class="wide"><span>允许跟随的玩家（逗号分隔）</span><input v-model="minecraftScopeDraft.allowed_players" spellcheck="false" :placeholder="minecraftConnectionDraft.username === 'Joi' ? '填写你的 MC 玩家名' : ''" /></label>
                      <label class="check"><input v-model="minecraftScopeDraft.allow_build" type="checkbox" /><span>允许建造</span></label>
                      <label class="check"><input v-model="minecraftScopeDraft.allow_containers" type="checkbox" /><span>允许存入容器</span></label>
                    </div>
                  </details>
                  <div class="minecraft-session-actions">
                    <template v-if="activeMinecraftSessionId">
                      <button type="button" :disabled="!realtimeVoiceConfigured" @click="toggleRealtimeVoice(true)"><AudioLines :size="16" />{{ realtimeVoiceActive ? '结束实时语音' : '启动实时语音 + Minecraft' }}</button>
                      <button v-if="realtimeVoiceState === 'acting'" type="button" @click="controlRealtimeMinecraft('pause')"><Pause :size="15" />暂停当前动作</button>
                      <button v-if="realtimeVoiceState === 'paused'" type="button" @click="controlRealtimeMinecraft('resume')"><Play :size="15" />继续当前动作</button>
                      <button v-if="['acting', 'paused'].includes(realtimeVoiceState)" type="button" class="danger" @click="controlRealtimeMinecraft('cancel')"><X :size="15" />取消当前动作</button>
                      <button
                        type="button"
                        :class="{ primary: minecraftAutonomyEnabled }"
                        :disabled="minecraftBusy"
                        :aria-pressed="minecraftAutonomyEnabled"
                        @click="toggleMinecraftAutonomy"
                      ><Sparkles :size="15" />{{ minecraftAutonomyEnabled ? '关闭自主行为' : '让 Joi 主动一点' }}</button>
                      <button type="button" class="danger" :disabled="minecraftBusy" @click="stopMinecraftSession"><Unplug :size="15" />结束游戏会话</button>
                    </template>
                    <button v-else type="button" class="primary" :disabled="minecraftBusy || minecraftConnectionDraft.port < 1 || !minecraftScopeDraft.allowed_blocks.trim()" :aria-busy="minecraftBusy" @click="startMinecraftSession">
                      <Gamepad2 :size="16" />{{ minecraftBusy ? '连接中…' : '预览范围并连接' }}
                    </button>
                  </div>
                  <p class="minecraft-privacy-note"><ShieldCheck :size="15" />部分转写不会执行动作；断线、停止或 Core 失联会取消当前目标且绝不自动重放。坐标、背包明细和 Bridge 日志不会进入语音或公开回执。</p>
                </div>
                <div class="game-adapter-list">
                  <article v-for="adapter in gameAdapterRows" :key="adapter.id" :class="{ disabled: adapter.installed && !adapter.enabled }">
                    <div>
                      <strong>{{ adapter.name }}</strong>
                      <span>{{ adapter.platforms.join(' / ') }} · {{ adapter.modes.map((mode) => mode === 'companion' ? '独立伙伴' : '角色接管').join(' / ') }}</span>
                    </div>
                    <em :class="{ ready: adapter.detection_status?.status === 'ready' }">{{ adapter.detection_status?.status === 'ready' ? '环境就绪' : '需要配置' }}</em>
                    <template v-if="adapter.installed">
                      <button type="button" @click="inspectGameAdapterRun(adapter)">检查</button>
                      <button type="button" @click="toggleGameAdapter(adapter)">{{ adapter.enabled ? '停用' : '启用' }}</button>
                      <button type="button" class="danger" @click="uninstallGameAdapter(adapter)"><Trash2 :size="14" /></button>
                    </template>
                    <button v-else type="button" class="primary" @click="installGameAdapter(adapter)">查看并安装</button>
                  </article>
                </div>
                <p v-if="gameAdapterNotice" class="agent-skill-notice">{{ gameAdapterNotice }}</p>
              </section>

              <details class="agent-native-skills">
                <summary>Joi 原生工具 <span>{{ nativeSkills().length }} · {{ skillManifestVersion() }}</span></summary>
                <div class="skill-grid" v-if="nativeSkills().length">
                  <article v-for="skill in nativeSkills()" :key="skill.id" class="skill-card" :class="skill.local_capability || 'unavailable'">
                  <header>
                    <strong>{{ skill.label || skill.id }}</strong>
                    <span>{{ skillCapabilityLabel(skill.local_capability) }}</span>
                  </header>
                  <div class="provider-meta" v-if="skillMeta(skill).length">
                    <span v-for="item in skillMeta(skill)" :key="`${skill.id}-${item}`">{{ item }}</span>
                  </div>
                  <div class="skill-tool-list" v-if="skillTools(skill).length">
                    <code v-for="tool in skillTools(skill)" :key="`${skill.id}-${tool}`">{{ tool }}</code>
                  </div>
                  <div class="skill-actions">
                    <button type="button" :disabled="skillToggleDisabled(skill)" @click="setSkillEnabled(skill, !skillEnabled(skill))">
                      {{ skillActionLabel(skill) }}
                    </button>
                  </div>
                  </article>
                </div>
              </details>
            </div>

            <div class="runtime-settings memory-settings" v-else-if="activeSettingsTab === 'memory'">
              <div class="runtime-settings-head">
                <div><strong>长期记忆</strong><span>仅保存你确认过的内容</span></div>
                <label class="memory-switch">
                  <input type="checkbox" :checked="memoryEnabled" @change="toggleMemoryEnabled" />
                  <span aria-hidden="true"></span>
                  <em>{{ memoryEnabled ? '开启' : '关闭' }}</em>
                </label>
              </div>
              <div class="settings-memory-overview">
                <div><strong>{{ memorySavedCount }}</strong><span>已保存</span></div>
                <div><strong>{{ memoryPendingCount }}</strong><span>待确认</span></div>
                <div><strong>{{ memoryStatus?.counts?.manual_notes || 0 }}</strong><span>手动笔记</span></div>
              </div>
              <p class="settings-memory-summary">{{ memoryProfile?.summary || 'Joi 会从对话中提出可记忆内容，只有确认后才会进入长期记忆。' }}</p>
              <button type="button" class="settings-memory-open" @click="openCabin('memory')"><Brain :size="18" />打开记忆管理</button>
            </div>

            <div class="runtime-settings language-settings" v-else-if="activeSettingsTab === 'language'">
              <div class="runtime-settings-head">
                <strong>界面语言</strong>
                <span>Joi 自己的按钮和文案</span>
              </div>
              <div class="language-options" role="group" aria-label="界面语言">
                <button type="button" class="language-option active" aria-pressed="true">中文</button>
                <button type="button" class="language-option" disabled>English</button>
                <button type="button" class="language-option" disabled>日本語</button>
              </div>
              <p class="language-hint">目前只有中文界面。其他语言还没有本地化，选了也只会显示中文，所以先不开放。</p>

              <div class="runtime-settings-head">
                <strong>聊天语言</strong>
                <span>Joi 在屏幕上写字用的语言</span>
              </div>
              <div class="language-options" role="group" aria-label="聊天语言">
                <button
                  v-for="code in chatLanguageChoices"
                  :key="code"
                  type="button"
                  class="language-option"
                  :class="{ active: currentChatLanguage === code }"
                  :aria-pressed="currentChatLanguage === code"
                  :disabled="languageBusy"
                  @click="saveChatLanguage(code)"
                >{{ languageLabel(code) }}</button>
              </div>
              <p class="language-hint">
                {{ currentChatLanguage === 'follow'
                  ? 'Joi 会用你这句话所用的语言回复。'
                  : `不管你用什么语言提问，Joi 都用${languageLabel(currentChatLanguage)}回复。` }}
              </p>

              <div class="runtime-settings-head">
                <strong>说话语言</strong>
                <span>来自角色包</span>
              </div>
              <div class="language-voice-row">
                <strong>{{ voiceLanguageLabel }}</strong>
                <button type="button" class="memory-link-button" @click="openCabin('characters')">在角色库里切换</button>
              </div>
              <p class="language-hint">
                角色包里选的语言只决定 Joi 用什么语言发声。她可以用{{ voiceLanguageLabel }}说话，同时在屏幕上写{{ currentChatLanguage === 'follow' ? '你所用的语言' : languageLabel(currentChatLanguage) }}。
              </p>
              <p v-if="languageNotice" class="agent-skill-notice">{{ languageNotice }}</p>
            </div>

            <div class="runtime-settings" v-else-if="activeSettingsTab === 'appearance'">
              <div class="runtime-settings-head">
                <strong>个性化装扮</strong>
                <span>点击进行穿戴</span>
              </div>
              <div class="closet-grid">
                <button type="button" class="accessory-card" :class="{ equipped: equippedAccessories.hat }" @click="toggleAccessory('hat')">巫师帽</button>
                <button type="button" class="accessory-card" :class="{ equipped: equippedAccessories.glasses }" @click="toggleAccessory('glasses')">墨镜</button>
                <button type="button" class="accessory-card" :class="{ equipped: equippedAccessories.ears }" @click="toggleAccessory('ears')">兔耳</button>
              </div>
            </div>

            <template v-else-if="activeSettingsTab === 'developer'">
              <div class="runtime-settings background-context-panel">
                <div class="runtime-settings-head">
                  <strong>背景上下文</strong>
                  <div class="memory-head-actions">
                    <label class="memory-enable-toggle">
                      <input type="checkbox" :checked="backgroundEnabled" :disabled="backgroundLoading" :aria-busy="backgroundLoading" @change="toggleBackgroundEnabled" />
                      <span>{{ backgroundEnabled ? '已开启' : '已关闭' }}</span>
                    </label>
                    <button type="button" class="memory-link-button" :disabled="backgroundLoading" :aria-busy="backgroundLoading" @click="refreshBackgroundStatus">{{ backgroundLoading ? '同步中' : '刷新' }}</button>
                  </div>
                </div>
                <div class="background-status-grid">
                  <article class="background-status-card" :class="{ active: backgroundActive }">
                    <span>状态</span>
                    <strong>{{ backgroundStateText }}</strong>
                    <small>{{ backgroundSummaryText }}</small>
                  </article>
                  <article class="background-status-card">
                    <span>摘要</span>
                    <strong>{{ backgroundStatus?.recent_count ?? backgroundRecentRows.length }}</strong>
                    <small>{{ backgroundRetentionText }}</small>
                  </article>
                </div>
              </div>
              <div class="debug-list">
                <div v-for="event in events.slice(-12).reverse()" :key="`${event.task_id}-${event.created_at}`" class="debug-row">
                  <span>{{ eventTime(event) }}</span>
                  <strong>{{ event.type }}</strong>
                  <code>{{ skillName(event) || toolName(event) || intentName(event) || event.display_card.status }}</code>
                  <p>{{ event.display_card.summary }}</p>
                </div>
              </div>
            </template>

            <!--
              No "coming soon" fallback. The chain above now covers every tab
              the navigation can reach, so this branch was unreachable -- it
              only ever appeared for the twelve categories that were declared
              but never routed to.
            -->
          </section>
        </div>
      </section>
    </section>

    <aside class="stage" :class="{ 'backdrop-enabled': stageBackdropEnabled, 'full-body': characterFullBody }" :style="stageBackdropStyle" v-if="activeCabin !== 'inspector'">
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
        :style="stageCharacterStyle"
        :title="isCompactMode ? '拖拽移动，单击输入，双击恢复主界面' : undefined"
        @mousedown="startMascotDrag"
        @dragstart.capture.prevent
        @selectstart.prevent
        @click.stop="handleMascotClick"
        @dblclick.stop.prevent="handleMascotDoubleClick"
      >
        <JoiCharacter
          :key="characterRenderKey"
          :model-url="live2DModelUrl"
          :model-type="characterDisplayModelType"
          :runtime-mapping="live2DRuntimeMapping"
          :fallback-image-src="characterImageSrc"
          :character-name="characterName"
          :emotion="activeExpressionEmotion"
          :motion="activeCharacterMotion"
          :compact="isCompactMode || characterFullBody"
          :zoom="stageZoom"
          :accessory-style="accessoryFitStyle"
          :accessories="equippedAccessories"
        />
        <div class="character-shadow"></div>
      </div>

      <div class="luna-stage-controls" v-if="!isCompactMode">
        <button type="button" :class="{ active: stageBackdropEnabled }" @click="stageBackdropEnabled = !stageBackdropEnabled">
          <ImageIcon :size="18" :stroke-width="1.75" />
          <span>背景</span>
        </button>
        <button type="button" @click="cycleStageZoom" :title="`当前缩放 ${stageZoomLabel}`">
          <Search :size="18" :stroke-width="1.75" />
          <span>缩放</span>
        </button>
        <button type="button" :class="{ active: characterFullBody }" @click="characterFullBody = !characterFullBody">
          <Maximize2 :size="18" :stroke-width="1.75" />
          <span>全身</span>
        </button>
        <button type="button" @click="stageCollapsed = true" title="收起角色，让对话铺满窗口">
          <PanelLeftClose :size="18" :stroke-width="1.75" />
          <span>收起</span>
        </button>
      </div>
      <!--
        The collapsed rail. Everything else on the stage is hidden by CSS; this
        stays so the stage never becomes a strip with no way out of it, and so
        the character is still present as a small avatar rather than gone.
      -->
      <div class="stage-rail" v-if="!isCompactMode && stageIsCollapsed">
        <span class="stage-rail-avatar" aria-hidden="true">
          <img v-if="characterAvatarSrc" :src="characterAvatarSrc" alt="" />
          <Sparkles v-else :size="18" />
        </span>
        <button
          type="button"
          class="stage-rail-expand"
          :disabled="!stageHasCharacter"
          :title="stageHasCharacter ? '展开角色舞台' : '当前没有可显示的角色'"
          @click="stageCollapsed = false"
        >
          <PanelLeftOpen :size="18" :stroke-width="1.75" />
          <span class="sr-only">展开角色舞台</span>
        </button>
        <!--
          A pending memory candidate is the user's decision to make (PRD 4.2),
          so collapsing the stage must not be the reason they never see it. The
          full prompt does not fit a 76px rail; this keeps the signal and the
          route to it, and expands the stage on click so the actual card is
          readable before anything is confirmed.
        -->
        <!--
          A pending memory candidate is the user's decision to make (PRD 4.2),
          so collapsing the stage must not be the reason they never see it. The
          full prompt does not fit a 76px rail; this keeps the signal and the
          route to it, and expands the stage on click so the actual card is
          readable before anything is confirmed.
        -->
        <button
          v-if="topPendingMemory"
          type="button"
          class="stage-rail-pending"
          :title="`有待确认的记忆：${memoryAuthorizeText}`"
          @click="stageCollapsed = false"
        >
          <Brain :size="16" :stroke-width="1.75" />
          <span class="sr-only">有待确认的记忆，展开查看</span>
        </button>
        <span class="stage-rail-dot" :class="{ online: connected }" :title="connected ? '在线' : '离线'"></span>
      </div>

      <div class="luna-stage-online" v-if="!isCompactMode">
        <span :class="{ online: connected }"></span>
        {{ connected ? '在线' : '离线' }}
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
            <span>{{ connected ? 'Joi online' : 'Joi offline' }}</span>
          </div>
          <button type="button" class="mini-restore-btn" :disabled="compactTransitioning" title="恢复主界面" @click="toggleCompactMode">还原</button>
        </div>
        <form class="mini-composer" @submit.prevent="submit">
          <button
            type="button"
            class="mini-mic-btn"
            :class="{ recording: voiceState === 'recording' }"
            :disabled="!connected || !asrConfigured || voiceState === 'transcribing' || realtimeVoiceActive"
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
          <button type="button" class="dock-btn" :class="{ active: activeCabin === 'memory' }" @click="activeCabin = 'memory'">
            <svg viewBox="0 0 24 24">
              <path d="M12 3c-2.76 0-5 1.9-5 4.25 0 .55.13 1.08.36 1.56C5.91 9.43 5 10.72 5 12.25c0 1.76 1.22 3.24 2.88 3.67C8.42 17.71 10.05 19 12 19s3.58-1.29 4.12-3.08C17.78 15.49 19 14.01 19 12.25c0-1.53-.91-2.82-2.36-3.44.23-.48.36-1.01.36-1.56C17 4.9 14.76 3 12 3zm-2.5 7.75a1.25 1.25 0 110-2.5 1.25 1.25 0 010 2.5zm5 0a1.25 1.25 0 110-2.5 1.25 1.25 0 010 2.5zM12 16.5c-1.4 0-2.55-.83-2.9-2h5.8c-.35 1.17-1.5 2-2.9 2z"/>
            </svg>
            <span>记忆舱</span>
          </button>
          <button type="button" class="dock-btn" @click="openCabin('inspector')">
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
    <dialog ref="appConfirmDialog" class="app-confirm-modal" @cancel.prevent="resolveAppConfirm(false)">
      <section v-if="appConfirmRequest" class="app-confirm-card" role="document">
        <header>
          <ShieldCheck :size="22" />
          <h2>{{ appConfirmRequest.title }}</h2>
        </header>
        <p>{{ appConfirmRequest.message }}</p>
        <footer>
          <button type="button" class="secondary" @click="resolveAppConfirm(false)">{{ appConfirmRequest.cancelLabel }}</button>
          <button type="button" :class="{ danger: appConfirmRequest.danger }" @click="resolveAppConfirm(true)">{{ appConfirmRequest.confirmLabel }}</button>
        </footer>
      </section>
    </dialog>
  </main>
</template>
