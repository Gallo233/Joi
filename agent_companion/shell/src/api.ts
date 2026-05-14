import type { AgentEvent } from './protocol'

export type CoreStatus = 'offline' | 'connecting' | 'online'

export interface CoreClientOptions {
  url: string
  onStatus: (status: CoreStatus) => void
  onEvent: (event: AgentEvent) => void
  onVoiceAudio?: (payload: { voice_audio_path?: string; voice_audio_rel?: string }) => void
  onError?: (message: string) => void
}

export class CoreClient {
  private socket: WebSocket | null = null
  private nextId = 1
  private reconnectTimer: number | null = null

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

  sendUserText(text: string, approved = false) {
    this.send('user.message', { text, approved })
  }

  resolveApproval(taskId: string, approved: boolean) {
    this.send('approval.resolve', { task_id: taskId, approved })
  }

  private send(method: string, params: Record<string, unknown>) {
    const payload = { jsonrpc: '2.0', id: `ui-${this.nextId++}`, method, params }
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
      return
    }
    this.options.onError?.('Core bridge is offline')
  }

  private handleMessage(raw: string) {
    try {
      const payload = JSON.parse(raw)
      if (payload?.method === 'agent.event' && payload.params) {
        this.options.onEvent(payload.params as AgentEvent)
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
