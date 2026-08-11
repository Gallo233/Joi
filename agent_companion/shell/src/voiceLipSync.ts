/**
 * Drive the mouth from the voice that is actually playing.
 *
 * The mouth used to open on a sine wave for a duration derived from the
 * reply's character count. That reads as "talking" only until you hear it: a
 * short line read slowly, or a long one read quickly, and the mouth is plainly
 * moving to its own rhythm. Reading the audio itself is what makes it lip sync
 * rather than a talking animation.
 *
 * Web Audio can also report silence forever -- an AudioContext that never
 * resumed, or a source the graph is not allowed to inspect. That is why this
 * reports whether the signal is trustworthy instead of only a level. Callers
 * keep the mouth closed whenever `live` is false; text never substitutes for
 * audio.
 */

// Spelled with its extension, unlike the rest of the shell: this module is
// loaded directly by `npm run test:shell`, and Node's type stripping resolves
// relative imports literally rather than the way a bundler would.
import { NEUTRAL_VISEMES, blendVisemes, visemesFromSpectrum, type VisemeWeights } from './voiceVisemes.ts'

export interface MouthSignal {
  /** 0 closed, 1 wide open. */
  level: number
  /** Whether real audio is driving `level` right now. */
  live: boolean
  /**
   * Which vowel shapes the mouth is making, summing to 1.
   *
   * Neutral -- all `aa` -- whenever `live` is false, so a caller that ignores
   * this field behaves exactly as it did when the mouth had only one shape.
   */
  visemes: VisemeWeights
}

/**
 * Map a real analyser signal onto a model's mouth range.
 *
 * The hard `live` gate is intentional: text arrival, a failed synthesis, and a
 * paused/ended element all produce exactly zero. Character runtimes use this
 * helper instead of maintaining their own text-duration fallback, so one line
 * can create only one lip-sync interval: the interval in which audio plays.
 */
export function voiceDrivenMouthLevel(signal: MouthSignal, floor = 0, scale = 1): number {
  if (!signal.live) return 0
  return Math.min(1, Math.max(0, floor + signal.level * scale))
}

// VRM blendshapes were previously capped at 0.68 (0.06 + 0.62), noticeably
// quieter than both the audible performance and Joi's other renderers.  Keep
// these values next to the audio gate so their test proves that raising the
// amplitude can never reintroduce text-driven mouth motion.
export const VRM_MOUTH_FLOOR = 0.08
export const VRM_MOUTH_SCALE = 0.86

// Speech RMS sits well below full scale, so it is lifted into a range that
// actually opens a mouth, then clamped.
const GAIN = 5.2
const NOISE_FLOOR = 0.012
// Opening fast and closing slower avoids a chattering jaw on consonants.
const ATTACK = 0.55
const RELEASE = 0.18
// If nothing crosses the floor for this long the analyser is not telling us
// anything useful, so the mouth closes rather than inventing speech from text.
const SILENCE_GRACE_MS = 700
// A mouth cannot change shape every frame without reading as a glitch, so the
// shape crosses most of the way to a new vowel in roughly 60ms.
const SHAPE_BLEND = 0.22

let context: AudioContext | null = null
let analyser: AnalyserNode | null = null
let samples: Float32Array<ArrayBuffer> | null = null
let spectrum: Float32Array<ArrayBuffer> | null = null
let attached: HTMLMediaElement | null = null
let streaming = false
let streamingStartedAt = 0
let streamingScheduledUntil = 0
const streamingSources = new Set<AudioBufferSourceNode>()
let smoothed = 0
let lastSoundAt = 0
let sawSound = false
let unlocked = false
let shape: VisemeWeights = { ...NEUTRAL_VISEMES }

function ensureContext(): AudioContext | null {
  if (context) return context
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    context = new Ctor()
  } catch {
    return null
  }
  return context
}

/**
 * Route a playing element through an analyser.
 *
 * The element keeps playing through the graph, so this must connect to the
 * destination or the voice goes silent. `createMediaElementSource` may only be
 * called once per element, which is fine because playback builds a new one for
 * every line.
 */
