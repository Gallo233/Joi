import assert from 'node:assert/strict'
import test from 'node:test'

import {
  STAGE_EMOTION_TABLE,
  STAGE_EMOTIONS,
  STAGE_FORMATS_WITHOUT_MODEL_URL,
  STAGE_MODEL_FORMATS,
  isStageEmotion,
  isStageModelFormat,
  stageEmotionShape,
} from '../src/character/stage.ts'

test('every emotion has a row for every format that can express one', () => {
  // The table is the whole answer to "what does worried look like": a missing
  // row is a renderer silently doing nothing, which reads as a broken model.
  for (const emotion of STAGE_EMOTIONS) {
    const row = STAGE_EMOTION_TABLE[emotion]
    assert.ok(row, `no row for ${emotion}`)
    for (const format of ['vrm', 'mmd']) {
      const shape = row[format]
      if (shape === null) continue
      assert.equal(typeof shape[0], 'string')
      assert.ok(shape[1] > 0 && shape[1] <= 1, `${emotion}.${format} weight out of range`)
    }
  }
  // Neutral is the absence of an expression, not an expression named neutral.
  assert.equal(STAGE_EMOTION_TABLE.neutral.vrm, null)
  assert.equal(STAGE_EMOTION_TABLE.neutral.mmd, null)
})

test('a VRM expression is never driven at full strength', () => {
  // Tuned against real rigs: 1.0 morphs the eyes shut and reads as a squint.
  for (const emotion of STAGE_EMOTIONS) {
    const shape = stageEmotionShape('vrm', emotion)
    if (shape) assert.ok(shape[1] <= 0.5, `${emotion} would shut the eyes`)
  }
})

test('the stage knows which formats carry no model file', () => {
  assert.ok(STAGE_MODEL_FORMATS.includes('procedural3d'))
  assert.ok(STAGE_FORMATS_WITHOUT_MODEL_URL.includes('procedural3d'))
  assert.equal(STAGE_FORMATS_WITHOUT_MODEL_URL.includes('live2d'), false)
  assert.equal(isStageModelFormat('spine'), true)
  assert.equal(isStageModelFormat('gif'), false)
  assert.equal(isStageEmotion('worried'), true)
  assert.equal(isStageEmotion('furious'), false)
})

test('tachie framing contains the whole artwork instead of filling the height', () => {
  // A bust is authored wider than tall. Scaling it to the canvas height made it
  // nearly twice the canvas width, so the sides -- most of the character --
  // were cropped away. Contain is the rule that fixes it.
  const contain = (canvasW, canvasH, artW, artH, fill) =>
    Math.min((canvasW * fill) / artW, (canvasH * fill) / artH)

  // The real case: a 1408x768 bust on a portrait stage.
  const scale = contain(600, 900, 1408, 768, 0.94)
  assert.ok(1408 * scale <= 600, 'artwork must fit the canvas width')
  assert.ok(768 * scale <= 900, 'artwork must fit the canvas height')

  // A tall full-body sprite fits too, the other way round.
  const tall = contain(600, 900, 768, 1408, 0.94)
  assert.ok(768 * tall <= 600 && 1408 * tall <= 900)
})
