/**
 * The project sheet has to be genuinely absent when it is closed.
 *
 * It used to be an `<aside>` that stayed mounted and slid out of view with
 * `translateX(-100%)`. Off-screen is not gone: ten focusable controls stayed in
 * the tab order, so tabbing out of the titlebar walked into a panel nobody
 * could see. There was no Esc, no focus trap, no focus return, and the backdrop
 * was a full-window `<button>` that was itself a tab stop leading nowhere.
 *
 * Every check below stands for something that was verified by hand in the
 * running app and would fail silently if it regressed -- none of it changes how
 * a screenshot looks.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { allElements, cssRules, declarationValue, templates, withinClass } from './helpers/shellSource.mjs'

const markup = () => templates().map((t) => t.text).join('\n')

test('the sheet is a dialog, not a panel parked off-screen', () => {
  const source = markup()
  assert.match(source, /<DialogContent[^>]*class="context-rail"/, 'the rail must render through DialogContent')
  assert.match(source, /<DialogOverlay[^>]*class="context-rail-backdrop"/, 'the backdrop must be an overlay')

  // The old backdrop was a <button>: a full-window tab stop that announced
  // itself to screen readers and did nothing visible.
  assert.doesNotMatch(source, /<button[^>]*class="context-rail-backdrop"/, 'the backdrop must not be a button')

  // A closed dialog is unmounted, so nothing should be pushing it off-screen.
  const parked = cssRules()
    .filter((rule) => rule.selector.includes('.context-rail') && !rule.selector.includes('data-state'))
    .filter((rule) => (declarationValue(rule.declarations, 'transform') ?? '').includes('-100%'))
    .map((rule) => rule.selector)
  assert.deepEqual(parked, [], 'a closed sheet should be absent, not translated out of view')
})

test('the sheet has a trigger, so focus has somewhere to return', () => {
  // Reka restores focus to the DialogTrigger when the dialog closes. Driving
  // `open` from a plain button instead leaves focus on <body> after Esc, which
  // strands keyboard users at the top of the document.
  assert.match(markup(), /<DialogTrigger/, 'the sheet must be opened through DialogTrigger')
})

test('both of the sheet\'s animations are declared, and both can finish', () => {
  // Reka holds a closing sheet in the DOM until its exit animation reports
  // `animationend`. So an exit animation must exist as a matched pair with the
  // entrance -- and neither may loop, because an animation that never ends
  // never reports, and the sheet would stay on screen permanently.
  const states = ['open', 'closed'].map((state) =>
    cssRules().find(
      (rule) =>
        rule.selector.includes('.context-rail') &&
        rule.selector.includes(`data-state='${state}'`) &&
        declarationValue(rule.declarations, 'animation'),
    ),
  )

  for (const [index, rule] of states.entries()) {
    const state = ['open', 'closed'][index]
    assert.ok(rule, `the sheet declares no animation for data-state='${state}'`)
    const animation = declarationValue(rule.declarations, 'animation')
    assert.doesNotMatch(animation, /infinite/, `the ${state} animation loops, so it never reports completion`)
    assert.match(animation, /\d+m?s/, `the ${state} animation has no duration`)
  }
})

test('settings does not stack a second navigation on top of its own', () => {
  // Settings is a full-window layer with a nav column. Opening the project
  // sheet over it covered those categories completely. PRD 9.1 puts Projects
  // inside Workspace; it is not a peer of Settings.
  const trigger = markup().match(/<DialogTrigger[\s\S]{0,200}?>/)
  assert.ok(trigger, 'no DialogTrigger found')
  assert.match(
    trigger[0],
    /activeCabin !== 'inspector'/,
    'the project sheet must not be reachable from Settings',
  )
})

test('settings does not stack two identical exits in one corner', () => {
  // Three controls reading "返回对话" were on screen together. Two of them sat
  // about 90px apart in the same top-right corner: the titlebar toggle and a
  // close button in the settings header. What remains is one per region --
  // back at the top of the settings nav, and the global toggle in the
  // titlebar -- so the label never appears twice in the same place.
  const exitsInHeader = allElements()
    .filter((element) => withinClass(element, 'settings-open-header') || element.classes.includes('settings-close'))
    .filter((element) => /返回对话/.test(element.attributes))

  assert.deepEqual(
    exitsInHeader.map((element) => `${element.file}:${element.line} .${element.classes.join('.')}`),
    [],
    'an exit here lands directly beneath the titlebar control that already says the same thing',
  )
})
