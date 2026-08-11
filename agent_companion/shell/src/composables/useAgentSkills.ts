/**
 * Third-party Skills: inspect, install, update, remove, and the drafts awaiting
 * a decision.
 *
 * PRD 4.8 makes inspection the gate -- a Skill's source, permissions and steps
 * are shown before anything is installed, never after -- so `inspectAgentSkill`
 * and `installInspectedAgentSkill` stay adjacent here rather than being reached
 * from separate places. Installing is only ever possible from an inspection
 * that already exists.
 *
 * Lifted from App.vue unchanged.
 */

import { invoke } from '@tauri-apps/api/core'
import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import type { CoreClient } from '../api'
import type { AttachmentKind } from '../shellContext'
import type {
  AgentSkillDraft,
  AgentSkillInspection,
  AgentSkillInstallation,
  CollaborationSnapshot,
  CoreReadyPayload,
} from '../protocol'

export function useAgentSkills(client: CoreClient,
  ready: Ref<CoreReadyPayload | null>,
  activeContext: Ref<NonNullable<CollaborationSnapshot['active']>>,
  refreshSkills: () => Promise<void> | void,) {
  const agentSkillSource = ref('')

  const agentSkillInspection = ref<AgentSkillInspection | null>(null)

  const agentSkillBusy = ref(false)

  const agentSkillNotice = ref('')

  const agentSkillScope = ref<'global' | 'project' | 'character'>('project')

  function agentSkillScopeId() {
    if (agentSkillScope.value === 'project') return activeContext.value.project_id || ''
    if (agentSkillScope.value === 'character') return ready.value?.character?.id || ''
    return ''
  }

  const installedAgentSkills = ref<AgentSkillInstallation[]>([])

  const agentSkillDrafts = ref<AgentSkillDraft[]>([])

  function agentSkillDraftSteps(draft: AgentSkillDraft) {
    const body = String(draft.payload?.instructions || draft.payload?.steps || '').trim()
    return body ? body.split('\n').filter((line) => line.trim()).slice(0, 6) : []
  }

  function agentSkillScopeLabel(scope: string) {
    return ({ global: '全局', project: '当前项目', character: '当前角色' } as Record<string, string>)[scope] || scope
  }

  async function chooseAgentSkillSource(kind: AttachmentKind) {
    try {
      const selected = await invoke<string[]>('pick_attachments', { kind })
      const source = selected?.[0]
      if (!source) return
      agentSkillSource.value = source
      agentSkillInspection.value = null
      agentSkillNotice.value = ''
      await inspectAgentSkill()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || '')
      if (message && !/cancel/i.test(message)) agentSkillNotice.value = message
    }
  }

  async function inspectAgentSkill() {
    const source = agentSkillSource.value.trim()
    if (!source || agentSkillBusy.value) return
    agentSkillBusy.value = true
    agentSkillNotice.value = ''
    try {
      const result = await client.agentSkillInspect(source) as { ok?: boolean; inspection?: AgentSkillInspection; error?: string; message?: string }
      agentSkillInspection.value = result.inspection || null
      agentSkillNotice.value = result.ok ? '检查完成。安装时会再次校验文件哈希。' : result.message || result.error || 'Skill 检查失败'
    } catch (error) {
      agentSkillInspection.value = null
      agentSkillNotice.value = error instanceof Error ? error.message : 'Skill 检查失败'
    } finally {
      agentSkillBusy.value = false
    }
  }

  async function installInspectedAgentSkill() {
    const inspection = agentSkillInspection.value
    if (!inspection || agentSkillBusy.value) return
    agentSkillBusy.value = true
    try {
      const result = await client.agentSkillInstall(
        agentSkillSource.value.trim(),
        agentSkillScope.value,
        agentSkillScopeId(),
        inspection.digest,
      ) as { ok?: boolean; error?: string; message?: string }
      agentSkillNotice.value = result.ok ? `${inspection.name} 已安装，脚本仍保持显式调用。` : result.message || result.error || 'Skill 安装失败'
      if (result.ok) {
        agentSkillSource.value = ''
        agentSkillInspection.value = null
        await refreshSkills()
      }
    } catch (error) {
      agentSkillNotice.value = error instanceof Error ? error.message : 'Skill 安装失败'
    } finally {
      agentSkillBusy.value = false
    }
  }

  async function updateAgentSkill(skill: AgentSkillInstallation) {
    agentSkillBusy.value = true
    try {
      // An update re-reads the source, so the first call only reports what was
      // read. Nothing is installed until the user confirms that exact digest.
      const review = await client.agentSkillUpdate(skill.id) as {
        ok?: boolean
        error?: string
        message?: string
        changed?: boolean
        inspection?: AgentSkillInspection
      }
      if (review.ok) {
        agentSkillNotice.value = `${skill.name} 已更新。`
        await refreshSkills()
        return
      }
      if (review.error !== 'update_review_required' || !review.inspection?.digest) {
        agentSkillNotice.value = review.message || review.error || '更新失败'
        return
      }
      if (!review.changed) {
        agentSkillNotice.value = `${skill.name} 来源内容没有变化，已保持当前版本。`
        return
      }
      const inspection = review.inspection
      const confirmed = window.confirm(
        [
          `${skill.name} 的来源有新内容。`,
          `版本 ${inspection.version}`,
          inspection.code_bearing ? '包含脚本，运行前仍需单独审核。' : '不包含脚本。',
          '确认后才会安装这次读到的内容。',
        ].join('\n'),
      )
      if (!confirmed) {
        agentSkillNotice.value = '已取消更新，仍在使用当前版本。'
        return
      }
      const applied = await client.agentSkillUpdate(skill.id, inspection.digest) as { ok?: boolean; error?: string; message?: string }
      agentSkillNotice.value = applied.ok ? `${skill.name} 已更新到确认过的版本。` : applied.message || applied.error || '更新失败'
      await refreshSkills()
    } finally {
      agentSkillBusy.value = false
    }
  }

  async function uninstallAgentSkill(skill: AgentSkillInstallation) {
    if (!window.confirm(`卸载 ${skill.name}？运行记录会保留，安装文件和注册信息会清理。`)) return
    const result = await client.agentSkillUninstall(skill.id, true) as { ok?: boolean; error?: string }
    agentSkillNotice.value = result.ok ? `${skill.name} 已卸载，没有遗留安装文件。` : result.error || '卸载失败'
    await refreshSkills()
  }

  async function toggleAgentSkill(skill: AgentSkillInstallation) {
    await client.agentSkillEnable(skill.id, !skill.enabled)
    await refreshSkills()
  }

  async function approveAgentSkillDraft(draft: AgentSkillDraft) {
    const steps = agentSkillDraftSteps(draft)
    const review = `${draft.name}\n\n${steps.join('\n') || '（草稿没有记录步骤）'}\n\n安装为 ${agentSkillScopeLabel(agentSkillScope.value)} Skill？安装后仍不会隐式运行脚本。`
    if (!window.confirm(review)) return
    agentSkillBusy.value = true
    try {
      const result = await client.agentSkillDraftApprove(draft.id, agentSkillScope.value, agentSkillScopeId()) as { ok?: boolean; error?: string; message?: string }
      agentSkillNotice.value = result.ok ? `${draft.name} 已从草稿安装。` : result.message || result.error || '草稿安装失败'
      await refreshSkills()
    } catch (error) {
      agentSkillNotice.value = error instanceof Error ? error.message : '草稿安装失败'
    } finally {
      agentSkillBusy.value = false
    }
  }

  async function rejectAgentSkillDraft(draft: AgentSkillDraft) {
    const result = await client.agentSkillDraftReject(draft.id) as { ok?: boolean; error?: string }
    agentSkillNotice.value = result.ok ? `${draft.name} 草稿已丢弃。` : result.error || '草稿丢弃失败'
    await refreshSkills()
  }
  return {
    agentSkillSource,
    agentSkillInspection,
    agentSkillBusy,
    agentSkillNotice,
    agentSkillScope,
    agentSkillScopeId,
    installedAgentSkills,
    agentSkillDrafts,
    agentSkillDraftSteps,
    agentSkillScopeLabel,
    chooseAgentSkillSource,
    inspectAgentSkill,
    installInspectedAgentSkill,
    updateAgentSkill,
    uninstallAgentSkill,
    toggleAgentSkill,
    approveAgentSkillDraft,
    rejectAgentSkillDraft,
  }
}
