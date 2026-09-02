import { isTauri } from '@tauri-apps/api/core'
import type { AttachmentKind } from '../shellContext'
import { tauriAssetUrl, tauriCoreConnection, tauriPickAttachments, tauriSetWindowControlsVisible } from './tauri'
import type { CoreConnectionInfo } from './types'
import { webCoreConnection, webShellContext } from './web'

export type { CompactPosition, CompactSize, CoreConnectionInfo, ShellMode, WebShellContext } from './types'
export { postCompactPosition, postCompactSize, postEmbedMessage, webShellContext } from './web'

export const isDesktopRuntime = isTauri()
export const shellWebContext = webShellContext()

export async function coreConnectionInfo(restart = false): Promise<CoreConnectionInfo> {
  return isDesktopRuntime ? tauriCoreConnection(restart) : webCoreConnection()
}

export function assetUrl(source: string) {
  const value = String(source || '').trim()
  if (!value) return ''
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value
  return isDesktopRuntime ? tauriAssetUrl(value) : ''
}

export async function pickAttachments(kind: AttachmentKind) {
  return isDesktopRuntime ? tauriPickAttachments(kind) : []
}

export async function setMacWindowControlsVisible(visible: boolean) {
  if (isDesktopRuntime) await tauriSetWindowControlsVisible(visible)
}