export function attachLipSync(audio: HTMLMediaElement): void {
  const ctx = ensureContext()
  if (!ctx) return
  detachLipSync()
  try {
    const source = ctx.createMediaElementSource(audio)
    const node = ctx.createAnalyser()
    // 2048 rather than 1024: reading a vowel needs the bins to resolve F1,
    // which sits as low as 300Hz, and at 1024 bins over 48kHz each bin is 23Hz
    // wide -- coarse enough to move a vowel to its neighbour.
    node.fftSize = 2048
    node.smoothingTimeConstant = 0.15
    source.connect(node)
    node.connect(ctx.destination)
    analyser = node
    samples = new Float32Array(node.fftSize)
    spectrum = new Float32Array(node.frequencyBinCount)
    attached = audio
    sawSound = false
    lastSoundAt = 0
    shape = { ...NEUTRAL_VISEMES }
    // Autoplay policies can leave the context suspended; without this the
    // graph runs but every sample reads zero and the voice is inaudible.
    if (ctx.state === 'suspended') void ctx.resume()
  } catch {
    // A source the graph cannot read leaves `live` false, and the caller keeps
    // its timer. Never let this take the audio down with it.
    analyser = null
    samples = null
    spectrum = null
    attached = null
  }
}

/** Decode little-endian signed PCM16 without involving an audio element. */
export function decodePcm16Base64(encoded: string): Float32Array {
  if (!encoded) return new Float32Array(0)
  const binary = atob(encoded)
  const frames = Math.floor(binary.length / 2)
  const samples = new Float32Array(frames)
  for (let index = 0; index < frames; index += 1) {
    const low = binary.charCodeAt(index * 2)
    const high = binary.charCodeAt(index * 2 + 1)
    let value = low | (high << 8)
    if (value >= 0x8000) value -= 0x10000
    samples[index] = value < 0 ? value / 32768 : value / 32767
  }
  return samples
}

/**
 * Schedule one MiMo PCM chunk on the same analyser used by file playback.
 * Chunks are placed back-to-back when generation is ahead of playback. If the
 * provider falls behind, the mouth closes during the real audible gap instead
 * of inventing motion from the already-returned text.
 */
export function enqueuePcm16Chunk(encoded: string, sampleRate = 24000, reset = false): boolean {
  const ctx = ensureContext()
  if (!ctx) return false
  if (reset) detachLipSync()
  let decoded: Float32Array
  try {
    decoded = decodePcm16Base64(encoded)
  } catch {
    return false
  }
  if (!decoded.length) return false
  if (!streaming || !analyser || !samples || !spectrum) {
    const node = ctx.createAnalyser()
    node.fftSize = 2048
    node.smoothingTimeConstant = 0.15
    node.connect(ctx.destination)
    analyser = node
    samples = new Float32Array(node.fftSize)
    spectrum = new Float32Array(node.frequencyBinCount)
    attached = null
    streaming = true
    streamingStartedAt = 0
    streamingScheduledUntil = 0
    sawSound = false
    lastSoundAt = 0
    shape = { ...NEUTRAL_VISEMES }
  }
  if (ctx.state === 'suspended') void ctx.resume()

  const rate = Number.isFinite(sampleRate) ? Math.max(8000, Math.floor(sampleRate)) : 24000
  const buffer = ctx.createBuffer(1, decoded.length, rate)
  buffer.getChannelData(0).set(decoded)
  const source = ctx.createBufferSource()
  source.buffer = buffer
  source.connect(analyser)
  const startAt = Math.max(ctx.currentTime + 0.035, streamingScheduledUntil)
  if (!streamingStartedAt || startAt > streamingScheduledUntil + 0.05) streamingStartedAt = startAt
  streamingScheduledUntil = startAt + buffer.duration
  streamingSources.add(source)
  source.onended = () => {
    streamingSources.delete(source)
    if (streamingSources.size || ctx.currentTime < streamingScheduledUntil - 0.02) return
    streaming = false
    analyser?.disconnect()
    analyser = null
    samples = null
    spectrum = null
    smoothed = 0
    sawSound = false
    shape = { ...NEUTRAL_VISEMES }
  }
  source.start(startAt)
  return true
}

