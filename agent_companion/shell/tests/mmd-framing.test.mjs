/**
 * The MMD stage frames the shot the flag actually asks for.
 *
 * `setCompact` receives `isCompactMode || characterFullBody`, which means "show
 * the whole body" -- the VRM stage has always read it that way. The MMD stage
 * read it as its opposite, so the desk pet showed a cropped head, and so did
 * the desktop stage until the full-body toggle was switched *off*. These hold
 * the direction, and the width correction that keeps twin tails in a narrow
 * frame.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { MODEL_FRACTION, NARROW_ASPECT, mmdFraming } from '../src/mmd/framing.ts'

const MODEL = { height: 1.6, headY: 1.6 * 0.92, zoom: 1, aspect: 1, fov: 28 }

/** Half the visible height at the model's distance, from the camera's own FOV. */
const halfSpan = ({ distance }, fov = MODEL.fov) => distance * Math.tan((fov * Math.PI) / 360)

test('full body keeps the feet and the top of the head inside the frame', () => {
  const framing = mmdFraming({ ...MODEL, fullBody: true })
  const half = halfSpan(framing)
  assert.ok(framing.target - half <= 0, 'the ground must be in frame')
  assert.ok(framing.target + half >= MODEL.height, 'the top of the head must be in frame')
})

test('a bust is the other framing, not the full-body one', () => {
  const body = mmdFraming({ ...MODEL, fullBody: true })
  const bust = mmdFraming({ ...MODEL, fullBody: false })
  assert.ok(bust.distance < body.distance, 'a bust is a closer shot than a full body')
  assert.ok(bust.target > body.target, 'a bust looks at the head, a full body at the middle')
  // The regression: reading the flag backwards made the *full body* the close
  // shot, which is what cropped the desk pet.
  assert.notEqual(body.target, bust.target)
})

test('the model fills the intended share of the frame', () => {
  const framing = mmdFraming({ ...MODEL, fullBody: true })
  const visible = halfSpan(framing) * 2
  assert.ok(Math.abs(MODEL.height / visible - MODEL_FRACTION) < 0.001)
})

test('a tall narrow canvas pulls back so the silhouette is not cropped at the sides', () => {
  const square = mmdFraming({ ...MODEL, fullBody: true, aspect: 1 })
  const pet = mmdFraming({ ...MODEL, fullBody: true, aspect: 0.5 })
  assert.ok(pet.distance > square.distance, 'a narrow frame needs more distance')
  assert.ok(Math.abs(pet.distance / square.distance - NARROW_ASPECT / 0.5) < 1e-9)
})

test('a canvas wider than the threshold is not pulled back', () => {
  const wide = mmdFraming({ ...MODEL, fullBody: true, aspect: 1.4 })
  const atThreshold = mmdFraming({ ...MODEL, fullBody: true, aspect: NARROW_ASPECT })
  assert.equal(wide.distance, atThreshold.distance)
})

test('zoom moves the camera and degenerate input cannot divide by zero', () => {
  const near = mmdFraming({ ...MODEL, fullBody: true, zoom: 2 })
  const far = mmdFraming({ ...MODEL, fullBody: true, zoom: 0.5 })
  assert.ok(near.distance < far.distance)
  for (const bad of [{ zoom: 0 }, { zoom: -1 }, { height: 0 }, { aspect: 0 }]) {
    const framing = mmdFraming({ ...MODEL, fullBody: true, ...bad })
    assert.ok(Number.isFinite(framing.distance) && framing.distance > 0, JSON.stringify(bad))
  }
})
