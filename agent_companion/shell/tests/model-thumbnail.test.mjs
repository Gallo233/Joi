import assert from 'node:assert/strict'
import test from 'node:test'

import { canRenderThumbnail } from '../src/character/stage.ts'

// The renderers need a DOM and WebGL, which node has neither of, so what is
// worth proving here is the rule that decides whether one is mounted at all.

test('a format with no licensed runtime is never mounted for a preview', () => {
  assert.equal(canRenderThumbnail('spine', 'model.json'), false)
})

test('a format that needs a model file is not mounted without one', () => {
  for (const format of ['live2d', 'vrm', 'mmd', 'tachie', 'static']) {
    assert.equal(canRenderThumbnail(format, ''), false, format)
    assert.equal(canRenderThumbnail(format, 'model.bin'), true, format)
  }
})

test('the built-in renderer previews with no model file at all', () => {
  assert.equal(canRenderThumbnail('procedural3d', ''), true)
})
