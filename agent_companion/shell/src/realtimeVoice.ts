// Explicit extension: the shell test runner strips types without resolving them.
import { normalizeCharacterMotion, type CharacterMotionName } from './characterMotion.ts'

export type RealtimeVoiceState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'user_speaking'
  | 'thinking'
  | 'assistant_speaking'
  | 'acting'
  | 'paused'
  | 'recovery_required'
  | 'error'

export type RealtimeVoiceEvent =
  | { kind: 'state'; state: RealtimeVoiceState; epoch?: number }
  | { kind: 'barge_in'; epoch: number }
  | { kind: 'user_transcript'; text: string; final: boolean; epoch?: number }
  | { kind: 'assistant_transcript'; text: string; final: boolean; epoch?: number }
  | { kind: 'game_action'; action?: string; status: string; recoveryRequired?: boolean }
  | { kind: 'character_motion'; motion: CharacterMotionName; epoch: number; durationMs: number; loop: boolean; intensity: number }
  | { kind: 'skill_action'; skill: string; status: string; requiresConfirmation: boolean; error?: string }
  | { kind: 'tts_state'; state: 'muted'; error: string; epoch: number }
  | { kind: 'error'; error: string }

export interface RealtimeStartResult {
  ok?: boolean
  session_id?: string
  state?: string
  error?: string
}

export interface RealtimeCoreMotion {
  name?: string
  duration_ms?: number
  loop?: boolean
  intensity?: number
}

export interface RealtimeCoreEvent {
  session_id?: string
  type?: string
  state?: string
  text?: string
  final?: boolean
  epoch?: number
  action?: string
  status?: string
  error?: string
  recovery_required?: boolean
  motion?: RealtimeCoreMotion
  skill?: string
  requires_confirmation?: boolean
}

interface MediaTrackLike {
  stop(): void
}

interface MediaStreamLike {
  getTracks(): MediaTrackLike[]
}

interface PcmCaptureLike {
  stop(): void
}

export interface RealtimeVoiceSessionOptions {
  mode?: 'conversation' | 'minecraft'
  minecraftSessionId?: string
  startSession: (params: Record<string, unknown>) => Promise<RealtimeStartResult>
  appendAudio: (params: Record<string, unknown>) => unknown
  stopSession: (sessionId: string) => unknown
  getUserMedia?: () => Promise<MediaStreamLike>
  createCapture?: (stream: MediaStreamLike, onPcm16: (audio: Uint8Array) => void) => PcmCaptureLike
  onState?: (state: RealtimeVoiceState) => void
  onEvent?: (event: RealtimeVoiceEvent) => void
}

const SAFE_ERRORS = new Set([
  'realtime_unconfigured',
  'realtime_config_error',
  'realtime_invalid_request',
  'realtime_invalid_response',
  'realtime_auth_failed',
  'realtime_rate_limited',
  'realtime_timeout',
  'realtime_unavailable',
  'realtime_provider_error',
  'realtime_disconnected',
  'realtime_audio_overflow',
  'realtime_audio_sequence_gap',
  'realtime_local_tts_unavailable',
  'realtime_local_tts_failed',
  'minecraft_session_not_runnable',
  'microphone_unavailable',
  'audio_capture_unavailable',
])

const PCM_SAMPLE_RATE = 16_000
const PCM_CHANNELS = 1
const PCM_SAMPLE_WIDTH = 2
const PCM_CHUNK_SAMPLES = 640 // 40 ms; provider contract allows 20–100 ms.

function boundedText(value: unknown) {
  return typeof value === 'string' ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').slice(0, 8000) : ''
}

