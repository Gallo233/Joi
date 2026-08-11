/**
 * The character stage collapses, and collapsing it never costs the user a
 * decision they were supposed to make.
 *
 * The stage held roughly 60% of the window whether or not there was anything
 * in it. With Core unreachable, or a character package that ships no model and
 * no sprite, that was more than half the window painted blank while the
 * conversation was squeezed into what was left.
 *
 * Collapsing hides a lot of the stage, which is the point -- but a hide list is
 * exactly the kind of thing that grows by one line at a time until something
 * load-bearing is on it. These tests draw the line around the parts that are
 * not decoration.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { allElements, cssRules, declarationValue, scriptSource, withinClass } from './helpers/shellSource.mjs'

const collapsedRules = () =>
  cssRules().filter((rule) => rule.selector.includes('.stage-collapsed'))

test('collapsing is a preference that survives a relaunch', () => {
  // A layout choice the user makes deliberately should not be undone every
  // launch; re-setting the same preference daily teaches them it is broken.
  assert.match(
    scriptSource(),
    /stageCollapsed = usePersistentRef\(/,
    'the collapse state must be persisted, not a plain ref',
  )
})

test('an empty stage collapses on its own', () => {
  // The original defect was not "the stage is too wide" -- it was "the stage is
  // wide even when there is nothing in it".
  const source = scriptSource()
  assert.match(
    source,
    /stageHasCharacter = computed\(\(\) => Boolean\(live2DModelUrl\.value \|\| characterImageSrc\.value\)\)/,
    'stageHasCharacter must be derived from whether there is anything to draw',
  )
  assert.match(
    source,
    /stageIsCollapsed = computed\(\(\) => stageCollapsed\.value \|\| !stageHasCharacter\.value\)/,
    'the stage must collapse when there is no character, whatever the preference says',
  )
})

test('a collapsed stage always offers a way back', () => {
  // A rail with no expand control is a one-way door: the user gives up the
  // stage once and cannot get it back without clearing storage.
  const expand = allElements().find((element) => element.classes.includes('stage-rail-expand'))
  assert.ok(expand, 'the collapsed rail has no expand control')

  const hidden = collapsedRules()
    .filter((rule) => rule.selector.includes('.stage-rail'))
    .filter((rule) => declarationValue(rule.declarations, 'display') === 'none')
    .map((rule) => rule.selector)
  assert.deepEqual(hidden, [], 'the rail itself must stay visible while collapsed')
})

test('collapsing never hides a decision the user has to make', () => {
  // Everything on the hide list should be decoration or a control with a
  // counterpart in the rail. A consent prompt is neither: PRD 4.2 makes memory
  // candidates the user's to confirm or reject, so a layout preference must not
  // be the reason one is never seen.
  const hideLists = collapsedRules()
    .filter((rule) => declarationValue(rule.declarations, 'display') === 'none')
    .flatMap((rule) => rule.selector.split(','))
    .map((part) => part.trim())

  const hidesConsent = hideLists.filter((selector) => /memory-authorize|approval|execution-approval/.test(selector))

  // If the prompt is hidden, the rail must carry the signal and a route back.
  if (hidesConsent.length > 0) {
    const signal = allElements().find((element) => element.classes.includes('stage-rail-pending'))
    assert.ok(
      signal,
      `collapsing hides ${hidesConsent.join(', ')} with nothing in the rail to replace it`,
    )
    assert.ok(
      withinClass(signal, 'stage-rail'),
      'the pending-memory signal must live in the rail, where it is visible while collapsed',
    )
  }
})

test('the collapsed rail does not outrank an approval', () => {
  // TDD 12.2: acting UI must never cover waiting/paused/failed. The rail is
  // chrome; it must not be given a stacking level that could put it over an
  // approval surface.
  const railLevels = cssRules()
    .filter((rule) => rule.selector.includes('.stage-rail'))
    .map((rule) => declarationValue(rule.declarations, 'z-index'))
    .filter((value) => value !== null)
    .map((value) => Number.parseInt(value, 10))
    .filter(Number.isFinite)

  for (const level of railLevels) {
    assert.ok(level < 40, `the rail stacks at ${level}, which can cover the approval surfaces`)
  }
})
