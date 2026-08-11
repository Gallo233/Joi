import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMOTION_FADE_MS,
  EMOTION_HOLD_MS,
  blendTowardMood,
  emotionSettled,
  emotionWeight,
} from '../src/characterExpression.ts'

test('a mood holds at full strength before it starts releasing', () => {
  assert.equal(emotionWeight(0, 0), 1)
  assert.equal(emotionWeight(0, EMOTION_HOLD_MS - 1), 1)
  assert.equal(emotionWeight(0, EMOTION_HOLD_MS), 1)
})

test('a mood releases to nothing rather than lingering forever', () => {
  const midway = emotionWeight(0, EMOTION_HOLD_MS + EMOTION_FADE_MS / 2)
  assert.ok(midway > 0 && midway < 1, `expected a partial release, got ${midway}`)

  assert.equal(emotionWeight(0, EMOTION_HOLD_MS + EMOTION_FADE_MS), 0)
  // The face must stay settled, not wrap around or drift negative.
  assert.equal(emotionWeight(0, EMOTION_HOLD_MS + EMOTION_FADE_MS * 10), 0)
  assert.equal(emotionWeight(0, Number.MAX_SAFE_INTEGER), 0)
})

test('release is monotonic, so a face never re-tightens on its own', () => {
  let previous = 1
  for (let now = 0; now <= EMOTION_HOLD_MS + EMOTION_FADE_MS + 500; now += 100) {
    const weight = emotionWeight(0, now)
    assert.ok(weight <= previous, `weight rose at ${now}ms: ${previous} -> ${weight}`)
    assert.ok(weight >= 0 && weight <= 1)
    previous = weight
  }
})

test('settled reports the resting face only once the mood is fully released', () => {
  assert.equal(emotionSettled(0, EMOTION_HOLD_MS), false)
  assert.equal(emotionSettled(0, EMOTION_HOLD_MS + EMOTION_FADE_MS / 2), false)
  assert.equal(emotionSettled(0, EMOTION_HOLD_MS + EMOTION_FADE_MS), true)
})

test('a clock that has not reached the start yet shows no mood', () => {
  assert.equal(emotionWeight(1000, 0), 0)
})

test('blending walks a pose channel from resting to mood and back', () => {
  assert.equal(blendTowardMood(1, 0.88, 1), 0.88)
  assert.equal(blendTowardMood(1, 0.88, 0), 1)
  assert.ok(Math.abs(blendTowardMood(1, 0.88, 0.5) - 0.94) < 1e-9)
  // Negative mood channels (a worried brow) blend the same way.
  assert.ok(Math.abs(blendTowardMood(0, -0.2, 0.5) - -0.1) < 1e-9)
})
