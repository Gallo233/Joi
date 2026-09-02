/**
 * Narrowing helpers for values that arrive from Core.
 *
 * Core's payloads are typed at the protocol boundary, but several call sites
 * read fields that are optional or version-dependent, and reach for a cast to
 * get at them. These two keep that cast in one place and make it total: an
 * unexpected shape becomes an empty record rather than a crash halfway through
 * rendering a panel.
 *
 * Lifted out of App.vue so composables split from it can share them instead of
 * each growing a copy.
 */

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

export function stringValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}
