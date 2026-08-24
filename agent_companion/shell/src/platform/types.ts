export interface CoreConnectionInfo {
  url: string
  token: string
  protocol_version: number
  instance_id: string
  status: string
  error?: string | null
}

export type ShellMode = 'full' | 'compact'

export interface WebShellContext {
  coreUrl: string
  token: string
  guestMode: boolean
  mode: ShellMode
  parentOrigin: string
}

export interface CompactSize {
  width: number
  height: number
}

export interface CompactPosition {
  x: number
  y: number
}

