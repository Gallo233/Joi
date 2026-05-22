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
  candidate_evidence?: Array<Record<string, unknown>>
}

export interface AgentEvent {
  type: EventType
  task_id: string
  display_card: DisplayCard
  voice_line: VoiceLine
  agent_state?: {
    voice_audio_path?: string
    voice_audio_rel?: string
    voice_audio_data_url?: string
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
  voice_audio_data_url?: string
  voice_audio_error?: string
}

export interface ArtifactReadResult {
  ok?: boolean
  artifact?: string
  mime?: string
  data_url?: string
  error?: string
}

export interface RuntimeProviderStatus {
  name: string
  label: string
  state: string
  enabled?: boolean
  configured?: boolean
  provider?: string
  model?: string
  summary?: string
  timeout_seconds?: number
  limit?: string
  last_error?: string
  notes?: string[]
}

export interface RuntimeConfigMutationChange {
  setting: string
  label: string
  action: string
  value_kind: string
}

export interface RuntimeConfigMutationError {
  code: string
  setting: string
}

export interface RuntimeConfigMutationResult {
  ok: boolean
  changed: boolean
  dry_run: boolean
  summary: string
  changes: RuntimeConfigMutationChange[]
  errors: RuntimeConfigMutationError[]
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
    volume?: number
    speed_factor?: number
    fallback_to_system?: boolean
    last_error?: string
  }
  runtime?: {
    read_only?: boolean
    safe_for_display?: boolean
    providers?: RuntimeProviderStatus[]
  }
  runtime_settings?: {
    asr?: {
      enabled?: boolean
      max_seconds?: number
      max_bytes?: number
      timeout_seconds?: number
    }
    tts?: {
      enabled?: boolean
      volume?: number
      speed_factor?: number
      fallback_to_system?: boolean
    }
    ocr?: {
      timeout_seconds?: number
    }
    llm?: {
      temperature?: number
      use_mock?: boolean
    }
    computer_use?: {
      post_action_settle_ms?: number
    }
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