export function parseRealtimeVoiceEvent(raw: unknown): RealtimeVoiceEvent | null {
  let event: RealtimeCoreEvent
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      event = parsed as RealtimeCoreEvent
    } catch {
      return null
    }
  } else if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    event = raw as RealtimeCoreEvent
  } else {
    return null
  }
  const epoch = Math.max(0, Number(event.epoch || 0))
  if (event.type === 'state' && isRealtimeState(event.state)) return { kind: 'state', state: event.state, epoch }
  if (event.type === 'barge_in') return { kind: 'barge_in', epoch }
  if (event.type === 'user_transcript' || event.type === 'assistant_transcript') {
    const text = boundedText(event.text)
    if (!text) return null
    return event.type === 'user_transcript'
      // The epoch pairs what was heard with what was answered, so a realtime
      // turn can be shown in the chat as one exchange.
      ? { kind: 'user_transcript', text, final: Boolean(event.final), epoch }
      : { kind: 'assistant_transcript', text, final: Boolean(event.final), epoch }
  }
  if (event.type === 'assistant_text') {
    const text = boundedText(event.text)
    return text ? { kind: 'assistant_transcript', text, final: true, epoch } : null
  }
  if (event.type === 'game_action') {
    return {
      kind: 'game_action',
      action: boundedText(event.action).slice(0, 40) || undefined,
      status: boundedText(event.status).slice(0, 24) || 'failed',
      recoveryRequired: Boolean(event.recovery_required),
    }
  }
  if (event.type === 'character_motion') {
    // Core already canonicalized this against its own table; the Shell only
    // renders a name it knows, so an unknown one is dropped rather than mapped.
    const motion = normalizeCharacterMotion(event.motion?.name)
    if (!motion) return null
    return {
      kind: 'character_motion',
      motion,
      epoch,
      durationMs: Math.max(0, Number(event.motion?.duration_ms || 0)),
      loop: Boolean(event.motion?.loop),
      intensity: Number(event.motion?.intensity || 0.8),
    }
  }
  if (event.type === 'skill_action') {
    return {
      kind: 'skill_action',
      skill: boundedText(event.skill).slice(0, 32),
      status: boundedText(event.status).slice(0, 24) || 'failed',
      requiresConfirmation: Boolean(event.requires_confirmation),
      error: boundedText(event.error).slice(0, 48) || undefined,
    }
  }
  if (event.type === 'tts_state' && event.state === 'muted') {
    return { kind: 'tts_state', state: 'muted', error: safeError(event.error), epoch }
  }
  if (event.type === 'error') return { kind: 'error', error: safeError(event.error) }
  return null
}

export class RealtimeVoiceSession {
  state: RealtimeVoiceState = 'idle'
  lastError = ''
  sessionId = ''
  private stream: MediaStreamLike | null = null
  private capture: PcmCaptureLike | null = null
  private generation = 0
  private sequence = 1
  private readonly options: RealtimeVoiceSessionOptions

  constructor(options: RealtimeVoiceSessionOptions) {
    this.options = options
  }

  async start() {
    if (this.state !== 'idle' && this.state !== 'error') return
    const generation = ++this.generation
    this.lastError = ''
    this.setState('connecting')
    try {
      const getUserMedia = this.options.getUserMedia
        || (() => navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } }) as unknown as Promise<MediaStreamLike>)
      let stream: MediaStreamLike
      try {
        stream = await getUserMedia()
      } catch {
        throw new Error('microphone_unavailable')
      }
      if (generation !== this.generation) {
        stopTracks(stream)
        return
      }
      this.stream = stream
      const result = await this.options.startSession({
        mode: this.options.mode || 'conversation',
        minecraft_session_id: this.options.mode === 'minecraft' ? this.options.minecraftSessionId || '' : '',
        disclosure_accepted: true,
      })
      if (!result.ok || !result.session_id) throw new Error(safeError(result.error))
      if (generation !== this.generation) {
        stopTracks(stream)
        void this.options.stopSession(result.session_id)
        return
      }
      this.sessionId = result.session_id
      this.sequence = 1
      try {
        const createCapture = this.options.createCapture || createBrowserPcmCapture
        this.capture = createCapture(stream, (audio) => this.sendPcm(audio, generation))
      } catch {
        void this.options.stopSession(this.sessionId)
        this.sessionId = ''
        throw new Error('audio_capture_unavailable')
      }
      this.setState('listening')
    } catch (error) {
      const code = safeError(error instanceof Error ? error.message : '')
      this.releaseMedia()
      this.lastError = code
      this.setState('error')
      throw new Error(code)
    }
  }

  handleCoreEvent(raw: unknown) {
    const source = raw && typeof raw === 'object' ? raw as RealtimeCoreEvent : {}
    if (!this.sessionId || source.session_id !== this.sessionId) return
    const event = parseRealtimeVoiceEvent(source)
    if (!event) return
    if (event.kind === 'state' && (event.state === 'error' || event.state === 'recovery_required')) {
      this.terminateFromCore(event.state, 'realtime_disconnected')
      this.options.onEvent?.(event)
      return
    }
    if (event.kind === 'state') this.setState(event.state)
    else if (event.kind === 'barge_in') this.setState('user_speaking')
    else if (event.kind === 'game_action') this.setState(event.recoveryRequired ? 'recovery_required' : event.status === 'acting' ? 'acting' : 'listening')
    else if (event.kind === 'error') {
      this.terminateFromCore('error', event.error)
      this.options.onEvent?.(event)
      return
    }
    this.options.onEvent?.(event)
  }

  stop() {
    this.generation += 1
    const sessionId = this.sessionId
    this.sessionId = ''
    this.releaseMedia()
    if (sessionId) void this.options.stopSession(sessionId)
    this.lastError = ''
    this.setState('idle')
  }

  private sendPcm(audio: Uint8Array, generation: number) {
    if (generation !== this.generation || !this.sessionId || audio.byteLength !== PCM_CHUNK_SAMPLES * PCM_SAMPLE_WIDTH) return
    const params = {
      session_id: this.sessionId,
      sequence: this.sequence++,
      sample_rate: PCM_SAMPLE_RATE,
      channels: PCM_CHANNELS,
      sample_width: PCM_SAMPLE_WIDTH,
      audio_base64: bytesToBase64(audio),
    }
    try {
      const pending = this.options.appendAudio(params)
      if (pending && typeof (pending as PromiseLike<unknown>).then === 'function') {
        void Promise.resolve(pending).catch(() => this.fail('realtime_disconnected'))
      }
    } catch {
      this.fail('realtime_disconnected')
    }
  }

  private fail(code: string) {
    const sessionId = this.sessionId
    this.sessionId = ''
    this.releaseMedia()
    if (sessionId) void this.options.stopSession(sessionId)
    this.lastError = safeError(code)
    this.setState('error')
  }

  private terminateFromCore(state: 'error' | 'recovery_required', code: string) {
    const sessionId = this.sessionId
    this.sessionId = ''
    this.generation += 1
    this.releaseMedia()
    if (sessionId) void this.options.stopSession(sessionId)
    this.lastError = safeError(code)
    this.setState(state)
  }

  private releaseMedia() {
    this.capture?.stop()
    this.capture = null
    stopTracks(this.stream)
    this.stream = null
  }

  private setState(state: RealtimeVoiceState) {
    this.state = state
    this.options.onState?.(state)
  }
}

