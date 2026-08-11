export interface VoiceAudioIdentity {
  task_id?: string
  event_type?: string
  event_created_at?: number
  voice_text?: string
}

const VOICE_KEY_SEPARATOR = '\u001f'
const PLAYABLE_VOICE_EVENT_TYPES = new Set([
  'approval_required',
  'runtime_final',
  'runtime_error',
  'tool_completed',
  'tool_failed',
])

/**
 * Keep internal lifecycle narration out of the speaker even if an older core
 * or a replayed event happens to contain synthesized audio.
 */
export function isPlayableVoiceEventType(eventType: string | undefined) {
  return Boolean(eventType && PLAYABLE_VOICE_EVENT_TYPES.has(eventType))
}

export function voiceAudioKey(payload: VoiceAudioIdentity) {
  return [
    payload.task_id || '',
    payload.event_type || '',
    payload.event_created_at === undefined ? '' : String(payload.event_created_at),
    payload.voice_text || '',
  ].join(VOICE_KEY_SEPARATOR)
}

export function nextVoiceEpoch(currentEpoch: number) {
  return Math.max(0, Math.floor(currentEpoch)) + 1
}

export function shouldPlayVoiceAudio(eventEpoch: number | undefined, currentEpoch: number) {
  return typeof eventEpoch === 'number' && eventEpoch === currentEpoch
}

export function asrRpcTimeoutMs(timeoutSeconds: number | undefined, graceSeconds = 10) {
  const timeout = Number.isFinite(Number(timeoutSeconds)) ? Number(timeoutSeconds) : 30
  const grace = Number.isFinite(Number(graceSeconds)) ? Number(graceSeconds) : 10
  return Math.max(1, Math.floor(timeout)) * 1000 + Math.max(0, Math.floor(grace)) * 1000
}
