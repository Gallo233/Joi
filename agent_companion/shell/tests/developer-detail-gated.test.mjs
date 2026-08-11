/**
 * PRD 9.1: "开发者事件、原始标识、审计细节和运行时诊断仅在 Developer Mode 展示".
 *
 * This is not a tidiness rule. Raw events carry tool names, task ids, argument
 * hashes and audit evidence -- the internal identifiers TDD 6.3 keeps out of
 * the ordinary product surface. Once one of these blocks escapes its gate it is
 * invisible in review: it renders fine, it just renders to everyone.
 *
 * Splitting App.vue moves these blocks into new files, which is exactly when a
 * `v-if` gets left behind on the old parent.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { allElements, conditionOf, scriptSource } from './helpers/shellSource.mjs'

/** Classes that render raw events, audit evidence or internal identifiers. */
const DEVELOPER_DETAIL = /^(audit-|debug-list$|debug-row$|debug-title$|task-section$)/

/**
 * The two legitimate ways to reach developer detail: the Workspace toggle, and
 * the Developer tab inside Settings, which the user navigates to deliberately.
 */
const GATES = [/\bdeveloperMode\b/, /activeSettingsTab === 'developer'/]

const developerDetailElements = () =>
  allElements().filter((element) => element.classes.some((c) => DEVELOPER_DETAIL.test(c)))

function isGated(element) {
  const conditions = [element, ...element.ancestors].map(conditionOf).filter(Boolean)
  return conditions.some((condition) => GATES.some((gate) => gate.test(condition)))
}

test('developer detail exists in the shell at all', () => {
  // Without this, a rename would empty the set and every assertion below would
  // pass by vacuum.
  assert.ok(
    developerDetailElements().length > 0,
    'no developer-detail surfaces found -- has DEVELOPER_DETAIL gone stale?',
  )
})

test('every developer-detail surface sits behind a developer gate', () => {
  const exposed = developerDetailElements()
    .filter((element) => !isGated(element))
    .map((element) => `${element.file}:${element.line} <${element.tag} class="${element.classes.join(' ')}">`)

  assert.deepEqual(exposed, [], 'these render raw internals in the ordinary product surface')
})

test('developer mode is off until the user asks for it', () => {
  assert.match(
    scriptSource(),
    /developerMode = ref\(false\)/,
    'developer mode must default to off, or first launch shows raw events',
  )
})
