/**
 * Watch Together: the shared-viewing loop and the settings that shape it.
 *
 * PRD 4.4 keeps this quiet by default -- an interval, a spoiler level and a
 * transcript source, not a running commentary -- so the cadence settings live
 * beside the loop that obeys them rather than in a distant settings blob.
 *
 * Lifted from App.vue unchanged.
 */

import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import type { CoreClient } from '../api'
import type { CoreReadyPayload, WatchLoopStatus } from '../protocol'
import { asRecord, stringValue } from './safeRecord'
import { errorLabel, sourceLabel } from './watchLabels'

export function useWatchLoop(
  client: CoreClient,
  errorText: Ref<string>,
  ready: Ref<CoreReadyPayload | null>,
  latestWatchLoopStatus: Ref<WatchLoopStatus | undefined>,
) {
  const watchCommentaryInterval = ref(30)

  const watchProactiveEnabled = ref(false)

  const watchSceneMode = ref<'quiet' | 'commentary' | 'translate' | 'analysis' | 'accessibility'>('quiet')

  const watchSpoilerLevel = ref<'none' | 'current_scene' | 'full'>('none')

  const watchTranscriptSource = ref<'system_audio' | 'ocr_subtitle' | 'auto'>('system_audio')

  const watchVisionInterval = ref(5)

  const watchLoopActive = computed(() => Boolean(watchLoopStatus.value.active))

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
        const count = Number(row.count || 0)
        const status = stringValue(row.status)
        const error = stringValue(row.error)
        const capture = stringValue(row.capture)
        const bytes = Number(row.audio_bytes || 0)
        const parts = [`${sourceLabel(source)} ${count ? `${count} 段` : '诊断'}`]
        if (status) parts.push(errorLabel(status))
        if (capture) parts.push(`采集 ${capture}`)
        if (bytes) parts.push(`${Math.round(bytes / 1024)}KB`)
        if (error) parts.push(errorLabel(error))
        return parts.join(' · ')
      })
      .slice(0, 3)
  })

  const watchLoopStatus = computed<WatchLoopStatus>(() => latestWatchLoopStatus.value || ready.value?.watch_loop || {})

  const watchLoopTranscript = computed(() => {
    const rows = Array.isArray(watchLoopStatus.value.rolling_transcript) && watchLoopStatus.value.rolling_transcript.length
      ? watchLoopStatus.value.rolling_transcript
      : Array.isArray(watchLoopStatus.value.last_transcript)
        ? watchLoopStatus.value.last_transcript
        : []
    return rows.filter(Boolean).slice(0, 3).join(' / ')
  })

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
      mode: watchSceneMode.value,
      spoiler_level: watchSpoilerLevel.value,
      vision_interval_ticks: watchVisionInterval.value,
    }).catch((error) => {
      errorText.value = error instanceof Error ? error.message : '实时陪看启动失败'
    })
  }

  function stopWatchLoop() {
    errorText.value = ''
    void client.watchLoopStop().catch((error) => {
      errorText.value = error instanceof Error ? error.message : '实时陪看停止失败'
    })
  }
  return {
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
  }
}
