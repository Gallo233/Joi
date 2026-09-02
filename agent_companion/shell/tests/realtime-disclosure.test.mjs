/**
 * The microphone disclosure has to describe this machine, not an assumption.
 *
 * The Minecraft disclosure promised that a captured game frame "只在本机做文字
 * 识别" and that "原图不会上传". That is true only when no vision model is
 * configured: with one, MinecraftScreenCache sends the frame itself to it for
 * the scene summary. A consent screen that understates where a screenshot goes
 * is the one defect in this flow the user cannot discover for themselves, so it
 * is worth a test that fails loudly if the wording drifts back.
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

/** The body of screenEvidenceDisclosure(), which builds the screen paragraph. */
function disclosureFunction() {
  const start = source.indexOf('function screenEvidenceDisclosure()')
  assert.notEqual(start, -1, 'screenEvidenceDisclosure must exist')
  const end = source.indexOf('\n}', start)
  assert.notEqual(end, -1)
  return source.slice(start, end)
}

/** Everything the function can return for one screen_evidence route. */
function branchFor(route) {
  const body = disclosureFunction()
  const start = body.indexOf(`'${route}'`)
  assert.notEqual(start, -1, `screenEvidenceDisclosure must handle ${route}`)
  const next = body.slice(start + route.length).search(/route ===|\n\s*return ''/)
  return body.slice(start, next === -1 ? undefined : start + route.length + next)
}

test('a configured vision model is disclosed as the frame leaving this machine', () => {
  const branch = branchFor('vision_model')
  assert.match(branch, /视觉模型/)
  assert.match(branch, /截图本身会发送/)
  // The two promises that are false on this route must not appear on it.
  assert.doesNotMatch(branch, /原图不会上传/)
  assert.doesNotMatch(branch, /只在本机做文字识别/)
})

test('the local-only promise is only made when nothing uploads the frame', () => {
  const branch = branchFor('local_ocr')
  assert.match(branch, /只在本机做文字识别/)
  assert.match(branch, /原图不会上传/)
  assert.match(branch, /没有配置视觉模型/)
})

test('the screen paragraph comes from Core status, never a hardcoded promise', () => {
  assert.match(disclosureFunction(), /ready\.value\?\.realtime_voice\?\.screen_evidence/)
  // No other copy of the old unconditional sentence survives elsewhere.
  const occurrences = source.split('截图只在本机做文字识别').length - 1
  assert.equal(occurrences, 1, 'the local-only sentence belongs to one guarded branch')
})
