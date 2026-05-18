import type { AgentEvent, CoreReadyPayload } from './protocol'

export type CoreStatus = 'offline' | 'connecting' | 'online'

export interface CoreClientOptions {
  url: string
  onStatus: (status: CoreStatus) => void
  onEvent: (event: AgentEvent) => void
  onReady?: (payload: CoreReadyPayload) => void
  onVoiceAudio?: (payload: { voice_audio_path?: string; voice_audio_rel?: string }) => void
  onError?: (message: string) => void
}

export class CoreClient {
  private socket: WebSocket | null = null
  private nextId = 1
  private reconnectTimer: number | null = null
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason?: unknown) => void }>()

  constructor(private readonly options: CoreClientOptions) {}

  connect() {
    this.close()
    this.options.onStatus('connecting')
    const socket = new WebSocket(this.options.url)
    this.socket = socket

    socket.onopen = () => this.options.onStatus('online')
    socket.onclose = () => {
      if (this.socket === socket) {
        this.options.onStatus('offline')
        this.scheduleReconnect()
      }
    }
    socket.onerror = () => this.options.onError?.('Core bridge connection failed')
    socket.onmessage = (message) => this.handleMessage(message.data)
  }

  close() {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.socket) {
      const socket = this.socket
      this.socket = null
      socket.close()
    }
  }

  sendUserText(text: string) {
    return this.send('user.message', { text })
  }

  resolveApproval(approvalId: string, approved: boolean) {
    return this.send('approval.resolve', { approval_id: approvalId, approved })
  }

  transcribeVoice(audioBase64: string, mimeType: string) {
    return this.send('voice.transcribe', { audio_base64: audioBase64, mime_type: mimeType })
  }

  private send(method: string, params: Record<string, unknown>) {
    const payload = { jsonrpc: '2.0', id: `ui-${this.nextId++}`, method, params }
    if (this.socket?.readyState === WebSocket.OPEN) {
      return new Promise((resolve, reject) => {
        this.pending.set(payload.id, { resolve, reject })
        this.socket?.send(JSON.stringify(payload))
        window.setTimeout(() => {
          const pending = this.pending.get(payload.id)
          if (!pending) return
          this.pending.delete(payload.id)
          pending.reject(new Error('Core request timed out'))
        }, 30000)
      })
    }
    this.options.onError?.('Core bridge is offline')
    return Promise.reject(new Error('Core bridge is offline'))
  }

  private handleMessage(raw: string) {
    try {
      const payload = JSON.parse(raw)
      if (payload?.id && (payload.result || payload.error)) {
        const pending = this.pending.get(payload.id)
        if (pending) {
          this.pending.delete(payload.id)
          if (payload.error) pending.reject(new Error(payload.error.message || 'Core request failed'))
          else pending.resolve(payload.result)
        }
        return
      }
      if (payload?.method === 'agent.event' && payload.params) {
        this.options.onEvent(payload.params as AgentEvent)
        return
      }
      if (payload?.method === 'core.ready' && payload.params) {
        this.options.onReady?.(payload.params as CoreReadyPayload)
        return
      }
      if (payload?.method === 'agent.voice_audio' && payload.params) {
        this.options.onVoiceAudio?.(payload.params)
      }
    } catch {
      this.options.onError?.('Invalid core message')
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 1600)
  }
}
