export type SettingsTabId =
  | 'execution'
  | 'runtime'
  | 'memory'
  | 'skills'
  | 'appearance'
  | 'developer'

interface SettingsTab {
  id: SettingsTabId
  label: string
  icon: string
  subtitle: string
}

/**
 * Every settings category the shell can actually open.
 *
 * This list used to declare eighteen. Twelve of them -- instructions, media,
 * language, notifications, pets, connectors, mcp_servers, external_mcp,
 * privacy, design_review, design_system, about -- were absent from
 * `settingsGroupDefinitions`, which is what builds the navigation, so nothing
 * could route to them. They rendered a "coming soon" placeholder that no user
 * could reach, and each carried an icon import and a subtitle that read like a
 * shipped feature. Removing them is not a reduction in scope: it makes the
 * list say what the product does.
 */
export const settingsTabs: SettingsTab[] = [
  { id: 'execution', label: '基础设置', icon: 'settings', subtitle: '选择本机 Agent CLI、模型和执行方式。' },
  { id: 'runtime', label: '模型与运行时', icon: 'cpu', subtitle: '管理模型提供商、语音和本地运行参数。' },
  { id: 'memory', label: '记忆', icon: 'brain', subtitle: '管理长期记忆和待确认候选。' },
  { id: 'skills', label: '技能', icon: 'sparkles', subtitle: '导入、审核和管理可复用工作流。' },
  { id: 'appearance', label: '外观', icon: 'palette', subtitle: '调整 Joi 外观和微缩模式装扮。' },
  { id: 'developer', label: '开发者', icon: 'code', subtitle: '查看审计、背景上下文和事件流。' },
]

const settingsById = new Map(settingsTabs.map((tab) => [tab.id, tab]))

export function settingsTitle(tab: SettingsTabId) {
  return settingsById.get(tab)?.label || '设置'
}

export function settingsSubtitle(tab: SettingsTabId) {
  return settingsById.get(tab)?.subtitle || ''
}
