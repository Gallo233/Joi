/**
 * The stylesheet does not accumulate rules for elements that no longer exist.
 *
 * This is a ratchet, not a cleanup: the shell reached 69 unreferenced classes
 * by never checking, and each one made the next person less sure which rules
 * were load-bearing.
 *
 * The subtlety worth guarding is the *detection*, not the count. Classes built
 * at runtime -- `:class="`status-${turn.status}`"` -- never appear in the source
 * as whole words, so a plain substring search calls them dead. Eighteen live
 * classes were reported that way, including every turn status and every
 * character emotion, and this is the number someone reads when deciding what is
 * safe to delete. A detector that is wrong in that direction is worse than no
 * detector.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { stylesheets } from './helpers/shellSource.mjs'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')

function sourceFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) sourceFiles(full, out)
    else if (/\.(vue|ts|js)$/.test(entry.name)) out.push(full)
  }
  return out
}

const markup = () => sourceFiles(SRC).map((f) => fs.readFileSync(f, 'utf8')).join('\n')

const declaredClasses = () => {
  const css = stylesheets()
    .map((sheet) => sheet.text.replace(/\/\*[\s\S]*?\*\//g, ''))
    .join('\n')
  return new Set([...css.matchAll(/\.(-?[A-Za-z_][A-Za-z0-9_-]*)/g)].map((m) => m[1]))
}

const runtimePrefixes = (source) => [...source.matchAll(/`([a-z0-9-]*?-)\$\{/g)].map((m) => m[1])

test('classes assembled at runtime are not mistaken for dead ones', () => {
  // Guards the detector itself. If the template-literal convention changes and
  // this stops finding prefixes, the test below silently starts reporting live
  // classes as garbage.
  const prefixes = runtimePrefixes(markup())
  assert.ok(prefixes.length > 0, 'no runtime class prefixes found -- has the convention changed?')
  for (const expected of ['status-', 'step-', 'emotion-', 'cabin-']) {
    assert.ok(prefixes.includes(expected), `"${expected}" classes are built at runtime but no longer detected`)
  }
})

test('no rule targets a class nothing renders', () => {
  const source = markup()
  const prefixes = runtimePrefixes(source)
  const dead = [...declaredClasses()]
    .filter((name) => !source.includes(name))
    .filter((name) => !prefixes.some((prefix) => name.startsWith(prefix)))
    .sort()

  assert.deepEqual(dead, [], 'these classes are styled but never rendered')
})