function createBrowserPcmCapture(stream: MediaStreamLike, onPcm16: (audio: Uint8Array) => void): PcmCaptureLike {
  const constructors = window as unknown as {
    AudioContext?: typeof AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  const AudioContextClass = constructors.AudioContext || constructors.webkitAudioContext
  if (!AudioContextClass) throw new Error('audio_capture_unavailable')
  const context = new AudioContextClass({ latencyHint: 'interactive' })
  const source = context.createMediaStreamSource(stream as unknown as MediaStream)
  const processor = context.createScriptProcessor(2048, 1, 1)
  const silent = context.createGain()
  silent.gain.value = 0
  const pending: number[] = []
  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0)
    const samples = resampleMono(input, context.sampleRate, PCM_SAMPLE_RATE)
    for (const sample of samples) pending.push(sample)
    while (pending.length >= PCM_CHUNK_SAMPLES) {
      const bytes = new Uint8Array(PCM_CHUNK_SAMPLES * 2)
      const view = new DataView(bytes.buffer)
      for (let index = 0; index < PCM_CHUNK_SAMPLES; index += 1) {
        const sample = Math.max(-1, Math.min(1, pending.shift() || 0))
        view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      }
      onPcm16(bytes)
    }
  }
  source.connect(processor)
  processor.connect(silent)
  silent.connect(context.destination)
  void context.resume()
  return {
    stop() {
      processor.onaudioprocess = null
      processor.disconnect()
      source.disconnect()
      silent.disconnect()
      pending.length = 0
      void context.close()
    },
  }
}

function resampleMono(input: Float32Array, sourceRate: number, targetRate: number): Float32Array {
  if (sourceRate === targetRate) return new Float32Array(input)
  const length = Math.max(1, Math.round(input.length * targetRate / sourceRate))
  const output = new Float32Array(length)
  const ratio = sourceRate / targetRate
  for (let index = 0; index < length; index += 1) {
    const position = index * ratio
    const left = Math.min(input.length - 1, Math.floor(position))
    const right = Math.min(input.length - 1, left + 1)
    const fraction = position - left
    output[index] = input[left] * (1 - fraction) + input[right] * fraction
  }
  return output
}

function bytesToBase64(value: Uint8Array) {
  let binary = ''
  for (let index = 0; index < value.byteLength; index += 1) binary += String.fromCharCode(value[index])
  return btoa(binary)
}

function stopTracks(stream: MediaStreamLike | null) {
  for (const track of stream?.getTracks() || []) track.stop()
}

function isRealtimeState(value: unknown): value is RealtimeVoiceState {
  return typeof value === 'string' && [
    'idle', 'connecting', 'listening', 'user_speaking', 'thinking', 'assistant_speaking',
    'acting', 'paused', 'recovery_required', 'error',
  ].includes(value)
}

function safeError(value: unknown) {
  const code = typeof value === 'string' ? value.trim().split(':', 1)[0] : ''
  return SAFE_ERRORS.has(code) ? code : 'realtime_unavailable'
}
