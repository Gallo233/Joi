/**
 * The app shows the notices it ships under.
 *
 * Joi is distributed without a Live2D Expandable Application agreement, which
 * makes the obligations that do apply the ones that carry the weight: the
 * Cubism sample-data terms require the copyright notice to travel with the
 * product. It travelled with the repository instead -- `THIRD_PARTY_NOTICES.md`
 * was complete and reachable by nobody who had only downloaded the DMG.
 *
 * These tests hold the two halves of the fix: a panel that names the copyright
 * holder, and a build that copies the notices in so the panel has something to
 * show.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { scriptSource } from './helpers/shellSource.mjs'

const SHELL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const appSource = () => fs.readFileSync(path.join(SHELL, 'src/App.vue'), 'utf8')
const packageJson = () => JSON.parse(fs.readFileSync(path.join(SHELL, 'package.json'), 'utf8'))

test('the About category is declared and reachable', () => {
  const source = scriptSource()
  assert.match(source, /\{ id: 'about',/, 'no About tab declared')
  const groups = source.match(/const settingsGroupDefinitions[\s\S]*?\n\]/)
  assert.ok(groups, 'settingsGroupDefinitions not found')
  assert.ok(groups[0].includes("'about'"), 'the About tab is declared but no navigation entry opens it')
})

test('the About panel names the copyright holder of the default character', () => {
  // Not "mentions Live2D somewhere": the terms ask for the notice, so the panel
  // has to carry the character's name and the company that owns her.
  const panel = appSource().match(/activeSettingsTab === 'about'[\s\S]*?\n            <\/div>/)
  assert.ok(panel, 'the About panel is gone')
  assert.ok(panel[0].includes('Live2D Inc.'), 'the About panel does not name Live2D Inc.')
  assert.ok(panel[0].includes('桃瀬ひより'), 'the About panel does not name the sample model it ships')
})

test('the notices are bundled, not read from a path that only exists in a checkout', () => {
  const source = appSource()
  assert.match(source, /from '\.\/generated\/LICENSE\.txt\?raw'/)
  assert.match(source, /from '\.\/generated\/THIRD_PARTY_NOTICES\.md\?raw'/)
})

test('every build copies the notices in before it bundles', () => {
  // This is the ratchet. The panel imports files that a build generates, so a
  // build that stops generating them is the way this regresses -- and it would
  // regress into a build failure on a clean checkout rather than a silent one,
  // which is why the sync belongs in each script rather than in a note.
  const { scripts } = packageJson()
  assert.ok(scripts['legal:sync'], 'the legal notice sync script is gone')
  for (const name of ['dev', 'build', 'build:release']) {
    assert.ok(
      scripts[name].includes('legal:sync'),
      `npm run ${name} no longer syncs the legal notices`,
    )
  }
})
