import type { CompactPosition, CompactSize, CoreConnectionInfo, ShellMode, WebShellContext } from './types'

const WS_PROTOCOLS = new Set(['ws:', 'wss:'])

function queryValue(name: string) {
  if (typeof window === 'undefined') return ''
  return new URLSearchParams(window.location.search).get(name)?.trim() || ''
}

function safeWebSocketUrl(value: string) {
  try {
    const url = new URL(value)
    return WS_PROTOCOLS.has(url.protocol) && !url.username && !url.password ? url.toString() : ''
  } catch {
    return ''
  }
}

function safeOrigin(value: string) {
  try {
    const url = new URL(value)
    return url.origin === 'null' ? '' : url.origin
  } catch {
    return ''
  }
}

function inferredParentOrigin() {
  const explicit = safeOrigin(queryValue('parent_origin'))
  if (explicit) return explicit
  if (typeof document === 'undefined' || !document.referrer) return ''
  return safeOrigin(document.referrer)
}

export function webShellContext(): WebShellContext {
  const configured = safeWebSocketUrl(queryValue('core'))
    || safeWebSocketUrl(import.meta.env.VITE_JOI_CORE_URL || '')
    || 'ws://127.0.0.1:8765/'
  const requestedMode = queryValue('mode')
  const mode: ShellMode = requestedMode === 'compact' ? 'compact' : 'full'
  return {
    coreUrl: configured,
    token: queryValue('token'),
    guestMode: queryValue('guest') === '1' || import.meta.env.VITE_JOI_GUEST_MODE === '1',
    mode,
    parentOrigin: inferredParentOrigin(),
  }
}

export function webCoreConnection(): CoreConnectionInfo {
  const context = webShellContext()
  return {
    url: context.coreUrl,
    token: context.token,
    protocol_version: 1,
    instance_id: '',
    status: 'external',
  }
}

let missingParentOriginReported = false

export function postEmbedMessage(type: string, payload: Record<string, unknown> = {}) {
  if (typeof window === 'undefined' || window.parent === window) return
  const targetOrigin = webShellContext().parentOrigin
  if (!targetOrigin) {
    // Never widen to '*': that would hand this session's geometry and cabin
    // traffic to whatever page framed it. But failing silently is worse than
    // the misconfiguration -- the pet simply stops moving with no clue why --
    // so say it once.
    if (!missingParentOriginReported) {
      missingParentOriginReported = true
      console.warn('[joi] embedded without a known parent origin; pass ?parent_origin= to enable host messaging')
    }
    return
  }
  window.parent.postMessage({ source: 'joi-shell', type, ...payload }, targetOrigin)
}

export function postCompactSize(size: CompactSize) {
  postEmbedMessage('joi.resize', {
    width: Math.max(1, Math.round(size.width)),
    height: Math.max(1, Math.round(size.height)),
  })
}

export function postCompactPosition(position: CompactPosition | null) {
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return
  postEmbedMessage('joi.position.restore', { x: position.x, y: position.y })
}

