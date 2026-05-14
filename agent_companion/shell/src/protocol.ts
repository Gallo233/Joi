export type EventType =
  | 'user_message'
  | 'plan_created'
  | 'approval_required'
  | 'tool_started'
  | 'tool_completed'
  | 'tool_failed'
  | 'task_completed'
  | 'task_failed'

export interface DisplayCard {
  title: string
  summary: string
  body?: string
  status?: 'info' | 'approval' | 'success' | 'failed'
  artifacts?: string[]
}

export interface VoiceLine {
  text: string
  emotion?: string
  sprite?: string
}

export interface AgentEvent {
  type: EventType
  task_id: string
  display_card: DisplayCard
  voice_line: VoiceLine
  agent_state?: {
    voice_audio_path?: string
    voice_audio_rel?: string
    voice_audio_error?: string
    policy?: {
      tool?: string
      reason?: string
      arguments_preview?: Record<string, string>
    }
    [key: string]: unknown
  }
  created_at: number
}

export interface UserCommand {
  id: string
  text: string
  approved?: boolean
}

export interface CoreReadyPayload {
  workspace: string
  character?: {
    name?: string
    sprites?: Array<{
      id: string
      label?: string
      image_path: string
    }>
  }
}
