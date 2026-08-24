import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import type { AttachmentKind } from '../shellContext'
import type { CoreConnectionInfo } from './types'

export async function tauriCoreConnection(restart = false) {
  if (restart) await invoke<CoreConnectionInfo>('restart_core')
  return invoke<CoreConnectionInfo>('core_connection_info')
}

export function tauriAssetUrl(source: string) {
  return convertFileSrc(source)
}

export function tauriPickAttachments(kind: AttachmentKind) {
  return invoke<string[]>('pick_attachments', { kind })
}

export async function tauriSetWindowControlsVisible(visible: boolean) {
  await invoke('set_macos_window_controls_visible', { visible })
}

