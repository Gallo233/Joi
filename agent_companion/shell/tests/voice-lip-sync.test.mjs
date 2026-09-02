import assert from 'node:assert/strict'
import test from 'node:test'

import {
  attachLipSync,
  decodePcm16Base64,
  detachLipSync,
  mouthSignal,
  VRM_MOUTH_FLOOR,
  VRM_MOUTH_SCALE,
  voiceDrivenMouthLevel,
} from '../src/voiceLipSync.ts'

/**
 * Node has no Web Audio, so these cover the decision the runtimes depend on:
 * when the analyser is not producing anything trustworthy, `live` must be
 * false so every renderer keeps the mouth closed.
 */

test('with no audio attached the mouth is closed and not live', () => {
  detachLipSync()
  const signal = mouthSignal(1000)
  assert.equal(signal.level, 0)
  assert.equal(signal.live, false)
})

test('an environment without Web Audio degrades instead of throwing', () => {
  const original = globalThis.window
  globalThis.window = {}
  try {
    // Must not throw: a shell without an AudioContext still has to play audio.
    assert.doesNotThrow(() => attachLipSync({ paused: false, ended: false }))
    assert.equal(mouthSignal(0).live, false)
  } finally {
    globalThis.window = original
  }
})

test('detaching resets the mouth so barge-in cannot leave it hanging open', () => {
  detachLipSync()
  const after = mouthSignal(5000)
  assert.equal(after.level, 0)
  assert.equal(after.live, false)
})

test('text-only or failed audio cannot open the mouth', () => {
  assert.equal(voiceDrivenMouthLevel({ level: 1, live: false, visemes: { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 } }), 0)
})

test('a live audio signal maps into the configured mouth range', () => {
  const level = voiceDrivenMouthLevel(
    { level: 0.5, live: true, visemes: { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 } },
    0.1,
    0.72,
  )
  assert.ok(Math.abs(level - 0.46) < 1e-9)
})

test('VRM uses a visibly stronger audio envelope without weakening the live gate', () => {
  const live = { level: 0.5, live: true, visemes: { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 } }
  const silent = { ...live, live: false }
  assert.ok(voiceDrivenMouthLevel(live, VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE) > 0.5)
  assert.equal(voiceDrivenMouthLevel(silent, VRM_MOUTH_FLOOR, VRM_MOUTH_SCALE), 0)
  assert.ok(VRM_MOUTH_FLOOR + VRM_MOUTH_SCALE <= 1)
})

test('streamed PCM16 is decoded as signed little-endian audio', () => {
  const pcm = Buffer.from([0xff, 0x7f, 0x00, 0x80, 0x00, 0x00]).toString('base64')
  const decoded = decodePcm16Base64(pcm)
  assert.equal(decoded.length, 3)
  assert.equal(decoded[0], 1)
  assert.equal(decoded[1], -1)
  assert.equal(decoded[2], 0)
})
