/**
 * BYOK: bring-your-own-key model configuration.
 *
 * The first domain lifted out of App.vue's 4300-line script, chosen because it
 * turned out to be genuinely self-contained: of everything these twelve
 * functions touch, only `client` and `connected` come from outside. Nothing
 * else in the shell reads this state, and this state reads nothing else.
 *
 * The code below is unchanged from where it lived before -- same logic, same
 * names -- so every template binding keeps resolving exactly as it did. That
 * is deliberate: a move and a rewrite in one step leaves nothing to compare
 * against when the rendering changes.
 */

import { computed, ref, type Ref } from 'vue'
import type { CoreClient } from '../api'
import type { ByokConnectResult, ByokPreset, ByokStatus, ByokTestResult, CoreReadyPayload } from '../protocol'

export function useByok(client: CoreClient, connected: Ref<boolean>) {
  const byokStatus = ref<ByokStatus | null>(null)

  const byokDraft = ref(defaultByokDraft())

  const byokApiKey = ref('')

  const byokLoading = ref(false)

  const byokDiscovering = ref(false)

  const byokAdvancedOpen = ref(false)

  const byokResult = ref<ByokTestResult | null>(null)

  const byokNotice = ref('')

  const byokDirty = ref(false)

  const fallbackByokPresets: ByokPreset[] = [
    { id: 'openai', label: 'OpenAI', description: '官方 API · 默认选择高性价比模型', base_url: 'https://api.openai.com/v1', model: 'gpt-5.6-luna', requires_key: true, cost_hint: '适合日常高频对话' },
    { id: 'openai_compatible', label: '兼容 API', description: '支持 OpenAI Chat Completions 的供应商', base_url: '', model: '', requires_key: true, cost_hint: '价格由供应商决定' },
    { id: 'ollama', label: 'Ollama 本地', description: '使用本机模型 · 不消耗云端额度', base_url: 'http://127.0.0.1:11434/v1', model: '', requires_key: false, cost_hint: '无 API 调用费用' },
  ]

  const byokPresets = computed(() => byokStatus.value?.presets?.length ? byokStatus.value.presets : fallbackByokPresets)

  const selectedByokPreset = computed(() => byokPresets.value.find((preset) => preset.id === byokDraft.value.provider) || byokPresets.value[0])

  const byokRequiresKey = computed(() => selectedByokPreset.value?.requires_key !== false)

  const byokSecretReady = computed(() => !byokRequiresKey.value || Boolean(byokApiKey.value.trim() || byokStatus.value?.secret?.stored))

  const byokCanConnect = computed(() => Boolean(connected.value && !byokLoading.value && !byokDiscovering.value && byokDraft.value.base_url.trim() && byokDraft.value.model.trim() && byokSecretReady.value))

  const byokKnownModels = computed(() => byokResult.value?.models || byokStatus.value?.last_test?.models || [])

  function defaultByokDraft() {
    return {
      provider: 'openai',
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-5.6-luna',
      temperature: 0.7,
    }
  }

  function syncByokStatus(status: ByokStatus | null | undefined, force = false) {
    if (!status) return
    byokStatus.value = status
    byokResult.value = status.last_test || byokResult.value
    if (byokDirty.value && !force) return
    byokDraft.value = {
      provider: status.provider || 'openai',
      base_url: status.base_url || 'https://api.openai.com/v1',
      model: status.model || 'gpt-5.6-luna',
      temperature: Number.isFinite(Number(status.temperature)) ? Number(status.temperature) : 0.7,
    }
  }

  function syncByokFromReady(payload: CoreReadyPayload) {
    syncByokStatus(payload.byok)
  }

  function selectByokPreset(preset: ByokPreset) {
    const changingProvider = byokDraft.value.provider !== preset.id
    byokDraft.value = {
      ...byokDraft.value,
      provider: preset.id,
      base_url: preset.base_url || (changingProvider ? '' : byokDraft.value.base_url),
      model: preset.model || (changingProvider ? '' : byokDraft.value.model),
    }
    byokApiKey.value = ''
    byokResult.value = null
    byokNotice.value = ''
    byokDirty.value = true
  }

  function markByokDirty() {
    byokDirty.value = true
    byokNotice.value = ''
    byokResult.value = null
  }

  function byokStateLabel() {
    if (byokLoading.value) return '正在连接'
    if (byokStatus.value?.configured) return '已连接'
    if (byokStatus.value?.state === 'mock') return 'Mock 模式'
    return '尚未连接'
  }

  function byokSecretLabel() {
    if (!byokRequiresKey.value) return '本地模型无需 API Key'
    const source = byokStatus.value?.secret?.source
    if (source === 'system') return '已安全存入系统密钥库'
    if (source === 'environment') return '正在使用环境变量'
    if (source === 'legacy') return '正在使用现有本地配置'
    if (source === 'not_required') return '本地模型无需密钥'
    if (byokApiKey.value.trim()) return '新密钥将在保存时写入系统密钥库'
    return '尚未保存密钥'
  }

  function byokErrorLabel(error = '') {
    const labels: Record<string, string> = {
      api_key_required: '请填写 API Key；已有密钥时可以留空继续使用。',
      invalid_api_key: 'API Key 格式无效，请检查是否粘贴完整。',
      secure_store_unavailable: '系统密钥库当前不可用，请重启 Joi 或检查依赖。',
      secure_store_failed: '密钥没有写入系统密钥库，请再试一次。',
      invalid_endpoint: '端点无效。云端接口需要 HTTPS，本地 Ollama 可使用 localhost。',
      invalid_model: '请填写供应商实际提供的模型 ID。',
      invalid_temperature: '温度必须在 0 到 2 之间。',
      authentication_failed: '密钥验证失败，请检查 API Key 或供应商权限。',
      endpoint_not_found: '接口地址不存在，请检查是否包含正确的 /v1 路径。',
      endpoint_unreachable: '无法连接接口，请检查网络、代理或本地服务。',
      connection_timeout: '连接测试超时，请检查接口是否可访问。',
      rate_limited: '供应商正在限流或额度不足，请检查用量后重试。',
      model_not_found: '接口可以连接，但没有找到这个模型 ID。',
      model_unconfigured: '配置还不完整，请补齐模型和密钥。',
      no_local_models: 'Ollama 已连接，但没有发现本地模型；请先拉取一个模型。',
      unsupported_discovery: '当前供应商暂不支持自动检测模型。',
      connection_test_failed: '接口测试失败；配置已保留，可以修改后重试。',
      config_write_failed: 'Joi 无法保存本地配置。',
      config_invalid: '现有 config.yaml 无法读取，请先修复配置格式。',
    }
    return labels[error] || (error ? '连接没有成功，请检查配置后重试。' : '')
  }

  async function refreshByokStatus() {
    if (!connected.value) return
    try {
      const status = await client.byokStatus() as ByokStatus
      syncByokStatus(status)
    } catch (error) {
      byokNotice.value = error instanceof Error ? error.message : '无法读取 BYOK 状态'
    }
  }

  async function connectByok() {
    if (!byokCanConnect.value) return
    byokLoading.value = true
    byokNotice.value = ''
    try {
      const payload: Record<string, unknown> = {
        provider: byokDraft.value.provider,
        base_url: byokDraft.value.base_url.trim(),
        model: byokDraft.value.model.trim(),
        temperature: Number(byokDraft.value.temperature),
      }
      if (byokApiKey.value.trim()) payload.api_key = byokApiKey.value.trim()
      const result = await client.byokConnect(payload) as ByokConnectResult
      if (result.byok) syncByokStatus(result.byok, true)
      byokResult.value = result.test || null
      if (result.saved) {
        byokApiKey.value = ''
        byokDirty.value = false
      }
      byokNotice.value = result.ok
        ? `连接成功${result.test?.latency_ms ? ` · ${result.test.latency_ms}ms` : ''}`
        : `${result.saved ? '配置已保存，但测试失败。' : ''}${byokErrorLabel(result.error || result.test?.error || '')}`
    } catch (error) {
      byokNotice.value = error instanceof Error ? error.message : 'BYOK 连接失败'
    } finally {
      byokLoading.value = false
    }
  }

  async function testByokConnection() {
    byokLoading.value = true
    byokNotice.value = ''
    try {
      const result = await client.byokTest() as ByokTestResult
      byokResult.value = result
      byokNotice.value = result.ok
        ? `连接正常${result.latency_ms ? ` · ${result.latency_ms}ms` : ''}`
        : byokErrorLabel(result.error || '')
      await refreshByokStatus()
    } catch (error) {
      byokNotice.value = error instanceof Error ? error.message : 'BYOK 测试失败'
    } finally {
      byokLoading.value = false
    }
  }

  async function disconnectByok() {
    byokLoading.value = true
    byokNotice.value = ''
    try {
      const result = await client.byokDisconnect() as { ok?: boolean; error?: string; byok?: ByokStatus; secret_removed?: boolean; secret_source?: string }
      if (result.byok) syncByokStatus(result.byok, true)
      byokResult.value = null
      byokApiKey.value = ''
      byokDirty.value = false
      if (result.ok) {
        byokNotice.value = result.secret_source === 'environment'
          ? 'BYOK 已断开；环境变量中的密钥由你继续管理。'
          : result.secret_removed
            ? 'BYOK 已断开，密钥已从系统密钥库移除。'
            : 'BYOK 已断开。'
      } else {
        byokNotice.value = byokErrorLabel(result.error || '')
      }
    } catch (error) {
      byokNotice.value = error instanceof Error ? error.message : 'BYOK 断开失败'
    } finally {
      byokLoading.value = false
    }
  }
  return {
    byokStatus,
    byokDraft,
    byokApiKey,
    byokLoading,
    byokDiscovering,
    byokAdvancedOpen,
    byokResult,
    byokNotice,
    byokDirty,
    byokPresets,
    selectedByokPreset,
    byokRequiresKey,
    byokSecretReady,
    byokCanConnect,
    byokKnownModels,
    syncByokStatus,
    syncByokFromReady,
    selectByokPreset,
    markByokDirty,
    byokStateLabel,
    byokSecretLabel,
    byokErrorLabel,
    refreshByokStatus,
    connectByok,
    testByokConnection,
    disconnectByok,
  }
}
