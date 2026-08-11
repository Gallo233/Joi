/**
 * Background context: what Joi has quietly noticed, and the scope it may notice
 * within.
 *
 * This is the surface PRD 4.6 governs -- observation is bounded by an explicit
 * scope, retention is visible, and the user can clear it. The switch, the
 * scope selector and the recent entries belong together because they are the
 * three halves of one promise: you can see what was kept, and you can end it.
 *
 * Lifted from App.vue unchanged.
 */

import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import type { CoreClient } from '../api'
import type { AgentEvent, BackgroundContextEntry, BackgroundContextScope, BackgroundContextStatus } from '../protocol'
import { asRecord, stringValue } from './safeRecord'
import { sourceLabel } from './watchLabels'

export function useBackgroundContext(client: CoreClient, errorText: Ref<string>) {
  const backgroundStatus = ref<BackgroundContextStatus | null>(null)

  const backgroundEnabled = computed(() => backgroundStatus.value?.enabled === true)

  const backgroundScopeType = ref<'window' | 'project' | 'game'>('window')

  const backgroundLoading = ref(false)

  const backgroundActive = computed(() => backgroundStatus.value?.active === true)

  const backgroundScopes = computed<BackgroundContextScope[]>(() => backgroundStatus.value?.approved_scopes || [])

  const backgroundRecentRows = computed<BackgroundContextEntry[]>(() => backgroundStatus.value?.recent_context || [])

  function backgroundScopeMeta(scope: BackgroundContextScope) {
    const pieces = [backgroundScopeTypeLabel(scope.type)]
    if (scope.approved_at) pieces.push(new Date(scope.approved_at * 1000).toLocaleString([], { hour12: false }))
    pieces.push(scope.enabled === false ? '关闭' : '启用')
    return pieces.join(' · ')
  }

  const backgroundScopeLabel = ref('当前窗口')

  function backgroundScopeTypeLabel(value?: string) {
    const labels: Record<string, string> = {
      window: '窗口',
      project: '项目',
      game: '游戏',
    }
    return labels[value || ''] || '范围'
  }

  const backgroundStateText = computed(() => {
    if (!backgroundStatus.value) return '未连接'
    if (!backgroundEnabled.value) return '已关闭'
    return backgroundActive.value ? '已批准' : '等待范围'
  })

  const backgroundSummaryText = computed(() => {
    if (!backgroundStatus.value) return '等待核心状态'
    if (!backgroundEnabled.value) return '后台上下文关闭'
    const scope = backgroundStatus.value.active_scope
    if (!scope) return '需要批准窗口、项目或游戏范围'
    return `${backgroundScopeTypeLabel(scope.type)} · ${scope.label || '已批准范围'}`
  })

  function backgroundRetentionLabel(value?: string) {
    const labels: Record<string, string> = {
      summaries_only: '仅摘要',
    }
    return labels[value || ''] || value || '仅摘要'
  }

  const backgroundRetentionText = computed(() => backgroundRetentionLabel(backgroundStatus.value?.retention))

  function backgroundEntryMeta(row: BackgroundContextEntry) {
    const pieces = [backgroundScopeTypeLabel(row.scope_type)]
    const source = stringValue(row.source)
    if (source) pieces.push(source === 'watch_loop' ? '陪看' : source)
    const transcript = stringValue(row.transcript_source)
    if (transcript) pieces.push(sourceLabel(transcript))
    const visual = stringValue(row.visual_status)
    if (visual) pieces.push(`视觉 ${visual}`)
    return pieces.join(' · ')
  }

  function backgroundEntryTime(row: BackgroundContextEntry) {
    if (!row.created_at) return '刚刚'
    return new Date(row.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  function syncBackgroundFromEvent(event: AgentEvent) {
    const background = asRecord(event.agent_state?.background_context)
    if ('safe_for_display' in background || 'recent_context' in background || 'recent_count' in background || 'scope_count' in background) {
      backgroundStatus.value = background as unknown as BackgroundContextStatus
    }
  }

  async function refreshBackgroundStatus() {
    backgroundLoading.value = true
    try {
      const result = (await client.backgroundStatus()) as { ok?: boolean; background?: BackgroundContextStatus; error?: string }
      if (result.background) backgroundStatus.value = result.background
      if (!result.ok) errorText.value = result.error || '背景上下文读取失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '背景上下文读取失败'
    } finally {
      backgroundLoading.value = false
    }
  }

  async function toggleBackgroundEnabled(event: Event) {
    const enabled = Boolean((event.target as HTMLInputElement | null)?.checked)
    backgroundLoading.value = true
    try {
      const result = (await client.backgroundConfigure({ enabled })) as { ok?: boolean; background?: BackgroundContextStatus; error?: string }
      if (result.background) backgroundStatus.value = result.background
      if (!result.ok) errorText.value = result.error || '背景上下文开关更新失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '背景上下文开关更新失败'
    } finally {
      backgroundLoading.value = false
    }
  }

  async function configureBackgroundScope() {
    backgroundLoading.value = true
    try {
      const label = backgroundScopeLabel.value.trim() || backgroundScopeTypeLabel(backgroundScopeType.value)
      const result = (await client.backgroundConfigure({
        enabled: true,
        scope_type: backgroundScopeType.value,
        label,
      })) as { ok?: boolean; background?: BackgroundContextStatus; error?: string }
      if (result.background) backgroundStatus.value = result.background
      if (!result.ok) errorText.value = result.error || '背景范围批准失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '背景范围批准失败'
    } finally {
      backgroundLoading.value = false
    }
  }

  async function clearBackgroundContext() {
    if (!backgroundRecentRows.value.length) return
    if (!window.confirm(`清空 ${backgroundRecentRows.value.length} 条背景摘要？批准范围会保留。`)) return
    backgroundLoading.value = true
    try {
      const result = (await client.backgroundClear()) as { ok?: boolean; background?: BackgroundContextStatus; error?: string }
      if (result.background) backgroundStatus.value = result.background
      if (!result.ok) errorText.value = result.error || '背景摘要清空失败'
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '背景摘要清空失败'
    } finally {
      backgroundLoading.value = false
    }
  }
  return {
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
  }
}
