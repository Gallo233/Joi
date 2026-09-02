/**
 * How long a mood stays on the character's face.
 *
 * The shell hands a runtime the most recent expression event and keeps handing
 * it the same one until another arrives, which can be a very long time. Held
 * literally, one cheerful line at startup leaves the character wearing that
 * expression for the rest of the session — and a face frozen in a reaction
 * stops reading as a reaction at all.
 *
 * So an expression is treated as something that happens and then passes: full
 * strength for a moment, then released back to a neutral, attentive resting
 * face. Both the VRM and Live2D runtimes use this same envelope so a character
 * does not settle differently depending on which format it happens to be.
 */

export const EMOTION_HOLD_MS = 2600
export const EMOTION_FADE_MS = 2600

/**
 * Weight of the current mood, from 1 at its peak to 0 once it has passed.
 *
 * `startedAt` and `now` share whatever clock the caller uses; both runtimes
 * pass `performance.now()`.
 */
export function emotionWeight(startedAt: number, now: number): number {
  const age = now - startedAt
  if (!Number.isFinite(age) || age <= EMOTION_HOLD_MS) return age < 0 ? 0 : 1
  const released = (age - EMOTION_HOLD_MS) / EMOTION_FADE_MS
  if (released >= 1) return 0
  return 1 - released
}

/** True once the mood has fully released and the face is resting. */
export function emotionSettled(startedAt: number, now: number): boolean {
  return emotionWeight(startedAt, now) <= 0
}

/** Blend one numeric pose channel from its resting value toward the mood. */
export function blendTowardMood(resting: number, mood: number, weight: number): number {
  return resting + (mood - resting) * weight
}
