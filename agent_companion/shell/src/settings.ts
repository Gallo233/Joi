export type SettingsTabId =
  | 'execution'
  | 'runtime'
  | 'instructions'
  | 'memory'
  | 'media'
  | 'skills'
  | 'external_mcp'
  | 'connectors'
  | 'mcp_servers'
  | 'language'
  | 'appearance'
  | 'design_review'
  | 'notifications'
  | 'pets'
  | 'design_system'
  | 'privacy'
  | 'about'
  | 'developer'

interface SettingsTab {
  id: SettingsTabId
  label: string
  icon: string
  subtitle: string
}

export const settingsTabs: SettingsTab[] = [
  { id: 'execution', label: '基础设置', icon: 'settings', subtitle: '选择本机 Agent CLI、模型和执行方式。' },
  { id: 'runtime', label: '模型与运行时', icon: 'cpu', subtitle: '管理模型提供商、语音和本地运行参数。' },
  { id: 'instructions', label: '指令与规则', icon: 'file-text', subtitle: '管理 Joi 的系统指令和项目规则。' },
  { id: 'memory', label: '记忆', icon: 'brain', subtitle: '管理长期记忆和待确认候选。' },
  { id: 'skills', label: '技能', icon: 'sparkles', subtitle: '导入、审核和管理可复用工作流。' },
  { id: 'media', label: '语音与媒体', icon: 'volume', subtitle: '配置语音、图像和视觉提供商。' },
  { id: 'appearance', label: '外观', icon: 'palette', subtitle: '调整 Joi 外观和微缩模式装扮。' },
  { id: 'language', label: '界面语言', icon: 'languages', subtitle: '选择界面显示语言。' },
  { id: 'notifications', label: '通知', icon: 'bell', subtitle: '设置任务、审批和语音通知。' },
  { id: 'pets', label: '角色与宠物', icon: 'bot', subtitle: '管理桌面角色、宠物和互动表现。' },
  { id: 'connectors', label: '连接器', icon: 'plug', subtitle: '管理本地与云端连接器。' },
  { id: 'mcp_servers', label: 'MCP 服务器', icon: 'server', subtitle: '查看 MCP 服务器发现状态。' },
  { id: 'external_mcp', label: '外部 MCP', icon: 'cable', subtitle: '管理外部 MCP 能力入口。' },
  { id: 'privacy', label: '隐私与数据', icon: 'shield', subtitle: '查看本地数据、权限和隐私边界。' },
  { id: 'design_review', label: '设计评审', icon: 'message', subtitle: '管理设计评审相关工作流。' },
  { id: 'design_system', label: '设计系统', icon: 'code', subtitle: '查看界面设计令牌。' },
  { id: 'developer', label: '开发者', icon: 'code', subtitle: '查看审计、背景上下文和事件流。' },
  { id: 'about', label: '关于 Joi', icon: 'info', subtitle: '查看 Joi 版本和运行环境。' },
]

const settingsById = new Map(settingsTabs.map((tab) => [tab.id, tab]))

export function settingsTitle(tab: SettingsTabId) {
  return settingsById.get(tab)?.label || '设置'
}

export function settingsSubtitle(tab: SettingsTabId) {
  return settingsById.get(tab)?.subtitle || ''
}
