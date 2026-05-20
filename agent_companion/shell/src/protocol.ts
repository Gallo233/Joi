export type EventType =
  | 'user_message'
  | 'plan_created'
  | 'approval_required'
  | 'audit_event'
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

export interface ComputerUseAuditArtifact {
  role: 'before' | 'after' | string
  kind: 'screenshot' | 'artifact' | string
  label: string
  ref?: string
}

export interface ComputerUseAuditEvent {
  task_id: string
  event_type: string
  timestamp: number
  sanitized_summary: string
  risk_level?: 'low' | 'medium' | 'high' | string
  approval_id?: string
  approval_status?: string
  tool_name?: string
  action_name?: string
  sanitized_arguments?: Record<string, unknown>
  before_artifacts?: ComputerUseAuditArtifact[]
  after_artifacts?: ComputerUseAuditArtifact[]
  verification_result?: {
    status?: string
    summary?: string
    signals?: Record<string, string>
  }
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
    approval?: {
      approval_id?: string
      task_id?: string
      step_index?: number
      tool?: string
      arguments_hash?: string
    }
    computer_use_audit?: ComputerUseAuditEvent[]
    [key: string]: unknown
  }
  created_at: number
}

export interface VoiceAudioPayload {
  task_id?: string
  event_type?: string
  event_created_at?: number
  voice_text?: string
  voice_audio_path?: string
  voice_audio_rel?: string
  voice_audio_error?: string
}

export interface UserCommand {
  id: string
  text: string
}

export interface CoreReadyPayload {
  workspace: string
  asr?: {
    enabled?: boolean
    configured?: boolean
    provider?: string
    max_seconds?: number
    max_bytes?: number
    timeout_seconds?: number
    error?: string
  }
  tts?: {
    enabled?: boolean
    configured?: boolean
    provider?: string
    last_error?: string
  }
  character?: {
    name?: string
    sprites?: Array<{
      id: string
      label?: string
      image_path: string
      image_data_url?: string
    }>
  }
}
