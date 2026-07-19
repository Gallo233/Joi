import type { AgentEvent, CoreReadyPayload, VoiceAudioPayload } from './protocol'

export type CoreStatus = 'offline' | 'connecting' | 'online'

export interface CoreClientOptions {
  url: string
  onStatus: (status: CoreStatus) => void
  onEvent: (event: AgentEvent) => void
  onReady?: (payload: CoreReadyPayload) => void
  onVoiceAudio?: (payload: VoiceAudioPayload) => void
  onError?: (message: string) => void
}

export class CoreClient {
  private socket: WebSocket | null = null
  private nextId = 1
  private reconnectTimer: number | null = null
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason?: unknown) => void; timeoutId: number }>()

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
        this.rejectPending('Joi runtime connection closed')
        this.scheduleReconnect()
      }
    }
    socket.onerror = () => this.options.onError?.('Joi runtime connection failed')
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
    this.rejectPending('Joi runtime connection closed')
  }

  sendUserText(text: string, threadId = '') {
    return this.send('user.message', { text, thread_id: threadId })
  }

  resolveApproval(approvalId: string, approved: boolean) {
    return this.send('approval.resolve', { approval_id: approvalId, approved })
  }

  selectSemanticTarget(selectionId: string, rank: number) {
    return this.send('semantic_target.select', { selection_id: selectionId, rank })
  }

  previewRuntimeConfig(updates: Record<string, unknown>) {
    return this.send('runtime.config.preview', { updates })
  }

  applyRuntimeConfig(updates: Record<string, unknown>) {
    return this.send('runtime.config.apply', { updates })
  }

  transcribeVoice(audioBase64: string, mimeType: string, timeoutMs: number) {
    return this.send(
      'voice.transcribe',
      { audio_base64: audioBase64, mime_type: mimeType },
      { timeoutMs, timeoutMessage: '语音识别等太久了，我先停下，你可以再试一次。' },
    )
  }

  readArtifact(artifact: string) {
    return this.send('artifact.read', { artifact })
  }

  watchLoopStart(params: Record<string, unknown> = {}) {
    return this.send('watch.loop.start', params)
  }

  watchLoopStop() {
    return this.send('watch.loop.stop', {})
  }

  watchLoopConfigure(params: Record<string, unknown> = {}) {
    return this.send('watch.loop.configure', params)
  }

  watchLoopRefresh(params: Record<string, unknown> = {}) {
    return this.send(
      'watch.loop.refresh',
      params,
      { timeoutMs: 60000, timeoutMessage: '画面理解耗时较久，我先停下，你可以再点一次。' },
    )
  }

  watchLoopStatus() {
    return this.send('watch.loop.status', {})
  }

  backgroundStatus() {
    return this.send('background.status', {})
  }

  backgroundConfigure(params: Record<string, unknown> = {}) {
    return this.send('background.configure', params)
  }

  backgroundClear() {
    return this.send('background.clear', {})
  }

  skillsList() {
    return this.send('skills.list', {})
  }

  agentSkillCatalog(projectId = '', characterId = '', includeDisabled = true) {
    return this.send('skill.catalog', { project_id: projectId, character_id: characterId, include_disabled: includeDisabled })
  }

  agentSkillInspect(source: string) {
    return this.send('skill.inspect', { source }, { timeoutMs: 65000, timeoutMessage: 'Skill 来源读取超时。' })
  }

  agentSkillInstall(source: string, scope: string, scopeId = '', expectedDigest = '') {
    return this.send('skill.install', { source, scope, scope_id: scopeId, expected_digest: expectedDigest }, { timeoutMs: 65000, timeoutMessage: 'Skill 安装超时。' })
  }

  agentSkillUpdate(installationId: string, expectedDigest = '') {
    return this.send('skill.update', { installation_id: installationId, expected_digest: expectedDigest }, { timeoutMs: 65000, timeoutMessage: 'Skill 更新超时。' })
  }

  agentSkillValidate(installationId: string) {
    return this.send('skill.validate', { installation_id: installationId })
  }

  agentSkillEnable(installationId: string, enabled: boolean) {
    return this.send('skill.enable', { installation_id: installationId, enabled })
  }

  agentSkillUninstall(installationId: string, confirmed = false) {
    return this.send('skill.uninstall', { installation_id: installationId, confirmed })
  }

  agentCliList() {
    return this.send('agent_cli.list', {})
  }

  agentCliTest(id: string) {
    return this.send('agent_cli.test', { id })
  }

  agentCliConfigure(params: Record<string, unknown>) {
    return this.send('agent_cli.configure', params)
  }

  agentCliStatus() {
    return this.send('agent_cli.status', {})
  }

  byokStatus() {
    return this.send('byok.status', {})
  }

  byokConnect(params: Record<string, unknown>) {
    return this.send('byok.connect', params, { timeoutMs: 30000, timeoutMessage: 'BYOK 连接测试超时，请检查端点或本地模型。' })
  }

  byokTest() {
    return this.send('byok.test', {}, { timeoutMs: 20000, timeoutMessage: 'BYOK 连接测试超时，请检查端点或网络。' })
  }

  byokModels(params: Record<string, unknown>) {
    return this.send('byok.models', params, { timeoutMs: 10000, timeoutMessage: '本地模型检测超时，请确认 Ollama 已启动。' })
  }

  byokDisconnect() {
    return this.send('byok.disconnect', {})
  }

  runtimeStatus() {
    return this.send('runtime.status', {})
  }

  runtimeConfigure(params: Record<string, unknown>) {
    return this.send('runtime.configure', params)
  }

  runtimeStart() {
    return this.send('runtime.start', {})
  }

  runtimeStop() {
    return this.send('runtime.stop', {})
  }

  runtimeApprovalResolve(approvalId: string, approved: boolean) {
    return this.send('runtime.approval.resolve', { approval_id: approvalId, approved })
  }

  joiMcpStatus() {
    return this.send('joi_mcp.status', {})
  }

  joiMcpInstallCodex() {
    return this.send('joi_mcp.install_codex', {}, { timeoutMs: 30000, timeoutMessage: 'Joi 能力连接等待超时' })
  }

  auditRecent(limit = 50) {
    return this.send('audit.recent', { limit })
  }

  conversationHistory(afterSequence = 0, limit = 160, threadId = '') {
    return this.send('conversation.history', { after_sequence: afterSequence, limit, thread_id: threadId })
  }

  projectList(includeArchived = false) {
    return this.send('project.list', { include_archived: includeArchived })
  }

  projectCreate(name: string, defaultCharacterId = '') {
    return this.send('project.create', { name, default_character_id: defaultCharacterId })
  }

  projectUpdate(projectId: string, updates: Record<string, unknown>) {
    return this.send('project.update', { project_id: projectId, ...updates })
  }

  projectArchive(projectId: string, archived = true) {
    return this.send('project.archive', { project_id: projectId, archived })
  }

  projectDelete(projectId: string, confirmed = false) {
    return this.send('project.delete', { project_id: projectId, confirmed })
  }

  threadList(projectId: string, query = '', includeArchived = false) {
    return this.send('thread.list', { project_id: projectId, query, include_archived: includeArchived })
  }

  threadCreate(projectId: string, title = '', characterId = '') {
    return this.send('thread.create', { project_id: projectId, title, character_id: characterId })
  }

  threadUpdate(threadId: string, updates: Record<string, unknown>) {
    return this.send('thread.update', { thread_id: threadId, ...updates })
  }

  threadActivate(threadId: string) {
    return this.send('thread.activate', { thread_id: threadId })
  }

  threadArchive(threadId: string, archived = true) {
    return this.send('thread.archive', { thread_id: threadId, archived })
  }

  threadDelete(threadId: string, confirmed = false) {
    return this.send('thread.delete', { thread_id: threadId, confirmed })
  }

  resourceBindingList(projectId: string) {
    return this.send('resource_binding.list', { project_id: projectId })
  }

  resourceBindingAdd(projectId: string, kind: string, value: string, label = '') {
    return this.send('resource_binding.add', { project_id: projectId, kind, value, label })
  }

  resourceBindingRemove(bindingId: string) {
    return this.send('resource_binding.remove', { binding_id: bindingId })
  }

  capabilitySessionStart(params: Record<string, unknown>) {
    return this.send('capability.session.start', params)
  }

  capabilitySessionStatus(sessionId = '') {
    return this.send('capability.session.status', { session_id: sessionId })
  }

  capabilitySessionPause(sessionId: string) {
    return this.send('capability.session.pause', { session_id: sessionId })
  }

  capabilitySessionResume(sessionId: string) {
    return this.send('capability.session.resume', { session_id: sessionId })
  }

  capabilitySessionCancel(sessionId: string) {
    return this.send('capability.session.cancel', { session_id: sessionId })
  }

  permissionGrant(sessionId: string, profile: string, scope: Record<string, unknown> = {}) {
    return this.send('permission.grant', { session_id: sessionId, profile, scope })
  }

  permissionRevoke(sessionId: string) {
    return this.send('permission.revoke', { session_id: sessionId })
  }

  gameAdapterList() {
    return this.send('game.adapter.list', {})
  }

  gameAdapterStatus(adapterId: string) {
    return this.send('game.adapter.status', { adapter_id: adapterId })
  }

  gameAdapterInstall(adapterId: string, confirmed = false) {
    return this.send('game.adapter.install', { adapter_id: adapterId, confirmed })
  }

  gameAdapterUninstall(adapterId: string, confirmed = false) {
    return this.send('game.adapter.uninstall', { adapter_id: adapterId, confirmed })
  }

  gameAdapterEnable(adapterId: string, enabled: boolean) {
    return this.send('game.adapter.enable', { adapter_id: adapterId, enabled })
  }

  gameAdapterRun(params: Record<string, unknown>) {
    return this.send('game.adapter.run', params, { timeoutMs: 920000, timeoutMessage: '游戏适配器运行超时。' })
  }

  gameAdapterPause(adapterId: string, sessionId = '') {
    return this.send('game.adapter.pause', { adapter_id: adapterId, session_id: sessionId })
  }

  gameAdapterResume(adapterId: string, sessionId = '') {
    return this.send('game.adapter.resume', { adapter_id: adapterId, session_id: sessionId })
  }

  characterList() {
    return this.send('character.list', {})
  }

  characterDetail(characterId: string) {
    return this.send('character.detail', { character_id: characterId })
  }

  characterCreate(character: Record<string, unknown>) {
    return this.send('character.create', { character }, { timeoutMs: 120000, timeoutMessage: '角色素材复制耗时较久，请检查模型文件大小。' })
  }

  characterUpdate(characterId: string, character: Record<string, unknown>) {
    return this.send('character.update', { character_id: characterId, character }, { timeoutMs: 120000, timeoutMessage: '角色更新耗时较久，请稍后再试。' })
  }

  characterInspect(path: string) {
    return this.send('character.inspect', { path }, { timeoutMs: 120000, timeoutMessage: '角色包安全检查耗时较久，请检查包体大小。' })
  }

  characterImport(path: string) {
    return this.send('character.import', { path }, { timeoutMs: 120000, timeoutMessage: '角色包导入耗时较久，请检查包体大小。' })
  }

  characterExport(characterId: string, destination: string) {
    return this.send('character.export', { character_id: characterId, destination }, { timeoutMs: 120000, timeoutMessage: '角色包导出耗时较久，请稍后再试。' })
  }

  characterActivate(characterId: string) {
    return this.send('character.activate', { character_id: characterId }, { timeoutMs: 60000, timeoutMessage: '角色切换耗时较久，请稍后再试。' })
  }

  characterDuplicate(characterId: string, name = '') {
    return this.send('character.duplicate', { character_id: characterId, name })
  }

  characterUninstall(characterId: string, fallbackId = '') {
    return this.send('character.uninstall', { character_id: characterId, fallback_id: fallbackId })
  }

  characterCheckUpdates(characterId: string) {
    return this.send('character.check_updates', { character_id: characterId }, { timeoutMs: 15000, timeoutMessage: '检查角色更新超时。' })
  }

  characterInstallUpdate(characterId: string, packageUrl = '') {
    return this.send('character.install_update', { character_id: characterId, package_url: packageUrl }, { timeoutMs: 180000, timeoutMessage: '角色更新耗时较久，请检查网络后重试。' })
  }

  memoryStatus() {
    return this.send('memory.status', {})
  }

  memoryList(options: { query?: string; kind?: string; offset?: number; limit?: number; sort?: 'recent' | 'oldest' | 'kind' } = {}) {
    return this.send('memory.list', options)
  }

  memoryPending(options: { offset?: number; limit?: number } = {}) {
    return this.send('memory.pending', options)
  }

  memoryRecall(query: string, limit = 8) {
    return this.send('memory.recall', { query, limit })
  }

  memoryBrowseVault() {
    return this.send('memory.browse_vault', {})
  }

  memorySaveCandidate(candidateId: number) {
    return this.send('memory.save_candidate', { candidate_id: candidateId })
  }

  memoryRejectCandidate(candidateId: number) {
    return this.send('memory.reject_candidate', { candidate_id: candidateId })
  }

  memorySetEnabled(enabled: boolean) {
    return this.send('memory.set_enabled', { enabled })
  }

  memoryDelete(memoryId: number) {
    return this.send('memory.delete', { memory_id: memoryId })
  }

  memoryUpdate(memoryId: number, text: string, kind?: string) {
    return this.send('memory.update', { memory_id: memoryId, text, kind })
  }

  memoryClear() {
    return this.send('memory.clear', {})
  }

  private send(method: string, params: Record<string, unknown>, options?: { timeoutMs?: number; timeoutMessage?: string }) {
    const payload = { jsonrpc: '2.0', id: `ui-${this.nextId++}`, method, params }
    if (this.socket?.readyState === WebSocket.OPEN) {
      return new Promise((resolve, reject) => {
        const timeoutMs = Math.max(1000, Number(options?.timeoutMs || 30000))
        const timeoutId = window.setTimeout(() => {
          const pending = this.pending.get(payload.id)
          if (!pending) return
          this.pending.delete(payload.id)
          pending.reject(new Error(options?.timeoutMessage || 'Joi runtime request timed out'))
        }, timeoutMs)
        this.pending.set(payload.id, { resolve, reject, timeoutId })
        this.socket?.send(JSON.stringify(payload))
      })
    }
    this.options.onError?.('Joi runtime is starting')
    return Promise.reject(new Error('Joi runtime is starting'))
  }

  private handleMessage(raw: string) {
    try {
      const payload = JSON.parse(raw)
      const isRpcResponse = payload?.id
        && typeof payload === 'object'
        && (Object.prototype.hasOwnProperty.call(payload, 'result') || Object.prototype.hasOwnProperty.call(payload, 'error'))
      if (isRpcResponse) {
        const pending = this.pending.get(payload.id)
        if (pending) {
          this.pending.delete(payload.id)
          window.clearTimeout(pending.timeoutId)
          if (payload.error) pending.reject(new Error(payload.error.message || 'Joi runtime request failed'))
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
      this.options.onError?.('Invalid Joi runtime message')
    }
  }

  private rejectPending(message: string) {
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timeoutId)
      pending.reject(new Error(message))
    }
    this.pending.clear()
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 1600)
  }
}
