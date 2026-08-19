<script setup lang="ts">
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import {
  ArrowLeft,
  BadgeCheck,
  Box,
  Check,
  ChevronRight,
  CirclePlus,
  Copy,
  Download,
  FileArchive,
  FileCheck2,
  Image as ImageIcon,
  LoaderCircle,
  MessageCircle,
  PackageCheck,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import type { CoreClient } from '../api'
import type {
  CharacterDetail,
  CharacterInspectResult,
  CharacterListResult,
  CharacterManifest,
  CharacterMutationResult,
  CharacterSummary,
  CoreReadyPayload,
} from '../protocol'
import { STAGE_FORMAT_LABEL, isStageModelFormat, type StageModelFormat } from '../character/stage'

const props = defineProps<{ client: CoreClient; connected: boolean }>()
const emit = defineEmits<{
  activated: [characterId: string, ready?: CoreReadyPayload]
  close: []
}>()

type LibraryView = 'library' | 'editor' | 'import-preview'
type EditorMode = 'create' | 'edit'

interface ExpressionDraft {
  id: string
  label: string
  emotion: string
  image: string
  image_path: string
  expression_id: string
  motion_group: string
  motion_index: number
}

interface CharacterDraft {
  id: string
  name: string
  version: string
  creator: string
  license: string
  persona: string
  personality: string
  scenario: string
  tone: string
  boundaries: string
  greeting: string
  alternateGreetings: string
  exampleDialogue: string
  systemPrompt: string
  postHistoryInstructions: string
  modelType: StageModelFormat
  avatarPath: string
  portraitPath: string
  modelPath: string
  backgroundPath: string
  accentColor: string
  expressions: ExpressionDraft[]
  voiceId: string
  voiceLabel: string
  voiceLanguage: string
  voiceSpeed: number
  voiceVolume: number
  referenceAudioPath: string
  voicePromptText: string
  voicePromptLanguage: string
  voiceDesign: string
  gptModelPath: string
  sovitsModelPath: string
  lorebook: string
  memoryNamespace: 'isolated' | 'shared' | 'disabled'
  requestedSkills: string
  sourceUrl: string
  updateUrl: string
}

interface UninstallConfirmation {
  characterId: string
  characterName: string
  wasActive: boolean
  fallbackId: string
  fallbackName: string
}

const view = ref<LibraryView>('library')
const editorMode = ref<EditorMode>('create')
const characters = ref<CharacterSummary[]>([])
const activeId = ref('')
const selectedId = ref('')
const selected = ref<CharacterDetail | null>(null)
const loading = ref(false)
const actionBusy = ref('')
// Off by default: a character does not get the previous one's conversation
// unless the user hands it over.
const inheritConversation = ref(false)
const libraryRoot = ref<HTMLElement | null>(null)
const uninstallConfirmPanel = ref<HTMLElement | null>(null)
const notice = ref('')
const error = ref('')
const importPreview = ref<CharacterInspectResult | null>(null)
const importSource = ref('')
const uninstallConfirmation = ref<UninstallConfirmation | null>(null)
const draft = reactive<CharacterDraft>(emptyDraft())

watch(view, async () => {
  await nextTick()
  libraryRoot.value?.closest<HTMLElement>('.workspace')?.scrollTo({ top: 0 })
})

const activeCharacter = computed(() => characters.value.find((row) => row.id === activeId.value) || null)
const selectedSummary = computed(() => characters.value.find((row) => row.id === selectedId.value) || activeCharacter.value)
const importAssetReport = computed(() => importPreview.value?.security?.asset_report)

function emptyDraft(): CharacterDraft {
  return {
    id: '',
    name: '',
    version: '1.0.0',
    creator: '',
    license: 'All rights reserved',
    persona: '',
    personality: '',
    scenario: '',
    tone: '自然、简短、有自己的判断',
    boundaries: '不隐瞒风险\n高风险动作必须先确认',
    greeting: '',
    alternateGreetings: '',
    exampleDialogue: '',
    systemPrompt: '',
    postHistoryInstructions: '',
    modelType: 'static',
    avatarPath: '',
    portraitPath: '',
    modelPath: '',
    backgroundPath: '',
    accentColor: '#5b7ff5',
    expressions: [],
    voiceId: 'default',
    voiceLabel: '默认音色',
    voicePromptLanguage: '',
    voiceDesign: '',
    gptModelPath: '',
    sovitsModelPath: '',
    voiceLanguage: 'zh',
    voiceSpeed: 1,
    voiceVolume: 1,
    referenceAudioPath: '',
    voicePromptText: '',
    lorebook: '',
    memoryNamespace: 'isolated',
    requestedSkills: '',
    sourceUrl: '',
    updateUrl: '',
  }
}

function resetDraft(next: CharacterDraft = emptyDraft()) {
  Object.assign(draft, next)
}

function avatarSource(row: CharacterSummary | CharacterDetail | null | undefined) {
  if (!row) return ''
  if (row.avatar_url) return row.avatar_url
  if (row.avatar_data_url) return row.avatar_data_url
  if (row.avatar_path) return convertFileSrc(row.avatar_path)
  return portraitSource(row)
}

function portraitSource(row: CharacterSummary | CharacterDetail | null | undefined) {
  if (!row) return ''
  if (row.portrait_url) return row.portrait_url
  if (row.portrait_data_url) return row.portrait_data_url
  return row.portrait_path ? convertFileSrc(row.portrait_path) : ''
}

function modelLabel(type?: string) {
  return isStageModelFormat(type) ? STAGE_FORMAT_LABEL[type] : '静态立绘'
}

/**
 * Thumbnails rendered from the model, for packages that ship no artwork.
 *
 * Keyed by character id and rendered once per session. A package with an author
 * portrait already has a picture; this is for the ones that would otherwise show
 * a letter, which are exactly the ones a user is least sure about.
 */
const renderedThumbnails = ref<Record<string, string>>({})
const thumbnailAttempted = new Set<string>()

function cardArtSource(row: CharacterSummary | CharacterDetail | null | undefined) {
  if (!row) return ''
  return avatarSource(row) || renderedThumbnails.value[row.id] || ''
}

async function renderMissingThumbnails() {
  const { canRenderThumbnail } = await import('../character/stage')
  for (const row of characters.value) {
    if (avatarSource(row) || thumbnailAttempted.has(row.id)) continue
    const format = isStageModelFormat(row.model_type) ? row.model_type : 'static'
    const modelUrl = row.model_path ? convertFileSrc(row.model_path) : ''
    if (!canRenderThumbnail(format, modelUrl)) continue
    thumbnailAttempted.add(row.id)
    // One at a time: each renderer holds a WebGL context, and a library of
    // eight characters mounting at once exhausts the browser's supply.
    const { renderModelThumbnail } = await import('../character/thumbnail')
    const image = await renderModelThumbnail(format, modelUrl, {}, { size: 160 })
    if (image) renderedThumbnails.value = { ...renderedThumbnails.value, [row.id]: image }
  }
}

function memoryLabel(namespace?: string) {
  if (namespace === 'shared') return '共享记忆'
  if (namespace === 'disabled') return '不使用长期记忆'
  return '独立记忆'
}

function rpcMessage(result: { message?: string; error?: string } | null | undefined, fallback: string) {
  return result?.message || result?.error || fallback
}

async function refresh(preferredId = '') {
  if (!props.connected) return
  loading.value = true
  error.value = ''
  try {
    const result = await props.client.characterList() as CharacterListResult
    if (!result.ok) throw new Error(rpcMessage(result, '角色库读取失败。'))
    characters.value = result.characters || []
    activeId.value = result.active_id || ''
    const nextId = preferredId || selectedId.value || activeId.value || characters.value[0]?.id || ''
    if (nextId) await selectCharacter(nextId)
    // After the list is on screen, not before: a thumbnail is worth waiting for
    // but never worth making the library wait.
    void renderMissingThumbnails()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色库读取失败。'
  } finally {
    loading.value = false
  }
}

async function selectCharacter(characterId: string) {
  if (uninstallConfirmation.value?.characterId !== characterId) uninstallConfirmation.value = null
  selectedId.value = characterId
  selected.value = null
  if (!props.connected || !characterId) return
  try {
    const result = await props.client.characterDetail(characterId) as { ok?: boolean; character?: CharacterDetail; message?: string; error?: string }
    if (!result.ok || !result.character) throw new Error(rpcMessage(result, '角色详情读取失败。'))
    selected.value = result.character
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色详情读取失败。'
  }
}

function openCreate() {
  editorMode.value = 'create'
  resetDraft()
  view.value = 'editor'
  notice.value = ''
  error.value = ''
}

function openEdit() {
  const manifest = selected.value?.manifest
  if (!manifest || selected.value?.built_in) return
  editorMode.value = 'edit'
  resetDraft(draftFromManifest(manifest))
  view.value = 'editor'
  notice.value = ''
  error.value = ''
}

async function editSelected() {
  if (!selected.value) return
  if (!selected.value.built_in) {
    openEdit()
    return
  }
  await duplicateSelected(true)
}

function draftFromManifest(manifest: CharacterManifest): CharacterDraft {
  const identity = manifest.identity || { name: '' }
  const appearance = manifest.appearance || {}
  const voice = manifest.voice || {}
  const lorebook = manifest.knowledge?.lorebook
  const loreRows = (lorebook?.entries || []).map((entry) => {
    const keys = Array.isArray(entry.keys) ? entry.keys.join(',') : ''
    return `${keys} | ${String(entry.content || '')}`.trim()
  })
  return {
    ...emptyDraft(),
    id: manifest.id,
    name: identity.name || '',
    version: manifest.version || '1.0.0',
    creator: manifest.creator?.name || '',
    license: manifest.security?.license || 'Unknown',
    persona: identity.persona || '',
    personality: identity.personality || '',
    scenario: identity.scenario || '',
    tone: identity.tone || '',
    boundaries: (identity.boundaries || []).join('\n'),
    greeting: identity.greeting || '',
    alternateGreetings: (identity.alternate_greetings || []).join('\n'),
    exampleDialogue: identity.example_dialogue || '',
    systemPrompt: identity.system_prompt || '',
    postHistoryInstructions: identity.post_history_instructions || '',
    modelType: appearance.model_type || 'static',
    accentColor: appearance.accent_color || '#5b7ff5',
    expressions: (appearance.expressions || []).map((entry, index) => ({
      id: String(entry.id || index + 1),
      label: String(entry.label || entry.emotion || '表情'),
      emotion: String(entry.emotion || 'neutral'),
      image: String(entry.image || ''),
      image_path: '',
      expression_id: String(entry.expression_id || ''),
      motion_group: String(entry.motion_group || ''),
      motion_index: Number(entry.motion_index || 0),
    })),
    voiceId: voice.id || 'default',
    voiceLabel: voice.label || '默认音色',
    voicePromptLanguage: voice.prompt_language || '',
    voiceDesign: voice.design || '',
    gptModelPath: voice.gpt_model || '',
    sovitsModelPath: voice.sovits_model || '',
    voiceLanguage: voice.language || 'zh',
    voiceSpeed: Number(voice.speed || 1),
    voiceVolume: Number(voice.volume || 1),
    voicePromptText: voice.prompt_text || '',
    lorebook: loreRows.join('\n'),
    memoryNamespace: manifest.knowledge?.memory_namespace || 'isolated',
    requestedSkills: (manifest.capabilities?.requested_skills || []).join(', '),
    sourceUrl: manifest.source?.url || '',
    updateUrl: manifest.source?.update_url || '',
  }
}

async function pickAsset(target: 'avatarPath' | 'portraitPath' | 'modelPath' | 'backgroundPath' | 'referenceAudioPath' | 'gptModelPath' | 'sovitsModelPath') {
  try {
    const paths = await invoke<string[]>('pick_attachments', { kind: 'file' })
    if (paths[0]) draft[target] = paths[0]
  } catch {
    error.value = '没有打开文件选择器。'
  }
}

async function pickExpressionAsset(index: number) {
  try {
    const paths = await invoke<string[]>('pick_attachments', { kind: 'file' })
    if (paths[0]) draft.expressions[index].image_path = paths[0]
  } catch {
    error.value = '没有打开文件选择器。'
  }
}

function addExpression() {
  draft.expressions.push({ id: String(draft.expressions.length + 1), label: '新表情', emotion: 'neutral', image: '', image_path: '', expression_id: '', motion_group: '', motion_index: 0 })
}

async function saveCharacter() {
  if (!draft.name.trim()) {
    error.value = '请先填写角色名称。'
    return
  }
  actionBusy.value = 'save'
  error.value = ''
  notice.value = ''
  try {
    const payload = buildPayload()
    const result = editorMode.value === 'create'
      ? await props.client.characterCreate(payload) as CharacterMutationResult
      : await props.client.characterUpdate(draft.id, payload) as CharacterMutationResult
    if (!result.ok || !result.character) throw new Error(rpcMessage(result, '角色保存失败。'))
    notice.value = editorMode.value === 'create' ? '角色包已创建，可以立即切换使用。' : '角色包已更新。'
    view.value = 'library'
    await refresh(result.character.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色保存失败。'
  } finally {
    actionBusy.value = ''
  }
}

function buildPayload(): Record<string, unknown> {
  const loreEntries = draft.lorebook.split('\n').map((line, index) => {
    const divider = line.indexOf('|')
    if (divider < 0) return { id: index + 1, name: `条目 ${index + 1}`, keys: [], content: line.trim(), enabled: true }
    return {
      id: index + 1,
      name: line.slice(0, divider).trim() || `条目 ${index + 1}`,
      keys: line.slice(0, divider).split(',').map((item) => item.trim()).filter(Boolean),
      content: line.slice(divider + 1).trim(),
      enabled: true,
    }
  }).filter((entry) => entry.content)
  return {
    id: draft.id || undefined,
    version: draft.version,
    identity: {
      name: draft.name,
      avatar_path: draft.avatarPath,
      persona: draft.persona,
      personality: draft.personality,
      scenario: draft.scenario,
      tone: draft.tone,
      boundaries: draft.boundaries.split('\n').map((row) => row.trim()).filter(Boolean),
      greeting: draft.greeting,
      alternate_greetings: draft.alternateGreetings.split('\n').map((row) => row.trim()).filter(Boolean),
      example_dialogue: draft.exampleDialogue,
      system_prompt: draft.systemPrompt,
      post_history_instructions: draft.postHistoryInstructions,
    },
    appearance: {
      model_type: draft.modelType,
      portrait_path: draft.portraitPath,
      model_path: draft.modelPath,
      background_path: draft.backgroundPath,
      accent_color: draft.accentColor,
      expressions: draft.expressions.map((row) => ({ ...row })),
      lip_sync: { source: 'audio_amplitude', parameter: 'ParamMouthOpenY' },
    },
    voice: {
      id: draft.voiceId,
      label: draft.voiceLabel,
      language: draft.voiceLanguage,
      speed: draft.voiceSpeed,
      volume: draft.voiceVolume,
      reference_audio_path: draft.referenceAudioPath,
      prompt_text: draft.voicePromptText,
      prompt_language: draft.voicePromptLanguage,
      design: draft.voiceDesign,
      gpt_model_path: draft.gptModelPath,
      sovits_model_path: draft.sovitsModelPath,
    },
    knowledge: { lorebook: { entries: loreEntries }, memory_namespace: draft.memoryNamespace },
    capabilities: { requested_skills: draft.requestedSkills.split(',').map((row) => row.trim()).filter(Boolean), approved_skills: [] },
    creator: { name: draft.creator },
    source: { type: 'local', url: draft.sourceUrl, update_url: draft.updateUrl },
    security: { license: draft.license, compatibility: '>=0.1.0' },
  }
}

async function chooseImport() {
  error.value = ''
  notice.value = ''
  try {
    const paths = await invoke<string[]>('pick_attachments', { kind: 'file' })
    if (!paths[0]) return
    importSource.value = paths[0]
    actionBusy.value = 'inspect'
    const result = await props.client.characterInspect(paths[0]) as CharacterInspectResult
    if (!result.ok || !result.preview) throw new Error(rpcMessage(result, '角色包检查失败。'))
    importPreview.value = result
    view.value = 'import-preview'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色包检查失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function confirmImport() {
  if (!importSource.value) return
  actionBusy.value = 'import'
  error.value = ''
  try {
    const result = await props.client.characterImport(importSource.value) as CharacterMutationResult
    if (!result.ok || !result.installed) throw new Error(rpcMessage(result, '角色包导入失败。'))
    notice.value = `“${result.character?.name || '角色'}”已安全安装。`
    view.value = 'library'
    importPreview.value = null
    await refresh(result.installed)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色包导入失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function activateSelected() {
  if (!selectedId.value || selectedId.value === activeId.value) return
  actionBusy.value = 'activate'
  error.value = ''
  try {
    const inherited = inheritConversation.value
    const result = await props.client.characterActivate(selectedId.value, inherited) as CharacterMutationResult
    if (!result.ok) throw new Error(rpcMessage(result, '角色切换失败。'))
    activeId.value = selectedId.value
    notice.value = inherited
      ? `已切换到 ${selected.value?.name || '新角色'}，当前对话已交给它继续。`
      : `已切换到 ${selected.value?.name || '新角色'}，它有自己独立的对话和记忆空间。`
    inheritConversation.value = false
    emit('activated', selectedId.value, result.ready)
    await refresh(selectedId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色切换失败。'
  } finally {
    actionBusy.value = ''
  }
}

// Only worth showing for a character that actually has a second language --
// a picker with one option is a control that cannot do anything.
const localeChoices = computed(() => selectedSummary.value?.available_locales || [])
const currentLocale = computed(() => selectedSummary.value?.locale || localeChoices.value[0] || '')

const LOCALE_LABELS: Record<string, string> = { zh: '中文', ja: '日本語', en: 'English' }

function localeLabel(locale: string) {
  return LOCALE_LABELS[locale] || LOCALE_LABELS[locale.split('-')[0]] || locale
}

async function switchLocale(locale: string) {
  if (!selectedId.value || locale === currentLocale.value) return
  actionBusy.value = 'locale'
  error.value = ''
  try {
    const result = await props.client.characterSetLocale(selectedId.value, locale) as CharacterMutationResult
    if (!result.ok) throw new Error(rpcMessage(result, '语言切换失败。'))
    // The persona, greeting and voice all changed, so the character has to be
    // re-read rather than having a label swapped in place.
    notice.value = `已切换到${localeLabel(locale)}配音；屏幕上的文字仍按「设置 → 语言」里的聊天语言显示。`
    if (selectedId.value === activeId.value) emit('activated', selectedId.value, result.ready)
    await refresh(selectedId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '语言切换失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function duplicateSelected(openEditor = false) {
  if (!selectedId.value) return
  actionBusy.value = 'duplicate'
  error.value = ''
  try {
    const result = await props.client.characterDuplicate(selectedId.value) as CharacterMutationResult
    if (!result.ok || !result.character) throw new Error(rpcMessage(result, '角色复制失败。'))
    notice.value = '角色副本已创建，可以自由编辑。'
    await refresh(result.character.id)
    if (openEditor) openEdit()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色复制失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function exportSelected() {
  if (!selectedId.value) return
  try {
    const folders = await invoke<string[]>('pick_attachments', { kind: 'folder' })
    if (!folders[0]) return
    actionBusy.value = 'export'
    const result = await props.client.characterExport(selectedId.value, folders[0]) as CharacterMutationResult
    if (!result.ok) throw new Error(rpcMessage(result, '角色导出失败。'))
    notice.value = `角色包已导出到 ${result.path || folders[0]}`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色导出失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function checkUpdates() {
  if (!selectedId.value) return
  actionBusy.value = 'update'
  error.value = ''
  try {
    const result = await props.client.characterCheckUpdates(selectedId.value) as { ok?: boolean; available?: boolean; latest_version?: string; package_url?: string; status?: string; message?: string; error?: string }
    if (!result.ok) throw new Error(rpcMessage(result, '更新检查失败。'))
    if (!result.available) {
      notice.value = result.status === 'no_update_source' ? '这个角色没有配置更新源。' : '当前已经是最新版本。'
      return
    }
    if (!window.confirm(`发现新版本 ${result.latest_version}。现在下载、安全校验并更新吗？`)) {
      notice.value = `发现新版本 ${result.latest_version}，尚未安装。`
      return
    }
    const installed = await props.client.characterInstallUpdate(selectedId.value, result.package_url || '') as CharacterMutationResult & { updated?: boolean; version?: string }
    if (!installed.ok || !installed.updated) throw new Error(rpcMessage(installed, '角色更新安装失败。'))
    notice.value = `角色已更新到 ${installed.version || result.latest_version}。`
    await refresh(selectedId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '更新检查失败。'
  } finally {
    actionBusy.value = ''
  }
}

async function requestUninstallSelected() {
  if (!selected.value || selected.value.built_in) return
  const character = selected.value
  const wasActive = character.id === activeId.value
  const fallback = wasActive
    ? characters.value.find((row) => row.built_in && row.id !== character.id)
      || characters.value.find((row) => row.id !== character.id)
    : undefined
  if (wasActive && !fallback) {
    error.value = '当前角色正在使用，而且角色库中没有可切换的备用角色。'
    return
  }
  error.value = ''
  uninstallConfirmation.value = {
    characterId: character.id,
    characterName: character.name,
    wasActive,
    fallbackId: fallback?.id || '',
    fallbackName: fallback?.name || '',
  }
  await nextTick()
  uninstallConfirmPanel.value?.scrollIntoView({ block: 'nearest' })
}

async function confirmUninstall() {
  const pending = uninstallConfirmation.value
  if (!pending) return
  actionBusy.value = 'uninstall'
  error.value = ''
  try {
    const result = await props.client.characterUninstall(pending.characterId, pending.fallbackId) as CharacterMutationResult
    if (!result.ok) throw new Error(rpcMessage(result, '角色卸载失败。'))
    if (pending.wasActive && pending.fallbackId) {
      activeId.value = result.active_id || pending.fallbackId
      emit('activated', activeId.value, result.ready)
    }
    uninstallConfirmation.value = null
    notice.value = '角色包已卸载，个人运行数据没有随包删除。'
    selectedId.value = activeId.value
    await refresh(activeId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色卸载失败。'
  } finally {
    actionBusy.value = ''
  }
}

watch(() => props.connected, (connected) => {
  if (connected) void refresh()
})

onMounted(() => void refresh())
</script>

<template>
  <section ref="libraryRoot" class="character-library" aria-labelledby="character-library-title">
    <header class="library-header">
      <div>
        <div class="library-navigation">
          <button type="button" class="library-return" @click="emit('close')">
            <MessageCircle :size="16" /> 返回对话
          </button>
        </div>
        <button v-if="view !== 'library'" type="button" class="quiet-back" @click="view = 'library'">
          <ArrowLeft :size="17" /> 返回角色库
        </button>
        <p class="eyebrow">CHARACTER PACKS</p>
        <h1 id="character-library-title">{{ view === 'editor' ? (editorMode === 'create' ? '制作角色' : '编辑角色') : view === 'import-preview' ? '安装预览' : '角色库' }}</h1>
        <p>{{ view === 'library' ? '每个角色拥有自己的身份、外观、声音与记忆边界。' : view === 'import-preview' ? '确认来源、许可证与权限后再安装。' : '把人格与素材整理成可携带的 Joi 角色包。' }}</p>
      </div>
      <div class="header-actions" v-if="view === 'library'">
        <button type="button" class="secondary-action" :disabled="!connected || Boolean(actionBusy)" @click="chooseImport">
          <LoaderCircle v-if="actionBusy === 'inspect'" class="spin" :size="18" />
          <Upload v-else :size="18" />
          导入
        </button>
        <button type="button" class="primary-action" :disabled="!connected" @click="openCreate">
          <CirclePlus :size="18" /> 制作角色
        </button>
      </div>
    </header>

    <div v-if="notice" class="library-notice success" role="status"><Check :size="17" />{{ notice }}</div>
    <div v-if="error" class="library-notice error" role="alert"><X :size="17" />{{ error }}</div>

    <template v-if="view === 'library'">
      <div class="active-ribbon" v-if="activeCharacter">
        <div class="active-avatar">
          <img v-if="avatarSource(activeCharacter)" :src="avatarSource(activeCharacter)" alt="" />
          <Sparkles v-else :size="24" />
        </div>
        <div>
          <span>当前角色</span>
          <strong>{{ activeCharacter.name }}</strong>
          <small>{{ modelLabel(activeCharacter.model_type) }} · {{ memoryLabel(activeCharacter.memory_namespace) }}</small>
        </div>
        <BadgeCheck :size="22" aria-label="正在使用" />
      </div>

      <div class="library-layout">
        <div class="character-rail" aria-label="已安装角色">
          <button
            v-for="character in characters"
            :key="character.id"
            type="button"
            class="character-card"
            :class="{ selected: character.id === selectedId, active: character.id === activeId }"
            @click="selectCharacter(character.id)"
          >
            <span class="card-art" :style="{ '--character-accent': character.accent_color || '#5b7ff5' }">
              <img v-if="cardArtSource(character)" :src="cardArtSource(character)" :alt="`${character.name} 角色头像`" />
              <span v-else>{{ character.name.slice(0, 1) }}</span>
              <i v-if="character.active"><Check :size="13" /></i>
            </span>
            <span class="card-copy">
              <strong>{{ character.name }}</strong>
              <small>{{ modelLabel(character.model_type) }} · v{{ character.version }}</small>
            </span>
            <ChevronRight :size="17" />
          </button>
          <button type="button" class="character-card create-card" @click="openCreate">
            <span class="card-art"><Plus :size="22" /></span>
            <span class="card-copy"><strong>制作新角色</strong><small>从身份与素材开始</small></span>
          </button>
        </div>

        <article class="character-detail" v-if="selectedSummary">
          <div class="detail-copy">
            <div class="detail-meta" aria-label="角色包信息">
              <span><Box :size="14" />{{ modelLabel(selectedSummary.model_type) }}</span>
              <span><ShieldCheck :size="14" />{{ selectedSummary.license || 'Unknown' }}</span>
            </div>
            <div class="detail-title">
              <div><small>v{{ selectedSummary.version }}</small><h2>{{ selectedSummary.name }}</h2></div>
              <span v-if="selectedSummary.id === activeId"><Check :size="15" />使用中</span>
            </div>
            <p class="detail-tone">{{ selectedSummary.tone || selectedSummary.greeting || '这个角色还没有填写简介。' }}</p>
            <dl>
              <div><dt>作者</dt><dd>{{ selectedSummary.creator?.name || '未署名' }}</dd></div>
              <div><dt>记忆</dt><dd>{{ memoryLabel(selectedSummary.memory_namespace) }}</dd></div>
              <div><dt>权限</dt><dd>{{ selectedSummary.requested_skills?.length ? `${selectedSummary.requested_skills.length} 项待审查` : '不请求技能' }}</dd></div>
              <div><dt>完整性</dt><dd>{{ selectedSummary.package_hash ? '已校验' : '待校验' }}</dd></div>
            </dl>
            <div class="detail-primary-actions">
              <button v-if="selectedSummary.id !== activeId" type="button" class="primary-action wide" :disabled="Boolean(actionBusy)" @click="activateSelected">
                <LoaderCircle v-if="actionBusy === 'activate'" class="spin" :size="18" /><PackageCheck v-else :size="18" />切换到这个角色
              </button>
              <button v-else type="button" class="active-button" disabled><Check :size="18" />当前正在使用</button>
            </div>
            <label v-if="selectedSummary.id !== activeId" class="inherit-conversation">
              <input type="checkbox" v-model="inheritConversation" :disabled="Boolean(actionBusy)" />
              <span>把当前对话交给新角色</span>
              <small>默认每个角色的对话和上下文相互独立。</small>
            </label>
            <div v-if="localeChoices.length > 1" class="character-locale">
              <span class="character-locale-label">配音语言</span>
              <div class="character-locale-options" role="group" aria-label="角色配音语言">
                <button
                  v-for="locale in localeChoices"
                  :key="locale"
                  type="button"
                  :class="{ selected: locale === currentLocale }"
                  :aria-pressed="locale === currentLocale"
                  :disabled="Boolean(actionBusy)"
                  @click="switchLocale(locale)"
                >
                  <LoaderCircle v-if="actionBusy === 'locale' && locale !== currentLocale" class="spin" :size="14" />
                  {{ localeLabel(locale) }}
                </button>
              </div>
              <small>人设、开场白和音色描述都会换成这个语言。</small>
            </div>
            <div class="detail-tools">
              <button type="button" :disabled="!selected || Boolean(actionBusy)" @click="editSelected"><Pencil :size="16" />{{ selectedSummary.built_in ? '复制后编辑' : '编辑' }}</button>
              <button type="button" :disabled="Boolean(actionBusy)" @click="duplicateSelected()"><Copy :size="16" />复制</button>
              <button type="button" :disabled="Boolean(actionBusy)" @click="exportSelected"><Download :size="16" />导出</button>
              <button
                type="button"
                :disabled="Boolean(actionBusy) || !selectedSummary.has_update_source"
                :title="selectedSummary.has_update_source ? '检查角色包更新' : '这个角色没有配置更新源'"
                @click="checkUpdates"
              ><RefreshCw :size="16" />更新</button>
              <button
                type="button"
                class="danger"
                :disabled="selectedSummary.built_in || Boolean(actionBusy)"
                :title="selectedSummary.built_in ? '内置角色用于保证 Joi 始终可用，不能卸载' : selectedSummary.id === activeId ? '先切换到内置角色，再卸载当前角色' : '卸载角色包，保留聊天与独立记忆'"
                @click="requestUninstallSelected"
              ><Trash2 :size="16" />{{ selectedSummary.built_in ? '内置角色保留' : selectedSummary.id === activeId ? '切换并卸载' : '卸载' }}</button>
            </div>
            <Transition name="uninstall-confirm">
              <section
                v-if="uninstallConfirmation"
                ref="uninstallConfirmPanel"
                class="uninstall-confirmation"
                role="alertdialog"
                aria-modal="false"
                aria-labelledby="uninstall-confirm-title"
                aria-describedby="uninstall-confirm-description"
              >
                <span class="uninstall-confirm-icon"><Trash2 :size="18" /></span>
                <div>
                  <strong id="uninstall-confirm-title">卸载“{{ uninstallConfirmation.characterName }}”？</strong>
                  <p id="uninstall-confirm-description">
                    <template v-if="uninstallConfirmation.wasActive">Joi 会先切换到“{{ uninstallConfirmation.fallbackName }}”。</template>
                    角色包会移除，聊天与独立记忆仍会保留。
                  </p>
                </div>
                <div class="uninstall-confirm-actions">
                  <button type="button" :disabled="Boolean(actionBusy)" @click="uninstallConfirmation = null">取消</button>
                  <button type="button" class="danger-confirm" :disabled="Boolean(actionBusy)" @click="confirmUninstall">
                    <LoaderCircle v-if="actionBusy === 'uninstall'" class="spin" :size="15" />
                    <Trash2 v-else :size="15" />确认卸载
                  </button>
                </div>
              </section>
            </Transition>
          </div>
        </article>
        <div v-else-if="loading" class="detail-loading"><LoaderCircle class="spin" :size="24" />正在读取角色库…</div>
      </div>
    </template>

    <template v-else-if="view === 'import-preview' && importPreview?.preview">
      <div class="install-preview">
        <article class="preview-character">
          <div class="preview-art" :style="{ '--character-accent': importPreview.preview.accent_color || '#5b7ff5' }">
            <img v-if="portraitSource(importPreview.preview)" :src="portraitSource(importPreview.preview)" alt="角色包预览" />
            <FileArchive v-else :size="54" />
          </div>
          <div>
            <span class="preview-kicker">准备安装</span>
            <h2>{{ importPreview.preview.name }}</h2>
            <p>{{ importPreview.preview.tone || importPreview.preview.greeting || '已读取角色身份信息。' }}</p>
            <div class="preview-meta">
              <span>{{ modelLabel(importPreview.preview.model_type) }}</span>
              <span>v{{ importPreview.preview.version }}</span>
              <span>{{ memoryLabel(importPreview.preview.memory_namespace) }}</span>
            </div>
          </div>
        </article>
        <section class="security-review">
          <header><ShieldCheck :size="21" /><div><h3>安装前安全审查</h3><p>角色包只包含声明式数据，不允许执行代码。</p></div></header>
          <div class="security-grid">
            <div><FileCheck2 :size="18" /><span>可执行文件</span><strong>未发现</strong></div>
            <div><ShieldCheck :size="18" /><span>密钥与令牌</span><strong>未发现</strong></div>
            <div><BadgeCheck :size="18" /><span>许可证</span><strong>{{ importPreview.security?.license || 'Unknown' }}</strong></div>
            <div><Sparkles :size="18" /><span>技能权限</span><strong>{{ importPreview.security?.requested_skills?.length || 0 }} 项</strong></div>
            <div><Box :size="18" /><span>模型素材</span><strong>{{ importAssetReport?.status === 'invalid' ? '需要修复' : importAssetReport?.status === 'warning' ? '可安装，有提醒' : '已就绪' }}</strong></div>
          </div>
          <p v-for="assetError in importAssetReport?.errors || []" :key="assetError" class="security-warning error">{{ assetError }}</p>
          <p v-for="warning in importPreview.warnings || []" :key="warning" class="security-warning">{{ warning }}</p>
          <div class="install-actions">
            <button type="button" class="secondary-action" @click="view = 'library'">取消</button>
            <button type="button" class="primary-action" :disabled="actionBusy === 'import' || importPreview.security?.installable === false" @click="confirmImport">
              <LoaderCircle v-if="actionBusy === 'import'" class="spin" :size="18" /><PackageCheck v-else :size="18" />确认并安装
            </button>
          </div>
        </section>
      </div>
    </template>

    <form v-else-if="view === 'editor'" class="character-editor" @submit.prevent="saveCharacter">
      <section class="editor-section">
        <header><span>01</span><div><h2>身份与表达</h2><p>决定角色如何理解自己、如何说话，以及什么不能做。</p></div></header>
        <div class="form-grid">
          <label><span>角色名称 *</span><input v-model="draft.name" maxlength="80" placeholder="例如：星野澪" /></label>
          <label><span>版本</span><input v-model="draft.version" maxlength="40" placeholder="1.0.0" /></label>
          <label><span>作者</span><input v-model="draft.creator" maxlength="100" placeholder="你的名字或团队" /></label>
          <label><span>素材许可证</span><input v-model="draft.license" maxlength="160" placeholder="例如 CC BY-NC 4.0" /></label>
          <label class="span-2"><span>人格</span><textarea v-model="draft.persona" rows="5" placeholder="这个角色是谁、重视什么、与用户是什么关系。"></textarea></label>
          <label><span>性格</span><textarea v-model="draft.personality" rows="3" placeholder="冷静、好奇、直率……"></textarea></label>
          <label><span>语气</span><textarea v-model="draft.tone" rows="3" placeholder="自然简短，不装作全知。"></textarea></label>
          <label class="span-2"><span>使用场景</span><textarea v-model="draft.scenario" rows="2" placeholder="例如在桌面陪用户工作、游戏和看视频。"></textarea></label>
          <label><span>边界（每行一条）</span><textarea v-model="draft.boundaries" rows="4"></textarea></label>
          <label><span>开场白</span><textarea v-model="draft.greeting" rows="4" placeholder="切换角色后第一次见面说什么。"></textarea></label>
          <label><span>备选开场白（每行一条）</span><textarea v-model="draft.alternateGreetings" rows="4"></textarea></label>
          <label><span>对话示例</span><textarea v-model="draft.exampleDialogue" rows="4" placeholder="{{user}}: …\n{{char}}: …"></textarea></label>
          <label class="span-2"><span>系统提示词</span><textarea v-model="draft.systemPrompt" rows="3" placeholder="可选；优先保持简洁，避免重复人格描述。"></textarea></label>
          <label class="span-2"><span>历史后置指令</span><textarea v-model="draft.postHistoryInstructions" rows="2" placeholder="可选；每次对话末尾需要持续遵守的规则。"></textarea></label>
        </div>
      </section>

      <section class="editor-section">
        <header><span>02</span><div><h2>外观与动作</h2><p>支持静态立绘、Live2D 和 VRM；Live2D 会连同模型目录安全复制。</p></div></header>
        <div class="form-grid">
          <label><span>显示类型</span><select v-model="draft.modelType"><option value="static">静态立绘</option><option value="live2d">Live2D</option><option value="vrm">VRM</option></select></label>
          <label><span>主题色</span><div class="color-field"><input v-model="draft.accentColor" type="color" /><input v-model="draft.accentColor" maxlength="7" /></div></label>
          <label class="asset-field"><span>角色头像</span><div><input :value="draft.avatarPath" readonly placeholder="用于角色库，建议方图或半身头像" /><button type="button" @click="pickAsset('avatarPath')"><ImageIcon :size="16" />选择</button></div></label>
          <label class="asset-field"><span>角色立绘</span><div><input :value="draft.portraitPath" readonly placeholder="PNG / JPG / WebP" /><button type="button" @click="pickAsset('portraitPath')"><ImageIcon :size="16" />选择</button></div></label>
          <label class="asset-field"><span>{{ draft.modelType === 'live2d' ? 'model3.json' : draft.modelType === 'vrm' ? 'VRM 模型' : '可选模型文件' }}</span><div><input :value="draft.modelPath" readonly /><button type="button" @click="pickAsset('modelPath')"><Box :size="16" />选择</button></div></label>
          <label class="asset-field span-2"><span>背景</span><div><input :value="draft.backgroundPath" readonly placeholder="可选角色专属背景" /><button type="button" @click="pickAsset('backgroundPath')"><ImageIcon :size="16" />选择</button></div></label>
        </div>
        <div class="expression-editor">
          <div class="subsection-title"><div><h3>表情与动作映射</h3><p>把情绪映射到立绘或模型动作；运行时会按回答情绪自动选择。</p></div><button type="button" @click="addExpression"><Plus :size="16" />添加表情</button></div>
          <div v-for="(expression, index) in draft.expressions" :key="index" class="expression-row">
            <input v-model="expression.id" placeholder="ID" />
            <input v-model="expression.label" placeholder="名称" />
            <select v-model="expression.emotion"><option>neutral</option><option>happy</option><option>thinking</option><option>alert</option><option>worried</option><option>serious</option></select>
            <button type="button" @click="pickExpressionAsset(index)"><ImageIcon :size="15" />{{ expression.image_path || expression.image ? '已选立绘' : '立绘' }}</button>
            <input v-model="expression.expression_id" placeholder="Expression ID" />
            <input v-model="expression.motion_group" placeholder="Motion 组" />
            <input v-model.number="expression.motion_index" type="number" min="0" placeholder="序号" />
            <button type="button" class="icon-danger" aria-label="删除表情" @click="draft.expressions.splice(index, 1)"><Trash2 :size="16" /></button>
          </div>
          <p v-if="!draft.expressions.length" class="empty-subsection">暂未添加表情；系统会使用主立绘和默认情绪映射。</p>
        </div>
      </section>

      <section class="editor-section">
        <header><span>03</span><div><h2>声音</h2><p>角色包只保存音色绑定和参考素材，不会包含供应商 API Key。</p></div></header>
        <div class="form-grid">
          <label><span>音色名称</span><input v-model="draft.voiceLabel" placeholder="默认音色" /></label>
          <label><span>语言</span><input v-model="draft.voiceLanguage" placeholder="zh / ja / en" /></label>
          <label><span>语速 {{ Number(draft.voiceSpeed).toFixed(1) }}×</span><input v-model.number="draft.voiceSpeed" type="range" min="0.5" max="2" step="0.1" /></label>
          <label><span>音量 {{ Math.round(Number(draft.voiceVolume) * 100) }}%</span><input v-model.number="draft.voiceVolume" type="range" min="0" max="2" step="0.1" /></label>
          <label class="asset-field span-2"><span>参考音频</span><div><input :value="draft.referenceAudioPath" readonly placeholder="WAV / MP3 / FLAC" /><button type="button" @click="pickAsset('referenceAudioPath')"><Volume2 :size="16" />选择</button></div></label>
          <label class="span-2"><span>参考音频文本</span><textarea v-model="draft.voicePromptText" rows="2" placeholder="参考音频中说的内容，用于零样本音色。"></textarea></label>
          <label><span>参考音频语言</span><input v-model="draft.voicePromptLanguage" placeholder="留空则跟随上面的语言" /></label>
          <label class="span-2"><span>音色描述</span><textarea v-model="draft.voiceDesign" rows="2" placeholder="用一两句话描述这个声音，供 MiMo voicedesign 生成音色；本地 GPT-SoVITS 不使用这一项。"></textarea></label>

          <p class="editor-subhead span-2">GPT-SoVITS 微调模型（可选）</p>
          <label class="asset-field span-2"><span>GPT 模型 .ckpt</span><div><input :value="draft.gptModelPath" readonly placeholder="留空则用服务当前加载的权重" /><button type="button" @click="pickAsset('gptModelPath')"><FileCheck2 :size="16" />选择</button></div></label>
          <label class="asset-field span-2"><span>SoVITS 模型 .pth</span><div><input :value="draft.sovitsModelPath" readonly placeholder="留空则用服务当前加载的权重" /><button type="button" @click="pickAsset('sovitsModelPath')"><FileCheck2 :size="16" />选择</button></div></label>
        </div>
        <div class="safe-inline"><Volume2 :size="17" /><span>只填参考音频即可零样本复刻音色；两个模型是自己微调过之后才需要，留空就用 GPT-SoVITS 服务当前已加载的权重。</span></div>
        <div class="safe-inline"><ShieldCheck :size="17" /><span>密钥由 Joi 的 BYOK/语音设置管理，导出角色包时不会带走。</span></div>
      </section>

      <section class="editor-section">
        <header><span>04</span><div><h2>知识、记忆与权限</h2><p>Lorebook 跟随角色包，用户记忆与聊天记录保存在包外。</p></div></header>
        <div class="form-grid">
          <label class="span-2"><span>Lorebook（每行：关键词1,关键词2 | 内容）</span><textarea v-model="draft.lorebook" rows="6" placeholder="故乡,家乡 | 她来自海边的小城。"></textarea></label>
          <label><span>长期记忆空间</span><select v-model="draft.memoryNamespace"><option value="isolated">独立记忆（推荐）</option><option value="shared">与 Joi 共享记忆</option><option value="disabled">不使用长期记忆</option></select></label>
          <label><span>请求技能（逗号分隔）</span><input v-model="draft.requestedSkills" placeholder="joi.screen.observe, joi.browser.search" /></label>
          <label><span>来源地址</span><input v-model="draft.sourceUrl" type="url" placeholder="https://…" /></label>
          <label><span>更新清单地址</span><input v-model="draft.updateUrl" type="url" placeholder="https://…/manifest.json" /></label>
        </div>
        <div class="safe-inline"><ShieldCheck :size="17" /><span>请求的技能安装后默认保持关闭，必须由用户逐项审查授权。</span></div>
      </section>

      <footer class="editor-footer">
        <button type="button" class="secondary-action" @click="view = 'library'">取消</button>
        <button type="submit" class="primary-action" :disabled="actionBusy === 'save'">
          <LoaderCircle v-if="actionBusy === 'save'" class="spin" :size="18" /><PackageCheck v-else :size="18" />{{ editorMode === 'create' ? '创建角色包' : '保存修改' }}
        </button>
      </footer>
    </form>
  </section>
</template>

<style scoped>
.character-library {
  --ink: #172033;
  --muted: #738097;
  --line: rgba(107, 126, 157, 0.17);
  --blue: #477cf4;
  color: var(--ink);
  min-height: 100%;
  container-type: inline-size;
}

button, input, textarea, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .45; }

.library-header { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--sp-6); margin-bottom: 22px; }
.library-navigation { min-height: 30px; display: flex; align-items: center; margin-bottom: 7px; }
.library-return { min-height: 30px; display: inline-flex; align-items: center; gap: var(--sp-2); padding: 0 var(--sp-2); border: 1px solid var(--line); border-radius: var(--radius-md); color: #60708b; background: rgba(255,255,255,.62); font-size: var(--text-xs); font-weight: 730; }
.library-return:hover, .library-return:focus-visible { color: #365f9c; background: white; border-color: rgba(71,124,244,.28); }
.library-header h1 { margin: 3px 0 5px; font-size: clamp(25px, 2.5vw, 34px); letter-spacing: -.04em; }
.library-header p:not(.eyebrow) { margin: 0; color: var(--muted); font-size: var(--text-md); }
.eyebrow { margin: 0; color: #5671a1; font-size: var(--text-xs); font-weight: 800; letter-spacing: .18em; }
.header-actions, .install-actions, .editor-footer { display: flex; gap: var(--sp-3); }
.header-actions { flex: 0 0 auto; }
.header-actions button { white-space: nowrap; }
.primary-action, .secondary-action, .active-button { min-height: 42px; border-radius: var(--radius-lg); padding: 0 var(--sp-4); display: inline-flex; align-items: center; justify-content: center; gap: var(--sp-2); font-weight: 750; border: 1px solid transparent; }
.primary-action { color: white; background: var(--blue); box-shadow: 0 9px 22px rgba(71, 124, 244, .2); }
.secondary-action { color: #45536b; background: rgba(255,255,255,.72); border-color: var(--line); }
.primary-action.wide, .active-button { width: 100%; }
.active-button { color: #2a804e; background: #edf8f1; border-color: #d7efdf; }
.quiet-back { display: inline-flex; align-items: center; gap: var(--sp-2); border: 0; background: transparent; color: #60708b; padding: 0 0 var(--sp-2); }

.library-notice { display: flex; gap: var(--sp-2); align-items: center; border-radius: var(--radius-md); padding: var(--sp-3) var(--sp-4); margin: -8px 0 16px; font-size: var(--text-base); }
.library-notice.success { color: #28784a; background: #eef8f2; }
.library-notice.error { color: #a84545; background: #fff0ef; }

.active-ribbon { display: grid; grid-template-columns: 52px 1fr auto; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); background: rgba(245, 249, 255, .86); border: 1px solid rgba(119, 151, 206, .16); border-radius: var(--radius-xl); margin-bottom: 15px; }
.active-avatar { width: 52px; height: 52px; border-radius: var(--radius-lg); overflow: hidden; display: grid; place-items: center; color: #4f71ae; background: #e9f0fb; }
.active-avatar img { width: 100%; height: 100%; object-fit: cover; object-position: top center; }
.active-ribbon div:nth-child(2) { display: grid; gap: var(--sp-1); }
.active-ribbon span { color: #64728a; font-size: var(--text-xs); font-weight: 750; }
.active-ribbon strong { font-size: var(--text-lg); }
.active-ribbon small { color: #647289; }
.active-ribbon > svg { color: #2e7f4f; }

.library-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--sp-4); min-height: 490px; }
.character-rail { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-2); }
.character-card { display: grid; grid-template-columns: 58px 1fr auto; align-items: center; gap: var(--sp-3); width: 100%; min-height: 72px; padding: var(--sp-2) var(--sp-3) var(--sp-2) var(--sp-2); text-align: left; color: #344158; border: 1px solid transparent; border-radius: var(--radius-xl); background: rgba(255,255,255,.38); transition: 180ms ease; }
.character-card:hover, .character-card:focus-visible { background: rgba(255,255,255,.8); border-color: var(--line); transform: translateY(-1px); }
.character-card.selected { background: white; border-color: rgba(88, 126, 194, .22); box-shadow: 0 11px 28px rgba(59, 80, 116, .08); }
.card-art { --character-accent: #5b7ff5; position: relative; width: 58px; height: 58px; border-radius: var(--radius-lg); overflow: hidden; display: grid; place-items: center; color: var(--character-accent); font-weight: 850; font-size: var(--text-2xl); background: color-mix(in srgb, var(--character-accent) 10%, white); }
.card-art img { width: 100%; height: 100%; object-fit: cover; object-position: top center; }
.card-art i { position: absolute; right: 4px; bottom: 4px; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; color: white; background: #42b873; box-shadow: 0 2px 7px rgba(23, 75, 45, .24); }
.card-copy { display: grid; gap: var(--sp-1); min-width: 0; }
.card-copy strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card-copy small { color: #657288; }
.create-card { border-style: dashed; border-color: rgba(100, 126, 169, .2); }
.create-card .card-art { background: transparent; border: 1px dashed rgba(100, 126, 169, .36); color: #607291; }

.character-detail { overflow: hidden; background: rgba(255,255,255,.76); border: 1px solid rgba(98, 122, 160, .14); border-radius: var(--radius-xl); box-shadow: 0 18px 48px rgba(53, 72, 106, .08); }
.detail-copy { padding: var(--sp-5); }
.detail-meta { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-bottom: 14px; }
.detail-meta span { display: inline-flex; align-items: center; gap: var(--sp-1); padding: var(--sp-2) var(--sp-2); border-radius: var(--radius-pill); color: #5f6f87; background: #f2f5fa; font-size: var(--text-xs); font-weight: 750; }
.detail-title { display: flex; justify-content: space-between; gap: var(--sp-4); align-items: flex-start; }
.detail-title small { color: #63728a; font-weight: 700; }
.detail-title h2 { margin: 2px 0 0; font-size: var(--text-2xl); letter-spacing: -.03em; }
.detail-title > span { display: inline-flex; align-items: center; gap: var(--sp-1); color: #2a804e; background: #edf8f1; border-radius: var(--radius-pill); padding: var(--sp-2) var(--sp-2); font-size: var(--text-xs); font-weight: 800; }
.detail-tone { min-height: 40px; color: #657285; line-height: 1.55; font-size: var(--text-base); }
.detail-copy dl { display: grid; grid-template-columns: 1fr 1fr; margin: 16px 0; border: 1px solid var(--line); border-radius: var(--radius-lg); overflow: hidden; }
.detail-copy dl div { padding: var(--sp-3) var(--sp-3); display: grid; gap: var(--sp-1); border-bottom: 1px solid var(--line); }
.detail-copy dl div:nth-child(odd) { border-right: 1px solid var(--line); }
.detail-copy dl div:nth-last-child(-n+2) { border-bottom: 0; }
.detail-copy dt { color: #657286; font-size: var(--text-xs); }
.detail-copy dd { margin: 0; font-size: var(--text-sm); font-weight: 730; overflow: hidden; text-overflow: ellipsis; }
.inherit-conversation { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: var(--sp-1) var(--sp-2); margin-top: 10px; font-size: var(--text-sm); color: #56657d; cursor: pointer; }
.inherit-conversation input { margin: 0; }
.inherit-conversation small { grid-column: 2; font-size: var(--text-xs); color: #63728a; }
.character-locale { display: grid; gap: var(--sp-2); margin-top: 10px; font-size: var(--text-sm); color: #56657d; }
.character-locale-label { font-weight: 750; }
.character-locale-options { display: flex; gap: var(--sp-2); }
.character-locale-options button { min-height: 32px; padding: 0 var(--sp-3); display: inline-flex; align-items: center; gap: var(--sp-1); border: 1px solid var(--line); border-radius: var(--radius-md); background: #fbfcfe; color: #56657d; font-size: var(--text-sm); font-weight: 700; }
.character-locale-options button.selected { border-color: color-mix(in srgb, var(--character-accent, #5b7ff5) 55%, var(--line)); background: color-mix(in srgb, var(--character-accent, #5b7ff5) 12%, white); color: #33415c; }
.character-locale small { font-size: var(--text-xs); color: #63728a; }
.detail-tools { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--sp-2); margin-top: 10px; }
.detail-tools button { min-height: 38px; border: 1px solid var(--line); background: #fbfcfe; border-radius: var(--radius-md); display: inline-flex; align-items: center; justify-content: center; gap: var(--sp-1); color: #56657d; font-size: var(--text-xs); }
.detail-tools button.danger { color: #b74f4f; }
.uninstall-confirmation { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--sp-3) var(--sp-3); align-items: start; margin-top: 10px; padding: var(--sp-3); border: 1px solid rgba(194, 79, 79, .18); border-radius: var(--radius-lg); background: #fff8f7; }
.uninstall-confirm-icon { width: 32px; height: 32px; display: grid; place-items: center; border-radius: var(--radius-md); color: #b74f4f; background: #ffeceb; }
.uninstall-confirmation strong { display: block; color: #8f3636; font-size: var(--text-sm); }
.uninstall-confirmation p { margin: 3px 0 0; color: #816565; font-size: var(--text-xs); line-height: 1.5; }
.uninstall-confirm-actions { grid-column: 2; display: flex; justify-content: flex-end; gap: var(--sp-2); }
.uninstall-confirm-actions button { min-height: 32px; padding: 0 var(--sp-3); border: 1px solid var(--line); border-radius: var(--radius-md); color: #657189; background: white; font-size: var(--text-xs); font-weight: 750; }
.uninstall-confirm-actions .danger-confirm { display: inline-flex; align-items: center; gap: var(--sp-1); color: white; border-color: #bd5353; background: #bd5353; }
.uninstall-confirm-enter-active, .uninstall-confirm-leave-active { transition: opacity 160ms ease, transform 160ms ease; }
.uninstall-confirm-enter-from, .uninstall-confirm-leave-to { opacity: 0; transform: translateY(-4px); }
.detail-loading { min-height: 440px; display: grid; place-content: center; gap: var(--sp-3); color: #657289; }

.install-preview { display: grid; gap: var(--sp-4); }
.preview-character { display: grid; grid-template-columns: 190px 1fr; align-items: center; gap: var(--sp-6); padding: var(--sp-5); background: rgba(255,255,255,.7); border: 1px solid var(--line); border-radius: var(--radius-xl); }
.preview-art { --character-accent: #5b7ff5; height: 190px; border-radius: var(--radius-xl); display: grid; place-items: center; overflow: hidden; color: var(--character-accent); background: color-mix(in srgb, var(--character-accent) 10%, white); }
.preview-art img { width: 100%; height: 100%; object-fit: contain; object-position: center bottom; }
.preview-kicker { color: #5171a6; font-size: var(--text-xs); font-weight: 800; letter-spacing: .1em; }
.preview-character h2 { margin: 6px 0; font-size: var(--text-3xl); }
.preview-character p { color: #657285; }
.preview-meta { display: flex; flex-wrap: wrap; gap: var(--sp-2); }
.preview-meta span { padding: var(--sp-2) var(--sp-2); border-radius: var(--radius-pill); color: #5b6a82; background: #f1f5fb; font-size: var(--text-xs); }
.security-review { padding: var(--sp-5); background: white; border: 1px solid var(--line); border-radius: var(--radius-xl); }
.security-review > header { display: flex; align-items: flex-start; gap: var(--sp-3); }
.security-review > header svg { color: #307e50; }
.security-review h3 { margin: 0 0 4px; }
.security-review header p { margin: 0; color: var(--muted); font-size: var(--text-sm); }
.security-grid { display: grid; grid-template-columns: 1fr 1fr; margin: 18px 0; border: 1px solid var(--line); border-radius: var(--radius-lg); overflow: hidden; }
.security-grid div { display: grid; grid-template-columns: auto 1fr auto; gap: var(--sp-2); align-items: center; padding: var(--sp-3); border-bottom: 1px solid var(--line); }
.security-grid div:nth-child(odd) { border-right: 1px solid var(--line); }
.security-grid div:nth-last-child(-n+2) { border-bottom: 0; }
.security-grid div:last-child:nth-child(odd) { grid-column: 1 / -1; border-right: 0; }
.security-grid svg { color: #5272a3; }
.security-grid span { color: #667288; font-size: var(--text-sm); }
.security-grid strong { font-size: var(--text-sm); }
.security-warning { padding: var(--sp-2) var(--sp-3); border-radius: var(--radius-md); color: #8a6331; background: #fff7e8; font-size: var(--text-sm); }
.security-warning.error { color: #a84545; background: #fff0ef; }
.install-actions { justify-content: flex-end; margin-top: 18px; }

.character-editor { display: grid; gap: var(--sp-4); padding-bottom: var(--sp-8); }
.editor-section { padding: var(--sp-5); border-radius: var(--radius-xl); background: rgba(255,255,255,.72); border: 1px solid var(--line); }
.editor-section > header { display: grid; grid-template-columns: 36px 1fr; gap: var(--sp-3); margin-bottom: 18px; }
.editor-section > header > span { width: 32px; height: 32px; border-radius: var(--radius-md); display: grid; place-items: center; color: #4f71ab; background: #edf3fc; font-size: var(--text-xs); font-weight: 850; }
.editor-section h2 { margin: 0; font-size: var(--text-xl); }
.editor-section header p, .subsection-title p { margin: 4px 0 0; color: var(--muted); font-size: var(--text-sm); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
.form-grid label { display: grid; gap: var(--sp-2); color: #5b687d; font-size: var(--text-sm); font-weight: 720; }
.form-grid label.span-2 { grid-column: 1 / -1; }
/* No `outline: none` here. Scoped styles are unlayered, which puts them above
   the interaction layer, so suppressing the outline in this one component
   would leave exactly these fields with no keyboard focus ring while every
   other field in the app has one. */
.form-grid input:not([type="range"]), .form-grid textarea, .form-grid select { width: 100%; box-sizing: border-box; color: var(--ink); background: rgba(248,250,253,.88); border: 1px solid rgba(94, 116, 151, .16); border-radius: var(--radius-md); padding: var(--sp-3) var(--sp-3); resize: vertical; }
.form-grid input:focus, .form-grid textarea:focus, .form-grid select:focus { border-color: rgba(71,124,244,.5); box-shadow: 0 0 0 3px rgba(71,124,244,.08); }
.asset-field div, .color-field { display: grid; grid-template-columns: 1fr auto; gap: var(--sp-2); }
.asset-field button, .subsection-title button, .expression-row button { min-height: 39px; border: 1px solid var(--line); border-radius: var(--radius-md); color: #53627a; background: white; display: inline-flex; align-items: center; justify-content: center; gap: var(--sp-2); padding: 0 var(--sp-3); }
.color-field input[type="color"] { width: 44px; height: 40px; padding: var(--sp-1); border: 1px solid var(--line); border-radius: var(--radius-md); }
.color-field { grid-template-columns: auto 1fr; }
.subsection-title { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--sp-4); margin: 22px 0 10px; }
.subsection-title h3 { margin: 0; font-size: var(--text-md); }
.expression-row { display: grid; grid-template-columns: 48px minmax(80px, 1fr) 92px auto minmax(90px, 1fr) minmax(76px, 1fr) 54px 38px; gap: var(--sp-2); margin-top: 7px; }
.expression-row input, .expression-row select { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius-md); background: #fafbfd; padding: var(--sp-2); }
.expression-row button { white-space: nowrap; font-size: var(--text-xs); }
.expression-row .icon-danger { color: #b65151; padding: 0; }
.empty-subsection { color: #647286; font-size: var(--text-sm); text-align: center; padding: var(--sp-4); border: 1px dashed var(--line); border-radius: var(--radius-md); }
.safe-inline { display: flex; align-items: center; gap: var(--sp-2); color: #5c7292; background: #f2f6fc; border-radius: var(--radius-md); padding: var(--sp-3) var(--sp-3); margin-top: 14px; font-size: var(--text-sm); }
.safe-inline svg { color: #4772b0; flex: 0 0 auto; }
.editor-footer { position: sticky; bottom: 8px; justify-content: flex-end; padding: var(--sp-3); border: 1px solid rgba(97, 120, 156, .15); border-radius: var(--radius-xl); background: rgba(249,251,255,.9); backdrop-filter: blur(18px); box-shadow: 0 16px 35px rgba(48, 67, 101, .12); }
.spin { animation: spin .85s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@container (max-width: 520px) {
  .library-header { display: grid; grid-template-columns: 1fr; }
  .header-actions { width: 100%; }
  .header-actions button { flex: 1; }
  .expression-row { grid-template-columns: 50px 1fr 100px; }
  .expression-row button { min-height: 36px; }
}

@container (max-width: 400px) {
  .library-header, .preview-character { grid-template-columns: 1fr; display: grid; }
  .header-actions { width: 100%; }
  .header-actions button { flex: 1; }
  .character-rail, .form-grid { grid-template-columns: 1fr; }
  .form-grid label.span-2 { grid-column: auto; }
  .detail-tools { grid-template-columns: repeat(3, 1fr); }
  .security-grid { grid-template-columns: 1fr; }
  .security-grid div { border-right: 0 !important; border-bottom: 1px solid var(--line) !important; }
  .security-grid div:last-child { border-bottom: 0 !important; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
</style>
