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
  skill_id?: string
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
    expression_sync?: {
      emotion?: string
      sprite?: string
      voice_style?: string
    }
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
  event_tool?: string
  watch_commentary?: boolean
  voice_text?: string
  voice_emotion?: string
  voice_sprite?: string
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

export interface WatchLoopStatus {
  active?: boolean
  session_id?: string
  query?: string
  interval_seconds?: number
  sample_count?: number
  transcript_source?: string
  active_transcript_source?: string
  transcript_status?: string
  iterations?: number
  started_at?: number
  updated_at?: number
  last_summary?: string
  last_transcript?: string[]
  rolling_summary?: string
  rolling_transcript?: string[]
  transcript_window_seconds?: number
  source_health?: Record<string, unknown>
  proactive_enabled?: boolean
  commentary_interval_seconds?: number
  vision_interval_ticks?: number
  last_visual_summary?: string
  visual_status?: string
  last_comment?: string
  last_comment_at?: number
  proactive_reason?: string
  last_error?: string
}

export interface MemoryRecord {
  id: number
  kind?: string
  text?: string
  source?: string
  created_at?: number
  relevance?: number
}

export interface MemoryCandidate {
  id: number
  kind?: string
  text?: string
  source?: string
  status?: string
  created_at?: number
  resolved_at?: number
  rejection_reason?: string
  priority?: 'high' | 'medium' | 'low' | string
  priority_score?: number
  priority_reason?: string
}

export interface MemoryProfile {
  version?: string
  enabled?: boolean
  summary?: string
  highlights?: string[]
  preferences?: string[]
  habits?: string[]
  relationship?: string[]
  recent_focus?: string[]
  counts?: Record<string, number>
  updated_at?: number
}

export interface MemoryStatus {
  enabled?: boolean
  vault_label?: string
  storage?: string
  recent?: MemoryRecord[]
  pending?: MemoryCandidate[]
  profile?: MemoryProfile
}

export interface MemoryVaultSection {
  title: string
  lines: string[]
}

export interface MemoryVault {
  path_label?: string
  storage?: string
  updated_at?: number
  sections?: MemoryVaultSection[]
}

export interface BackgroundContextScope {
  id?: string
  type?: 'window' | 'project' | 'game' | string
  label?: string
  approved_at?: number
  enabled?: boolean
}

export interface BackgroundContextEntry {
  created_at?: number
  scope_id?: string
  scope_type?: string
  source?: string
  summary?: string
  visual_status?: string
  transcript_source?: string
}

export interface BackgroundContextStatus {
  version?: string
  safe_for_display?: boolean
  enabled?: boolean
  active?: boolean
  active_scope?: BackgroundContextScope
  approved_scopes?: BackgroundContextScope[]
  scope_count?: number
  recent_context?: BackgroundContextEntry[]
  recent_count?: number
  retention?: string
  video_recording?: boolean
}

export interface NativeSkill {
  id: string
  label: string
  category?: string
  description?: string
  tools?: string[]
  rpc_methods?: string[]
  input_schema?: Record<string, unknown>
  result_schema?: Record<string, unknown>
  permission_level?: 'low' | 'medium' | 'high' | string
  supports_dry_run?: boolean
  local_capability?: 'ready' | 'off' | 'unavailable' | 'degraded' | string
  enabled?: boolean
  configured?: boolean
  state_policy?: string
  audit?: string
  notes?: string[]
}

export interface NativeSkillManifest {
  version?: string
  safe_for_display?: boolean
  workspace_bound?: boolean
  skills?: NativeSkill[]
}

export interface CoreReadyPayload {
  workspace_label?: string
  workspace_bound?: boolean
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
  watch_loop?: WatchLoopStatus
  memory?: MemoryStatus
  audit?: {
    version?: string
    safe_for_display?: boolean
    record_count?: number
    storage?: string
  }
  background?: BackgroundContextStatus
  skills?: NativeSkillManifest
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
    skills?: Record<string, { enabled?: boolean }>
  }
  character?: {
    name?: string
    sprites?: Array<{
      id: string
      label?: string
      image_data_url?: string
    }>
  }
}
