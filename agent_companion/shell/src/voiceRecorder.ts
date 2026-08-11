/**
 * Record the microphone as WAV, because that is what the recogniser reads.
 *
 * `MediaRecorder` gives whatever container the engine prefers -- webm in
 * Chromium, mp4 in the WKWebView the packaged app runs in -- and MiMo's
 * recogniser documents only WAV and MP3. That mismatch fails late and
 * confusingly: the recording works, the upload works, and the service refuses
 * it. Transcoding afterwards would mean shipping ffmpeg for one conversion.
 *
 * Capturing the raw samples and writing the 44-byte header ourselves avoids
 * all of it, and has the side benefit that the sample rate is known rather
 * than whatever the container happened to negotiate.
 */

/** 16 kHz is plenty for speech and a quarter the bytes of 48 kHz. */
const TARGET_SAMPLE_RATE = 16000

export interface VoiceRecording {
  blob: Blob
  mimeType: string
  seconds: number
}

/**
 * Turn mono float samples into a 16-bit PCM WAVE file.
 *
 * Exported for its own sake: it is the part with a header format to get wrong,
 * and it is pure, so it can be checked without a microphone.
 */
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array<ArrayBuffer> {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const bytes = new Uint8Array(buffer)
  const view = new DataView(buffer)
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true) // PCM header length
  view.setUint16(20, 1, true) // uncompressed
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // bytes per second
  view.setUint16(32, 2, true) // bytes per frame
  view.setUint16(34, 16, true) // bits per sample
  ascii(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    // Clamp before scaling: a sample slightly past full scale would otherwise
    // wrap to the opposite extreme and click.
    const clamped = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, Math.round(clamped * (clamped < 0 ? 0x8000 : 0x7fff)), true)
  }
  return bytes
}

/** Average channels down to mono, since the recogniser wants one. */
function toMono(input: Float32Array[], frames: number): Float32Array {
  if (input.length === 1) return input[0]
  const mono = new Float32Array(frames)
  for (const channel of input) {
    for (let i = 0; i < frames; i += 1) mono[i] += channel[i] / input.length
  }
  return mono
}

export class VoiceRecorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private processor: ScriptProcessorNode | null = null
  private chunks: Float32Array[] = []
  private frames = 0

  get active(): boolean {
    return this.processor !== null
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    // Ask for the target rate directly; engines that refuse simply give their
    // own, and the header records whatever we actually got.
    this.context = new Ctor({ sampleRate: TARGET_SAMPLE_RATE })
    if (this.context.state === 'suspended') await this.context.resume()
    const source = this.context.createMediaStreamSource(this.stream)
    // ScriptProcessor rather than an AudioWorklet: a worklet needs a separate
    // module file fetched at runtime, which a packaged app serves from its own
    // origin and a strict CSP can block. This is deprecated but universally
    // available, and it runs only while the user is holding the button.
    this.processor = this.context.createScriptProcessor(4096, 1, 1)
    this.chunks = []
    this.frames = 0
    this.processor.onaudioprocess = (event) => {
      const channels: Float32Array[] = []
      for (let i = 0; i < event.inputBuffer.numberOfChannels; i += 1) {
        channels.push(new Float32Array(event.inputBuffer.getChannelData(i)))
      }
      const mono = toMono(channels, event.inputBuffer.length)
      this.chunks.push(mono)
      this.frames += mono.length
    }
    source.connect(this.processor)
    // A ScriptProcessor only fires while it is connected to the graph, and
    // the destination is the only sink guaranteed to pull it. Volume stays at
    // zero so the user does not hear themselves.
    const mute = this.context.createGain()
    mute.gain.value = 0
    this.processor.connect(mute)
    mute.connect(this.context.destination)
  }

  /** Stop, release the microphone, and return what was captured. */
  async stop(): Promise<VoiceRecording | null> {
    const context = this.context
    const sampleRate = context?.sampleRate || TARGET_SAMPLE_RATE
    if (this.processor) this.processor.onaudioprocess = null
    this.processor?.disconnect()
    this.processor = null
    // Released explicitly: leaving a track live keeps the recording indicator
    // lit, which reads as the app still listening.
    for (const track of this.stream?.getTracks() || []) track.stop()
    this.stream = null
    this.context = null
    if (context) await context.close().catch(() => undefined)
    if (!this.frames) return null

    const merged = new Float32Array(this.frames)
    let offset = 0
    for (const chunk of this.chunks) {
      merged.set(chunk, offset)
      offset += chunk.length
    }
    this.chunks = []
    this.frames = 0
    const wav = encodeWav(merged, sampleRate)
    return {
      blob: new Blob([wav], { type: 'audio/wav' }),
      mimeType: 'audio/wav',
      seconds: merged.length / sampleRate,
    }
  }
}
