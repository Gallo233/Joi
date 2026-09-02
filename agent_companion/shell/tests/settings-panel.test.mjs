/**
 * Settings: every declared category is reachable, and the panel's layout
 * responds to its own width rather than the window's.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { cssRules, declarationValue, scriptSource, stylesheets } from './helpers/shellSource.mjs'

const settingsSource = () => stylesheets() && scriptSource()

test('every declared settings tab is reachable from the navigation', () => {
  // The list once declared eighteen categories while the navigation exposed
  // six. The other twelve rendered a "coming soon" placeholder nobody could
  // route to, and each read like a shipped feature in the type union.
  const source = scriptSource()

  // Scoped to the tabs array: `{ id: '...', label: ... }` is also the shape of
  // the BYOK provider presets, which are not navigation entries.
  const table = source.match(/export const settingsTabs: SettingsTab\[\] = \[[\s\S]*?\n\]/)
  assert.ok(table, 'settingsTabs table not found')
  const declared = [...table[0].matchAll(/\{ id: '(\w+)'/g)].map((m) => m[1])
  assert.ok(declared.length > 0, 'no settings tabs found -- has settings.ts changed shape?')

  const groups = source.match(/const settingsGroupDefinitions[\s\S]*?\n\]/)
  assert.ok(groups, 'settingsGroupDefinitions not found')
  const routed = new Set([...groups[0].matchAll(/'(\w+)'/g)].map((m) => m[1]))

  const unreachable = declared.filter((id) => !routed.has(id))
  assert.deepEqual(unreachable, [], 'these categories are declared but no navigation entry opens them')
})

test('the settings panel responds to its own width, not the window width', () => {
  // The CLI cards used to collapse at a 1320px *viewport*. The cards sit inside
  // the settings panel, so at a 1024px window that panel was still ~900px wide
  // -- far more than the ~330px its three columns need -- and the "测试" button
  // stretched to 834px on a row of its own while every card doubled in height.
  //
  // Anything that lays out inside the panel has to be measured against the
  // panel. `@container` does that; a viewport media query cannot.
  const panelSelectors = /\.agent-cli-card|\.agent-cli-test|\.settings-section-head|\.settings-config-card/

  const viewportRuled = cssRules()
    .filter((rule) => panelSelectors.test(rule.selector))
    .filter((rule) => rule.atRule?.startsWith('@media'))
    .map((rule) => `${rule.atRule} { ${rule.selector} }`)

  assert.deepEqual(viewportRuled, [], 'these lay out panel contents from the window width')
})

test('the settings panel establishes a containment context', () => {
  // Without this, every `@container` rule below silently never matches.
  const container = cssRules().find(
    (rule) =>
      rule.selector.includes('.settings-main') && declarationValue(rule.declarations, 'container-type'),
  )
  assert.ok(container, '.settings-main must declare container-type for the @container rules to apply')
  assert.match(declarationValue(container.declarations, 'container-type'), /inline-size/)
})