/**
 * Let the character speak without the user having clicked first.
 *
 * A reply arrives over a WebSocket, which is not a user gesture, and a webview
 * is entitled to refuse to start audio outside one. In the dev browser it does
 * not; a packaged WKWebView is stricter, and there the refusal is near
 * invisible -- `play()` rejects, the mouth never moves, and nothing on screen
 * says why.
 *
 * Called from a real gesture, this does the two things that lift the
 * restriction for everything after it: it starts the AudioContext the mouth
 * reads from, and it plays one silent element so element playback counts as
 * already begun. Both are cheap and idempotent.
 */
export function unlockAudioPlayback(): void {
  const ctx = ensureContext()
  if (ctx && ctx.state === 'suspended') void ctx.resume()
  if (unlocked) return
  unlocked = true
  try {
    // 44 bytes: a WAVE header describing zero samples. Nothing is audible, and
    // the webview still counts it as playback the user asked for.
    const silence = new Audio(
      'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=',
    )
    silence.volume = 0
    void silence.play().catch(() => {
      // Refused even from a gesture: nothing more to try, and the caller's own
      // playback will report the failure when it happens.
    })
  } catch {
    // No Audio constructor at all; playback will fail visibly later.
  }
}

export function detachLipSync(): void {
  for (const source of streamingSources) {
    try {
      source.stop()
    } catch {
      // It may already have ended between the set iteration and stop().
    }
  }
  streamingSources.clear()
  streaming = false
  streamingStartedAt = 0
  streamingScheduledUntil = 0
  try {
    analyser?.disconnect()
  } catch {
    // A detached node is already silent.
  }
  analyser = null
  samples = null
  spectrum = null
  attached = null
  smoothed = 0
  sawSound = false
  lastSoundAt = 0
  shape = { ...NEUTRAL_VISEMES }
}

/** Current mouth opening and shape, and whether audio is really behind them. */
export function mouthSignal(now: number = performance.now()): MouthSignal {
  if (!analyser || !samples || (!attached && !streaming)) {
    return { level: 0, live: false, visemes: { ...NEUTRAL_VISEMES } }
  }
  if (attached && (attached.paused || attached.ended)) {
    smoothed = 0
    return { level: 0, live: false, visemes: { ...NEUTRAL_VISEMES } }
  }
  if (streaming && context && (context.currentTime < streamingStartedAt || context.currentTime >= streamingScheduledUntil)) {
    smoothed = 0
    return { level: 0, live: false, visemes: { ...NEUTRAL_VISEMES } }
  }
  analyser.getFloatTimeDomainData(samples)
  let sum = 0
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i]
  const rms = Math.sqrt(sum / samples.length)
  if (rms > NOISE_FLOOR) {
    lastSoundAt = now
    sawSound = true
  }
  const target = Math.min(1, Math.max(0, (rms - NOISE_FLOOR) * GAIN))
  smoothed += (target - smoothed) * (target > smoothed ? ATTACK : RELEASE)
  // Playing but never audible means the analyser is blind to this source.
  const live = sawSound && now - lastSoundAt < SILENCE_GRACE_MS
  if (!live) {
    shape = { ...NEUTRAL_VISEMES }
    return { level: 0, live, visemes: shape }
  }
  // Only read a shape out of audio loud enough to have one. Below the floor
  // the spectrum is room tone, and chasing its peaks would work the mouth
  // through vowels between every word.
  if (spectrum && rms > NOISE_FLOOR) {
    analyser.getFloatFrequencyData(spectrum)
    shape = blendVisemes(shape, visemesFromSpectrum(spectrum, analyser.context.sampleRate), SHAPE_BLEND)
  }
  return { level: smoothed, live, visemes: shape }
}
