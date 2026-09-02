/**
 * Compact mode is the same app in less space, not a cut-down one.
 *
 * It used to offer a text field and click-to-record and nothing else: starting
 * a realtime call, watching together, or reaching memory and settings all meant
 * leaving compact mode first. And the character was a fixed box, so the one
 * thing a desktop companion is expected to do -- be the size you want in the
 * corner you put her in -- was not offered at all.
 *
 * AIRI's desktop window is the reference: it gives the model its own scale
 * beside the window's, and remembers where the window sits. These tests hold
 * the two properties that follow from that -- the wheel scales her and the
 * scale is kept -- plus the one geometric invariant that scaling introduces:
 * the window must stay tall enough that the control panel never covers her.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { allElements, cssRules, declarationValue, scriptSource, withinClass } from './helpers/shellSource.mjs'

const constant = (name) => {
  const match = scriptSource().match(new RegExp(`const ${name} = (-?\\d+(?:\\.\\d+)?)`))
  assert.ok(match, `${name} must be a literal constant the geometry can be checked against`)
  return Number(match[1])
}

test('the wheel scales her, and does not scroll the page instead', () => {
  const wheeled = allElements().filter((element) => /@wheel/.test(element.attributes))
  assert.equal(wheeled.length, 1, 'exactly one element takes the wheel')
  const [character] = wheeled
  assert.match(character.attributes, /:class="\['character'/, 'the wheel belongs to the character, not the stage around her')
  assert.match(
    character.attributes,
    /@wheel\.prevent="handleStageWheel"/,
    'the wheel must be bound with .prevent, or the gesture scrolls the shell behind her',
  )
})

test('one wheel notch is one step on a trackpad and on a mouse', () => {
  // A trackpad reports many small deltas where a mouse reports few large ones.
  // Reading the magnitude would make the same gesture behave differently on the
  // two devices, so the handler may only read the direction.
  const handler = scriptSource().match(/function handleStageWheel\([\s\S]*?\n}/)
  assert.ok(handler, 'handleStageWheel must exist')
  assert.match(handler[0], /event\.deltaY > 0 \? -1 : 1/)
  assert.doesNotMatch(
    handler[0],
    /deltaY \*|\* event\.deltaY|deltaY \/ /,
    'scaling by the raw delta makes a trackpad flick her from tiny to huge',
  )
})

test('compact scale and stage zoom are separate, and both are kept', () => {
  const source = scriptSource()
  assert.match(source, /compactScale = usePersistentRef\('compactScale'/)
  assert.match(source, /stageZoom = usePersistentRef\('stageZoom'/)
  // Compact mode returns before touching the stage zoom: scaling the desktop
  // companion must not silently rescale the full window she is restored to.
  const handler = source.match(/function handleStageWheel\([\s\S]*?\n}/)[0]
  assert.match(handler, /if \(isCompactMode\.value\) \{[\s\S]*?return\s*\}/)
})

test('the scale is bounded, so she cannot be spun to nothing or off the screen', () => {
  const source = scriptSource()
  const min = constant('ZOOM_MIN')
  const max = constant('ZOOM_MAX')
  assert.ok(min > 0 && min < 1, 'the floor must leave her visible')
  assert.ok(max > 1 && max <= 4, 'the ceiling must keep the window on screen')
  assert.match(source, /function clampZoom\([\s\S]*?Math\.min\(ZOOM_MAX, Math\.max\(ZOOM_MIN/)
})

test('the character box follows the scale for every renderer', () => {
  // Scaling the box rather than the art is what makes one gesture work for a
  // VRM camera, a Live2D canvas and a flat sprite alike.
  const sized = cssRules().filter(
    (rule) =>
      rule.selector.includes('.compact-active') &&
      !rule.selector.includes(':not(.compact-active)') &&
      /\.character(-fit)?$/.test(rule.selector.trim()) &&
      declarationValue(rule.declarations, 'width') !== null,
  )
  const targets = sized.map((rule) => rule.selector.trim().split(' ').pop())
  assert.deepEqual(
    [...new Set(targets)].sort(),
    ['.character', '.character-fit'],
    'both the character box and the box its renderer draws into must be sized',
  )
  for (const rule of sized) {
    for (const property of ['width', 'height']) {
      assert.match(
        declarationValue(rule.declarations, property) ?? '',
        /var\(--compact-scale/,
        `${rule.selector} must size ${property} from --compact-scale`,
      )
    }
  }
})

test('the window always reserves enough room that the panel cannot cover her', () => {
  // The measured dashboard is 166px tall and sits 16px off the bottom. The
  // window is character + margin + reserve, so the reserve has to cover the
  // panel and its gap -- reserving less is what put it across her legs.
  const reserve = constant('COMPACT_DASHBOARD_HEIGHT')
  assert.ok(
    reserve >= 166 + 16,
    `the dashboard reserve (${reserve}px) must cover the panel and the gap beneath it`,
  )
})

test('everything the composer can start is reachable without leaving compact mode', () => {
  const source = allElements()
    .filter((element) => withinClass(element, 'mini-control-dashboard'))
    .map((element) => element.attributes)
    .join('\n')
  assert.match(source, /toggleRealtimeVoice\(\)/, 'a realtime call must be startable from compact mode')
  assert.match(source, /startWatchLoop\(\)/, 'watching together must be startable from compact mode')
  assert.match(source, /openCompactCabin\('memory'\)/)
  assert.match(source, /openCompactCabin\('inspector'\)/)
  assert.match(source, /openCompactAttachments\('file'\)/)
})

test('an attachment restores the window its chip list lives in', () => {
  // Attaching from a window that cannot show what was attached is a silent
  // action, which is worse than not offering it.
  assert.match(
    scriptSource(),
    /async function openCompactAttachments[\s\S]{0,220}if \(isCompactMode\.value\) await toggleCompactMode\(\)/,
  )
})

test('a running call says so in the only place compact mode can say it', () => {
  const buttons = allElements().filter((element) => element.classes.includes('mini-action-btn'))
  assert.ok(buttons.length >= 4, 'the compact dashboard must carry the quick actions')
  const raw = buttons.map((element) => element.attributes).join('\n')
  assert.match(raw, /:class="\{ active: realtimeVoiceActive \}"/)
  assert.match(raw, /:class="\{ active: watchLoopActive \}"/)
})

test('where she was left is remembered, not just how big she was', () => {
  assert.match(
    scriptSource(),
    /compactPosition = usePersistentRef<[^>]*>\('compactPosition'/,
    'putting a desktop companion in a corner is a placement, and it has to survive a relaunch',
  )
})

test('a position is only restored if she can still be grabbed there', () => {
  // The saved point is only meaningful against the displays it was recorded on.
  // Unplug the monitor she was parked on and restoring it puts her somewhere
  // she cannot be dragged back from.
  const source = scriptSource()
  assert.match(source, /async function compactPositionIsReachable\(/)
  assert.match(source, /await availableMonitors\(\)/, 'reachability must be checked against the real displays')
  assert.match(
    source,
    /async function restoreCompactPosition\([\s\S]{0,600}if \(!\(await compactPositionIsReachable\([\s\S]{0,40}\)\)\) return/,
    'restore must bail out when the saved point is off-screen',
  )
})

test('moving her ourselves is never mistaken for the user placing her', () => {
  const source = scriptSource()
  // Leaving compact mode hands the normal window back its own position while
  // the shell still reads as compact. Without the guard that move overwrites
  // the corner the user chose.
  assert.match(source, /suppressCompactPositionSave/)
  assert.match(
    source,
    /onMoved\(\(\{ payload \}\) => \{\s*if \(!isCompactMode\.value \|\| suppressCompactPositionSave\) return/,
  )
  assert.match(
    source,
    /stopWatchingCompactPosition\(\)\s*suppressCompactPositionSave = true/,
    'the restore path must suppress before it moves the window back',
  )
})

test('the move listener is released with the component', () => {
  // onMoved returns an unlisten function; dropping it leaks a native listener
  // across every remount.
  assert.match(
    scriptSource(),
    /onBeforeUnmount\(\(\) => \{[\s\S]{0,400}stopWatchingCompactPosition\(\)/,
  )
})

test('memory and settings restore the window they need to be readable in', () => {
  assert.match(
    scriptSource(),
    /async function openCompactCabin[\s\S]{0,200}if \(isCompactMode\.value\) await toggleCompactMode\(\)/,
    'a full cabin drawn into a 360px pet is not readable',
  )
})
