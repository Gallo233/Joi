/**
 * Human-readable names for Watch Together's transcript sources and failures.
 *
 * These are the strings a person reads when a capture path is unavailable, so
 * they must stay plain and specific -- "无回环设备" tells someone what to fix,
 * "system_audio_device_missing" does not. Unknown values fall through
 * unchanged rather than being hidden, which keeps a new Core status visible
 * instead of silently blank.
 */

export function sourceLabel(value: string) {
  const labels: Record<string, string> = {
    system_audio: '系统音频',
    ocr_subtitle: '字幕/OCR',
    auto: '自动',
  }
  return labels[value] || value
}

export function errorLabel(value: string) {
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
    ready: '就绪',
    success: '成功',
    failed: '失败',
    unavailable: '不可用',
    ok: '正常',
  }
  return labels[value] || value
}
