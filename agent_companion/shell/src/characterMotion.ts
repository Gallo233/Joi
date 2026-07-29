export const CHARACTER_MOTIONS = ['idle', 'greet', 'talk', 'happy', 'finger_gun', 'dance'] as const

export type CharacterMotionName = typeof CHARACTER_MOTIONS[number]

export interface CharacterMotionRequest {
  motion: CharacterMotionName
  eventKey: string
  durationMs?: number
  loop?: boolean
  intensity?: number
}

export interface CharacterMotionMapping {
  id?: string
  name?: string
  motion?: string
  aliases?: string[]
  motion_group?: string
  motion_index?: number | string
  duration_ms?: number
  loop?: boolean
  intensity?: number
}

export interface ResolvedCharacterMotion {
  name: CharacterMotionName
  eventKey: string
  durationMs: number
  loop: boolean
  intensity: number
  mapping?: CharacterMotionMapping
}

const DEFAULT_DURATION_MS: Record<CharacterMotionName, number> = {
  idle: 0,
  greet: 2200,
  talk: 1800,
  happy: 2200,
  finger_gun: 1900,
  dance: 6000,
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

export function normalizeCharacterMotion(value: unknown): CharacterMotionName | null {
  const normalized = String(value || '').trim().toLocaleLowerCase().replace(/[\s-]+/g, '_')
  return CHARACTER_MOTIONS.includes(normalized as CharacterMotionName) ? normalized as CharacterMotionName : null
}

export function resolveCharacterMotion(
  request: CharacterMotionRequest,
  mappings: CharacterMotionMapping[] = [],
): ResolvedCharacterMotion {
  const name = normalizeCharacterMotion(request.motion) || 'idle'
  const mapping = mappings.find((entry) => {
    const candidates = [entry.id, entry.name, entry.motion, ...(entry.aliases || [])]
    return candidates.some((candidate) => normalizeCharacterMotion(candidate) === name)
  })
  const durationCandidate = Number(request.durationMs ?? mapping?.duration_ms ?? DEFAULT_DURATION_MS[name])
  const durationMs = name === 'idle'
    ? Math.max(0, Math.min(12_000, Number.isFinite(durationCandidate) ? durationCandidate : 0))
    : clamp(Number.isFinite(durationCandidate) ? durationCandidate : DEFAULT_DURATION_MS[name], 400, 12_000)
  const intensityCandidate = Number(request.intensity ?? mapping?.intensity ?? 0.8)
  return {
    name,
    eventKey: String(request.eventKey || `${name}:${performance.now()}`),
    durationMs,
    loop: Boolean(request.loop ?? mapping?.loop ?? name === 'idle'),
    intensity: clamp(Number.isFinite(intensityCandidate) ? intensityCandidate : 0.8, 0.25, 1),
    mapping,
  }
}

export function motionEnvelope(motion: ResolvedCharacterMotion, startedAt: number, now: number, fadeMs = 180) {
  const elapsed = Math.max(0, now - startedAt)
  const fadeIn = clamp(elapsed / Math.max(1, fadeMs), 0, 1)
  if (motion.loop || motion.durationMs <= 0) return fadeIn
  const fadeOut = clamp((motion.durationMs - elapsed) / Math.max(1, fadeMs), 0, 1)
  return Math.min(fadeIn, fadeOut)
}

export function motionExpired(motion: ResolvedCharacterMotion, startedAt: number, now: number) {
  return !motion.loop && motion.durationMs > 0 && now - startedAt >= motion.durationMs
}
