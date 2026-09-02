/**
 * TDD 12.2, threat "角色 UI 掩盖风险": acting animation must never cover
 * waiting/paused/failed. A companion that hides the thing asking for permission
 * behind the thing that looks alive is a companion that gets permission it was
 * never given.
 *
 * Two ways an approval can be swallowed, and both are stated in the source:
 * it can be *nested inside* decoration, or it can be *out-stacked* by it.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { allElements, classesIn, cssRules, declarationValue, stackingLevel } from './helpers/shellSource.mjs'

const APPROVAL_SURFACES = ['execution-approval', 'approval-actions', 'mini-approval-actions']

/** Everything that exists to look alive rather than to convey system state. */
const DECORATIVE_LAYERS = [
  'character',
  'character-fit',
  'character-art',
  'character-shadow',
  'character-fallback-layer',
  'character-live2d-canvas',
  'accessory-item',
  'live2d-status-badge',
  'speech',
]

function approvalElements() {
  return allElements().filter((element) => APPROVAL_SURFACES.some((s) => element.classes.includes(s)))
}

test('every approval surface is actually rendered somewhere', () => {
  // Guards the rest of this file: assertions over an empty set pass silently.
  const found = new Set(approvalElements().flatMap((el) => el.classes.filter((c) => APPROVAL_SURFACES.includes(c))))
  assert.deepEqual([...found].sort(), [...APPROVAL_SURFACES].sort())
})

test('no approval is nested inside the character decoration', () => {
  for (const element of approvalElements()) {
    const enclosing = element.ancestors.flatMap((a) => a.classes).filter((c) => DECORATIVE_LAYERS.includes(c))
    assert.deepEqual(
      enclosing,
      [],
      `${element.classes.join('.')} at ${element.file}:${element.line} sits inside ${enclosing.join(', ')} -- ` +
        'decoration would clip or cover it',
    )
  }
})

test('an approval that shares the stage outranks every decorative layer', () => {
  const decorativeCeiling = Math.max(
    ...DECORATIVE_LAYERS.map((c) => stackingLevel(c)).filter((level) => level !== null),
  )

  for (const element of approvalElements()) {
    // Only approvals that share a stacking context with decoration are at risk.
    // The in-conversation approval lives in the workspace column, a sibling of
    // the stage, so nothing on the stage can reach it.
    const onStage = element.ancestors.some((a) => a.classes.includes('stage'))
    if (!onStage) continue

    const chain = [element, ...element.ancestors].flatMap((node) => node.classes)
    const level = Math.max(...chain.map((c) => stackingLevel(c) ?? Number.NEGATIVE_INFINITY))

    assert.ok(
      Number.isFinite(level),
      `${element.classes.join('.')} at ${element.file}:${element.line} is on the stage but nothing in its ` +
        'ancestry declares a z-index -- decoration would decide the order',
    )
    assert.ok(
      level > decorativeCeiling,
      `${element.classes.join('.')} stacks at ${level}, at or below decoration at ${decorativeCeiling}`,
    )
  }
})

test('no rule hides an approval surface', () => {
  const hidden = []
  for (const rule of cssRules()) {
    if (!APPROVAL_SURFACES.some((s) => classesIn(rule.selector).includes(s))) continue
    for (const [property, value] of [
      ['display', 'none'],
      ['visibility', 'hidden'],
      ['opacity', '0'],
      ['pointer-events', 'none'],
    ]) {
      if (declarationValue(rule.declarations, property) === value) {
        hidden.push(`${rule.selector} { ${property}: ${value} }`)
      }
    }
  }

  assert.deepEqual(hidden, [], 'an approval that cannot be seen or clicked is an approval that cannot be refused')
})
