/**
 * The stylesheet states its sizes once, through tokens.
 *
 * Before this, the shell carried 24 distinct font sizes (including six
 * half-pixel values and text at 8px), 27 radii and 115 padding values. None of
 * that was a decision anyone made; it is what happens when each new panel picks
 * a number that looks right next to the last one. The tokens are the decision,
 * and these tests are what stop the drift starting again -- a single `13.5px`
 * added in a hurry is invisible in review and permanent in practice.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { cssRules, declarationValue, stylesheets } from './helpers/shellSource.mjs'

/** Below this, text stops being small and starts being unreadable. */
const MIN_FONT_PX = 11

const declarationsOf = (property) =>
  cssRules()
    .map((rule) => ({ rule, value: declarationValue(rule.declarations, property) }))
    .filter((entry) => entry.value !== null)

test('no text is declared below the readable floor', () => {
  const tooSmall = declarationsOf('font-size')
    .filter(({ value }) => {
      const px = Number.parseFloat(value)
      return value.endsWith('px') && Number.isFinite(px) && px < MIN_FONT_PX
    })
    .map(({ rule, value }) => `${rule.file}: ${rule.selector} { font-size: ${value} }`)

  assert.deepEqual(tooSmall, [], `text below ${MIN_FONT_PX}px`)
})

test('font sizes come from the scale, not from whatever looked right', () => {
  const offScale = declarationsOf('font-size')
    .filter(({ value }) => !value.includes('var(--text-') && !value.startsWith('clamp('))
    .map(({ rule, value }) => `${rule.file}: ${rule.selector} { font-size: ${value} }`)

  assert.deepEqual(offScale, [], 'these bypass the type scale')
})

test('radii come from the scale', () => {
  // `0` and percentages are shapes, not sizes. `50%` is a circle; the stage
  // backdrop uses `46%` to sit slightly off-round so it reads as an organic
  // blob behind the character rather than a bubble. Both mean the same thing
  // at any scale, so neither belongs in a step table.
  const offScale = declarationsOf('border-radius')
    .filter(({ value }) =>
      value
        .split(/\s+/)
        .some((part) => !part.includes('var(--radius-') && part !== '0' && !part.endsWith('%')),
    )
    .map(({ rule, value }) => `${rule.file}: ${rule.selector} { border-radius: ${value} }`)

  assert.deepEqual(offScale, [], 'these bypass the radius scale')
})

test('a pill stays a pill', () => {
  // `999px` is the idiom for "fully rounded", not a large radius. Snapping it
  // onto the largest fixed step turns every capsule button and avatar in the
  // app into a rounded rectangle -- a change that is obvious on screen and
  // invisible in a diff full of `border-radius` edits.
  const tokens = stylesheets().find((sheet) => sheet.file === 'styles/tokens.css')?.text ?? ''
  const pill = tokens.match(/--radius-pill:\s*(\d+)px/)
  assert.ok(pill, '--radius-pill is not defined')
  assert.ok(
    Number.parseInt(pill[1], 10) >= 999,
    `--radius-pill is ${pill[1]}px, which rounds corners instead of making a capsule`,
  )

  const inUse = cssRules().filter((rule) =>
    (declarationValue(rule.declarations, 'border-radius') ?? '').includes('--radius-pill'),
  )
  assert.ok(inUse.length > 0, 'nothing uses --radius-pill; the capsules were probably snapped away')
})

test('gaps come from the spacing scale', () => {
  const offScale = ['gap', 'row-gap', 'column-gap']
    .flatMap((property) =>
      declarationsOf(property).filter(({ value }) =>
        value.split(/\s+/).some((part) => !part.includes('var(--sp-') && part !== '0'),
      ),
    )
    .map(({ rule, value }) => `${rule.file}: ${rule.selector} { gap: ${value} }`)

  assert.deepEqual(offScale, [], 'these bypass the spacing scale')
})
