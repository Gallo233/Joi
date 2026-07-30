export type EventType =
  | 'user_message'
  | 'plan_created'
  | 'runtime_started'
  | 'runtime_delta'
  | 'runtime_final'
  | 'runtime_error'
  | 'skill_started'
  | 'skill_completed'
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

// What the shared presence is doing. Unlike ui_phase it accounts for the
// capability session: a paused session reads as 'paused', not 'acting'.
export type PublicPhase =
  | 'idle'
  | 'received'
  | 'understanding'
  | 'thinking'
  | 'acting'
  | 'waiting'
  | 'paused'
  | 'done'
  | 'failed'

export interface AgentEvent {
  event_id?: string
  sequence?: number
  type: EventType
  task_id: string
  project_id?: string
  thread_id?: string
  session_id?: string
  character_id?: string
  public_phase?: PublicPhase | string
  display_card: DisplayCard
  voice_line: VoiceLine
  agent_state?: {
    ui_phase?: 'received' | 'understanding' | 'thinking' | 'acting' | 'waiting' | 'done' | 'failed' | 'idle' | string
    ui_label?: string
    ui_transient?: boolean
    public_phase?: PublicPhase | string
    voice_audio_path?: string
    voice_audio_rel?: string
    voice_audio_data_url?: string
    voice_audio_error?: string
    expression_sync?: {
      emotion?: string
      sprite?: string
      voice_style?: string
    }
    character_motion?: {
      name?: 'idle' | 'greet' | 'talk' | 'happy' | 'finger_gun' | 'dance' | string
      label?: string
      duration_ms?: number
      loop?: boolean
      intensity?: number
      interruptible?: boolean
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

export type PermissionProfile = 'observe' | 'collaborate' | 'delegate'

export interface JoiProject {
  id: string
  name: string
  default_character_id: string
  archived: boolean
  created_at: number
  updated_at: number
}

export interface JoiThread {
  id: string
  project_id: string
  title: string
  character_id: string
  archived: boolean
  created_at: number
  updated_at: number
}

export interface ResourceBinding {
  id: string
  project_id: string
  kind: 'directory' | 'application' | 'domain' | 'game' | string
  value: string
  label: string
  metadata?: Record<string, unknown>
  created_at: number
}

export interface ActionReceipt {
  id: string
  session_id: string
  step_index: number
  action: string
  risk: string
  before_summary?: string
  after_summary?: string
  verification?: Record<string, unknown>
  duration_ms?: number
  status: string
  created_at: number
}

export interface CapabilitySession {
  id: string
  project_id: string
  thread_id: string
  capability: string
  goal: string
  permission_profile: PermissionProfile
  state: 'created' | 'running' | 'paused' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled' | string
  driver: string
  budget?: Record<string, number>
  stop_conditions?: string[]
  permission?: { id?: string; profile?: PermissionProfile; scope?: Record<string, unknown>; status?: string }
  receipts?: ActionReceipt[]
  created_at: number
  updated_at: number
  completed_at?: number | null
}

export interface CollaborationSnapshot {
  schema_version?: number
  active?: { project_id?: string; thread_id?: string; session_id?: string; character_id?: string }
  projects?: JoiProject[]
  threads?: JoiThread[]
  bindings?: ResourceBinding[]
  capability_session?: CapabilitySession
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
  mode?: 'quiet' | 'commentary' | 'translate' | 'analysis' | 'accessibility' | string
  spoiler_level?: 'none' | 'current_scene' | 'full' | string
  raw_media_retention?: boolean
  scene_observation?: Record<string, unknown>
}

export interface MemoryRecord {
  id: number
  kind?: string
  text?: string
  source?: string
  created_at?: number
  updated_at?: number
  relevance?: number
}

export interface MemoryPage {
  items: MemoryRecord[]
  total: number
  offset: number
  limit: number
  has_more: boolean
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

export interface MemoryCandidatePage {
  items: MemoryCandidate[]
  total: number
  offset: number
  limit: number
  has_more: boolean
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
  counts?: {
    saved?: number
    pending?: number
    manual_notes?: number
    updated_at?: number
    by_kind?: Record<string, number>
  }
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
  counts?: MemoryStatus['counts']
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

// What Joi observed while fetching a Skill, as opposed to what the package
// says about itself. `trust` is derived from the source and the signature; a
// package cannot raise its own tier.
export interface AgentSkillProvenance {
  source_kind: string
  source_ref: string
  resolved_ref: string
  digest: string
  inspected_at: number
  trust: 'local' | 'remote_unsigned' | 'signed' | string
  signature: 'absent' | 'verified' | 'unverifiable' | 'invalid' | string
  publisher: string
  auto_update: boolean
}

export interface AgentSkillInspection {
  source: string
  source_kind: string
  name: string
  description: string
  version: string
  author?: string
  license?: string
  digest: string
  scripts?: string[]
  references?: string[]
  assets?: string[]
  permissions?: Record<string, string[]>
  dependencies?: string[]
  warnings?: string[]
  code_bearing?: boolean
  implicit_invocation?: boolean
  instructions?: string
  provenance?: AgentSkillProvenance
}

export interface AgentSkillInstallation {
  id: string
  name: string
  version: string
  scope: 'global' | 'project' | 'character' | string
  scope_id: string
  source: string
  root_path: string
  digest: string
  manifest?: AgentSkillInspection
  provenance?: AgentSkillProvenance
  enabled: boolean
  created_at: number
  updated_at: number
}

export interface AgentSkillDraftPayload {
  description?: string
  instructions?: string
  steps?: string
  permissions?: Record<string, unknown>
  [key: string]: unknown
}

// A successful run can only ever produce a draft; installing one is a separate
// reviewed step, so drafts stay inert until the user approves them.
export interface AgentSkillDraft {
  id: string
  project_id: string
  thread_id: string
  name: string
  payload: AgentSkillDraftPayload
  status: 'draft' | 'approved' | 'rejected' | 'installed' | string
  created_at: number
  updated_at: number
}

export interface GameAdapterManifest {
  id: string
  name: string
  version: string
  author: string
  license: string
  platforms: string[]
  modes: string[]
  detection: string[]
  observation_sources: string[]
  action_sets: string[]
  pause_strategy: string
  verification: string[]
  checkpoint_strategy: string
  source: string
  code_bearing: boolean
  installed?: boolean
  enabled?: boolean
  paused?: boolean
  detection_status?: Record<string, unknown>
}

export interface AgentCliModelOption {
  id: string
  label: string
  reasoning?: string[]
  default_reasoning?: string
}

export interface AgentCliProfile {
  id: string
  name: string
  vendor?: string
  installed?: boolean
  version?: string
  status?: 'ready' | 'missing' | string
  probe_ok?: boolean
  error?: string
  models?: string[]
  model_options?: AgentCliModelOption[]
  models_source?: 'cli_live' | 'profile' | 'fallback' | string
  models_error?: string
  reasoning?: string[]
  run_strategy?: string
  supports_takeover?: boolean
  notes?: string[]
}

export interface AgentCliListResult {
  ok?: boolean
  mode?: 'local_cli' | 'byok' | string
  selected?: string
  clis?: AgentCliProfile[]
  error?: string
}

export interface AgentCliTestResult {
  ok?: boolean
  summary?: string
  error?: string
  cli?: AgentCliProfile
}

export interface AgentCliRuntimeStatus {
  safe_for_display?: boolean
  enabled?: boolean
  mode?: 'local_cli' | 'byok' | string
  selected?: string
  model?: string
  reasoning?: string
  status?: string
}

export interface CodexRuntimeStatus {
  safe_for_display?: boolean
  enabled?: boolean
  mode?: 'local_cli' | 'byok' | string
  selected?: string
  model?: string
  reasoning?: string
  available?: boolean
  status?: string
  session?: string
  mcp_connected?: boolean
  pending_approvals?: number
}

export interface JoiMcpStatus {
  connected?: boolean
  available?: boolean
  status?: string
}

export interface ByokPreset {
  id: string
  label: string
  description?: string
  base_url?: string
  model?: string
  requires_key?: boolean
  cost_hint?: string
}

export interface ByokSecretStatus {
  stored?: boolean
  source?: 'system' | 'environment' | 'legacy' | 'not_required' | 'missing' | string
  secure_store_available?: boolean
}

export interface ByokTestResult {
  ok?: boolean
  error?: string
  latency_ms?: number
  models?: string[]
  checked_without_generation?: boolean
}

export interface ByokStatus {
  ok?: boolean
  configured?: boolean
  state?: 'ready' | 'not_configured' | 'incomplete' | 'mock' | string
  provider?: string
  base_url?: string
  model?: string
  temperature?: number
  requires_key?: boolean
  secret?: ByokSecretStatus
  presets?: ByokPreset[]
  last_test?: ByokTestResult | null
}

export interface ByokConnectResult {
  ok?: boolean
  saved?: boolean
  error?: string
  test?: ByokTestResult
  byok?: ByokStatus
}

export interface CharacterSummary {
  id: string
  name: string
  version: string
  active?: boolean
  built_in?: boolean
  model_type?: 'static' | 'live2d' | 'vrm'
  avatar_path?: string
  avatar_url?: string
  avatar_data_url?: string
  portrait_path?: string
  portrait_url?: string
  portrait_data_url?: string
  accent_color?: string
  memory_namespace?: 'isolated' | 'shared' | 'disabled'
  license?: string
  greeting?: string
  tone?: string
  creator?: { name?: string; notes?: string }
  requested_skills?: string[]
  package_hash?: string
  has_update_source?: boolean
}

export interface CharacterManifest {
  schema?: string
  id: string
  version: string
  identity: {
    name: string
    avatar?: string
    persona?: string
    personality?: string
    scenario?: string
    tone?: string
    boundaries?: string[]
    system_prompt?: string
    post_history_instructions?: string
    greeting?: string
    alternate_greetings?: string[]
    example_dialogue?: string
  }
  appearance?: {
    model_type?: 'static' | 'live2d' | 'vrm'
    portrait?: string
    model?: string
    background?: string
    accent_color?: string
    expressions?: Array<Record<string, unknown>>
    motions?: Array<Record<string, unknown>>
    lip_sync?: Record<string, unknown>
  }
  voice?: {
    id?: string
    label?: string
    language?: string
    prompt_language?: string
    speed?: number
    volume?: number
    reference_audio?: string
    prompt_text?: string
    emotion_map?: Record<string, unknown>
  }
  knowledge?: {
    lorebook?: { name?: string; description?: string; entries?: Array<Record<string, unknown>> }
    memory_namespace?: 'isolated' | 'shared' | 'disabled'
  }
  capabilities?: { requested_skills?: string[]; approved_skills?: string[] }
  creator?: { name?: string; notes?: string }
  source?: { type?: string; url?: string; update_url?: string }
  security?: { license?: string; compatibility?: string; built_in?: boolean; package_hash?: string }
  extensions?: Record<string, unknown>
}

export interface CharacterDetail extends CharacterSummary {
  manifest?: CharacterManifest
}

export interface CharacterListResult {
  ok?: boolean
  active_id?: string
  characters?: CharacterSummary[]
  error?: string
  message?: string
}

export interface CharacterMutationResult {
  ok?: boolean
  active_id?: string
  installed?: string
  uninstalled?: string
  path?: string
  character?: CharacterDetail
  warnings?: string[]
  security?: Record<string, unknown>
  ready?: CoreReadyPayload
  error?: string
  message?: string
}

export interface CharacterInspectResult {
  ok?: boolean
  source?: string
  preview?: CharacterDetail
  warnings?: string[]
  security?: {
    executable_content?: boolean
    secret_content?: boolean
    license?: string
    requested_skills?: string[]
    compatibility?: string
    installable?: boolean
    asset_report?: {
      model_type?: string
      status?: 'ready' | 'warning' | 'invalid' | string
      installable?: boolean
      checks?: Array<{ name?: string; ok?: boolean; detail?: string; required?: boolean }>
      errors?: string[]
      warnings?: string[]
    }
  }
  error?: string
  message?: string
}

export interface CharacterRuntime extends CharacterSummary {
  background_path?: string
  background_url?: string
  background_data_url?: string
  model_path?: string
  model_url?: string
  expression_mappings?: Array<{
    emotion?: string
    expression_id?: string
    motion_group?: string
    motion_index?: number | string
  }>
  motion_mappings?: Array<{
    id?: string
    name?: string
    motion?: string
    aliases?: string[]
    motion_group?: string
    motion_index?: number | string
    duration_ms?: number
    loop?: boolean
    intensity?: number
  }>
  lip_sync?: { parameter?: string }
  sprites?: Array<{
    id: string
    label?: string
    image_data_url?: string
  }>
}

export interface CoreReadyPayload {
  product?: string
  protocol_version?: number
  instance_id?: string
  health?: {
    livez?: string
    readyz?: string
  }
  workspace_label?: string
  workspace_bound?: boolean
  event_cursor?: number
  active_approval_ids?: string[]
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
  agent_cli?: AgentCliRuntimeStatus
  codex_runtime?: CodexRuntimeStatus
  joi_mcp?: JoiMcpStatus
  byok?: ByokStatus
  skills?: NativeSkillManifest
  agent_skills?: AgentSkillInstallation[]
  game_adapters?: GameAdapterManifest[]
  collaboration?: CollaborationSnapshot
  characters?: CharacterListResult
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
  character?: CharacterRuntime
}
