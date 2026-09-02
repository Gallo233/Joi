/**
 * A control must answer when you touch it.
 *
 * Before this was fixed the shell had 267 interactive elements, one `:active`
 * rule (which only changed the cursor), and `* { outline: none }` at the top of
 * the stylesheet removing the browser's focus ring from everything. Clicking a
 * button produced no acknowledgement, so people clicked twice; tabbing moved
 * focus somewhere invisible.
 *
 * Both fixes are single rules applied globally, which is what makes them worth
 * guarding: one careless `outline: none` or one stray override puts the whole
 * app back where it started, and nothing about the screenshot would look wrong.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { cssRules, declarationValue, stylesheets } from './helpers/shellSource.mjs'

const entry = () => stylesheets().find((sheet) => sheet.file === 'styles/index.css')

test('the cascade layer order is declared, and interaction sits above legacy', () => {
  // Layer order is what lets new rules win at their natural specificity. Lose
  // it and every rule added from here on has to out-specify the legacy sheet.
  const source = entry()?.text ?? ''
  const declaration = source.match(/@layer\s+([^;]+);/)
  assert.ok(declaration, 'styles/index.css must declare the layer order before importing anything')

  const order = declaration[1].split(',').map((name) => name.trim())
  assert.ok(order.includes('legacy') && order.includes('interaction'), `unexpected layers: ${order}`)
  assert.ok(
    order.indexOf('interaction') > order.indexOf('legacy'),
    'interaction must come after legacy, or the legacy sheet wins',
  )
  assert.match(source, /@import[^;]*styles\.css'\)\s*layer\(legacy\)/, 'the legacy sheet must be imported into its layer')
})

test('nothing suppresses the focus ring', () => {
  // The original defect was one declaration on `*`. It is worth failing loudly
  // on any reappearance, because the symptom -- focus lands somewhere with no
  // ring -- is invisible unless someone happens to be navigating by keyboard.
  const suppressors = cssRules()
    .filter((rule) => {
      const outline = declarationValue(rule.declarations, 'outline')
      const style = declarationValue(rule.declarations, 'outline-style')
      return outline === 'none' || outline === '0' || style === 'none'
    })
    .map((rule) => `${rule.file}: ${rule.selector}`)

  assert.deepEqual(suppressors, [], 'these remove the keyboard focus ring')
})

test('focus-visible draws a ring that is actually visible', () => {
  const ring = cssRules().find(
    (rule) => rule.selector === ':focus-visible' && declarationValue(rule.declarations, 'outline'),
  )
  assert.ok(ring, 'no global :focus-visible outline rule')

  const outline = declarationValue(ring.declarations, 'outline')
  assert.match(outline, /solid/, `focus outline must be solid, got "${outline}"`)
  assert.doesNotMatch(outline, /\b0(px)?\b/, `focus outline must have width, got "${outline}"`)
})

test('pressing a button produces a visible change, not just a cursor swap', () => {
  const pressRules = cssRules().filter((rule) => rule.selector.includes(':active'))
  assert.ok(pressRules.length > 0, 'no :active rules at all')

  const visible = pressRules.filter((rule) => {
    // A press that only changes the cursor is the bug, not the fix.
    const meaningful = rule.declarations.replace(/cursor\s*:[^;]+;?/g, '').trim()
    return meaningful.length > 0
  })
  assert.ok(visible.length > 0, 'every :active rule only changes the cursor -- that is not feedback')

  // The fix has to reach ordinary buttons generally, not a handful by name.
  const global = visible.some((rule) => /(?:^|,)\s*button[^,]*:active/.test(rule.selector))
  assert.ok(global, 'no :active rule applies to plain `button` -- feedback would be per-element again')
})

test('press feedback cannot fight a control that positions itself with transform', () => {
  // Several controls centre themselves with `transform: translateX(-50%)`.
  // Using `transform` for the press would replace that and throw them across
  // the titlebar; `translate` composes with it instead.
  const pressing = cssRules().filter(
    (rule) => rule.selector.includes(':active') && rule.file === 'styles/interaction.css',
  )
  for (const rule of pressing) {
    assert.equal(
      declarationValue(rule.declarations, 'transform'),
      null,
      `${rule.selector} presses with "transform"; use the "translate" property so it composes`,
    )
  }
})
