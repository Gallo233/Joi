/**
 * Long-term memory: the saved record, the candidates awaiting the user's
 * decision, and the search over both.
 *
 * PRD 4.2 makes this the user's to govern -- candidates are proposed, never
 * assumed -- so the confirm/edit/reject path and the enabled switch live
 * together here rather than being spread across a settings panel and a
 * workspace view.
 *
 * Lifted from App.vue unchanged. Its only ties to the rest of the shell are
 * the Core client and the shared error line, both passed in.
 */

import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import type { CoreClient } from '../api'
import type {
  AgentEvent,
  MemoryCandidate,
  MemoryCandidatePage,
  MemoryPage,
  MemoryRecord,
  MemoryStatus,
  MemoryVault,
} from '../protocol'
import { asRecord } from './safeRecord'

export function useMemory(client: CoreClient, errorText: Ref<string>, readonlyMode = false) {
  const pendingMemories = computed(() => (memoryStatus.value?.pending || []).filter((item) => item.status === 'pending'))
  const recentMemories = computed(() => memoryStatus.value?.recent || [])
  const memoryStatus = ref<MemoryStatus | null>(null)

  const memoryQuery = ref('')

  const memoryRows = ref<MemoryRecord[]>([])

  const memoryPendingRows = ref<MemoryCandidate[]>([])

  const memoryView = ref<'saved' | 'pending'>('saved')

  const memoryTotal = ref(0)

  const memoryHasMore = ref(false)

  const memoryOffset = ref(0)

  const memorySearchLoading = ref(false)

  const memoryVault = ref<MemoryVault | null>(null)

  const editingMemoryId = ref<number | null>(null)

  const editingMemoryText = ref('')

  const editingMemoryKind = ref('note')

  const memoryEnabled = computed(() => memoryStatus.value?.enabled !== false)

  const memoryProfile = computed(() => memoryStatus.value?.profile || null)

  const memoryProfileSections = computed(() =>
    [
      { key: 'preferences', label: '偏好', rows: memoryProfile.value?.preferences || [] },
      { key: 'habits', label: '习惯', rows: memoryProfile.value?.habits || [] },
      { key: 'relationship', label: '关系', rows: memoryProfile.value?.relationship || [] },
      { key: 'recent_focus', label: '最近关注', rows: memoryProfile.value?.recent_focus || [] },
    ],
  )

  const memorySavedCount = computed(() => Number(memoryStatus.value?.counts?.saved ?? memoryProfile.value?.counts?.saved ?? recentMemories.value.length))

  const memoryPendingCount = computed(() => Number(memoryStatus.value?.counts?.pending ?? memoryProfile.value?.counts?.pending ?? pendingMemories.value.length))

  const memoryProfileCountText = computed(() => {
    return `${memorySavedCount.value} 条已保存 · ${memoryPendingCount.value} 条待确认`
  })

  const topPendingMemory = computed(() => pendingMemories.value[0] || null)

  const memoryAuthorizeText = computed(() => topPendingMemory.value?.text || '')

  const memoryQueryText = computed(() => memoryQuery.value.trim())

  const displayedMemoryRows = computed(() => memoryRows.value)

  const displayedMemoryCandidates = computed(() => memoryPendingRows.value)

  const memorySearchEmptyText = computed(() => (memoryQueryText.value ? '没有找到相关记忆' : 'Joi 还没有保存长期记忆'))

  function syncMemoryFromEvent(event: AgentEvent) {
    const memory = asRecord(event.agent_state?.memory)
    if ('recent' in memory || 'pending' in memory || 'vault_label' in memory) {
      memoryStatus.value = memory as unknown as MemoryStatus
    }
  }

  function memoryPriorityLabel(value?: string) {
    if (value === 'high') return '高优先'
    if (value === 'medium') return '中优先'
    if (value === 'low') return '低优先'
    return value || '普通'
  }

  async function refreshMemoryStatus() {
    try {
      const result = (await client.memoryStatus()) as { ok?: boolean; memory?: MemoryStatus }
      if (result.memory) memoryStatus.value = result.memory
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆状态读取失败'
    }
  }

  async function refreshMemoryWorkspace() {
    if (readonlyMode) {
      await loadMemoryPage(true)
      return
    }
    await Promise.all([refreshMemoryStatus(), loadMemoryPage(true), browseMemoryVault()])
  }

  async function loadMemoryPage(reset = true) {
    if (memorySearchLoading.value) return
    memorySearchLoading.value = true
    const offset = reset ? 0 : memoryOffset.value
    try {
      if (memoryView.value === 'pending') {
        const result = (await client.memoryPending({ offset, limit: 12 })) as { ok?: boolean; page?: MemoryCandidatePage; error?: string }
        const page = result.page
        if (!result.ok || !page) {
          errorText.value = result.error || '待确认记忆读取失败'
          return
        }
        memoryPendingRows.value = reset ? page.items : [...memoryPendingRows.value, ...page.items]
        memoryRows.value = []
        memoryTotal.value = page.total
        memoryOffset.value = page.offset + page.items.length
        memoryHasMore.value = page.has_more
        return
      }
      const result = (await client.memoryList({ query: memoryQueryText.value, offset, limit: 12, sort: 'recent' })) as { ok?: boolean; page?: MemoryPage; error?: string }
      const page = result.page
      if (!result.ok || !page) {
        errorText.value = result.error || '记忆列表读取失败'
        return
      }
      memoryRows.value = reset ? page.items : [...memoryRows.value, ...page.items]
      memoryPendingRows.value = []
      memoryTotal.value = page.total
      memoryOffset.value = page.offset + page.items.length
      memoryHasMore.value = page.has_more
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆列表读取失败'
    } finally {
      memorySearchLoading.value = false
    }
  }

  async function searchMemory() {
    memoryView.value = 'saved'
    await loadMemoryPage(true)
  }

  async function selectMemoryView(view: 'saved' | 'pending') {
    if (memoryView.value === view && (memoryRows.value.length || memoryPendingRows.value.length)) return
    memoryView.value = view
    editingMemoryId.value = null
    await loadMemoryPage(true)
  }

  function beginMemoryEdit(memory: MemoryRecord) {
    editingMemoryId.value = memory.id
    editingMemoryText.value = memory.text || ''
    editingMemoryKind.value = memory.kind || 'note'
  }

  function cancelMemoryEdit() {
    editingMemoryId.value = null
    editingMemoryText.value = ''
  }

  async function saveMemoryEdit(memoryId: number) {
    if (readonlyMode) return
    const text = editingMemoryText.value.trim()
    if (!text) return
    try {
      const result = (await client.memoryUpdate(memoryId, text, editingMemoryKind.value)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      if (!result.ok) {
        errorText.value = result.error === 'duplicate_memory' ? '已经存在相同记忆' : result.error || '记忆更新失败'
        return
      }
      cancelMemoryEdit()
      await loadMemoryPage(true)
      void browseMemoryVault()
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆更新失败'
    }
  }

  function memoryKindLabel(kind?: string) {
    const value = (kind || 'note').toLocaleLowerCase()
    if (value.startsWith('preference')) return '偏好'
    if (value.startsWith('habit') || value.startsWith('routine')) return '习惯'
    if (value.startsWith('relationship') || value.startsWith('identity') || value.startsWith('persona')) return '关系'
    if (value.startsWith('project') || value.startsWith('focus') || value.startsWith('task')) return '关注'
    return '笔记'
  }

  function memoryTime(timestamp?: number) {
    if (!timestamp) return '刚刚'
    const value = new Date(timestamp * 1000)
    const today = new Date()
    if (value.toDateString() === today.toDateString()) return value.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return value.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  async function browseMemoryVault() {
    if (readonlyMode) return
    try {
      const result = (await client.memoryBrowseVault()) as { ok?: boolean; vault?: MemoryVault; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      if (result.vault) memoryVault.value = result.vault
      if (!result.ok) errorText.value = result.error || '记忆库读取失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆库读取失败'
    }
  }

  async function saveMemoryCandidate(candidateId: number) {
    if (readonlyMode) return
    try {
      const result = (await client.memorySaveCandidate(candidateId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      await loadMemoryPage(true)
      void browseMemoryVault()
      if (!result.ok) errorText.value = result.error || '记忆保存失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆保存失败'
    }
  }

  async function rejectMemoryCandidate(candidateId: number) {
    if (readonlyMode) return
    try {
      const result = (await client.memoryRejectCandidate(candidateId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      await loadMemoryPage(true)
      if (!result.ok) errorText.value = result.error || '记忆已忽略'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆忽略失败'
    }
  }

  async function toggleMemoryEnabled(event: Event) {
    if (readonlyMode) return
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
    if (readonlyMode) return
    try {
      const result = (await client.memoryDelete(memoryId)) as { ok?: boolean; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      await loadMemoryPage(true)
      void browseMemoryVault()
      if (!result.ok) errorText.value = result.error || '记忆删除失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆删除失败'
    }
  }

  async function clearMemory() {
    if (readonlyMode) return
    const count = memorySavedCount.value + memoryPendingCount.value
    if (!count) return
    if (!window.confirm(`清空 ${count} 条记忆和待确认候选？此操作不会删除手动编辑区。`)) return
    try {
      const result = (await client.memoryClear()) as { ok?: boolean; memory?: MemoryStatus; error?: string }
      if (result.memory) memoryStatus.value = result.memory
      memoryRows.value = []
      memoryPendingRows.value = []
      memoryTotal.value = 0
      memoryHasMore.value = false
      void browseMemoryVault()
      if (!result.ok) errorText.value = result.error || '记忆清空失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '记忆清空失败'
    }
  }
  return {
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
  }
}
