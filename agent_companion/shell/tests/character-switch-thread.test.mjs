/**
 * Switching characters must not leave the previous one's conversation on screen.
 *
 * Core gives each character its own thread and reports the new one in the same
 * payload as the switch. The shell then asks for history *by thread id*, so a
 * handler that assigns `ready` without taking the collaboration snapshot with it
 * keeps `activeContext.thread_id` pointing at the character just left -- and the
 * fetch faithfully returns that character's transcript into the new one's
 * window. It reads as a Core bug and is entirely a shell one, which is why this
 * is asserted against the source: there is no unit to call.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const handler = source.slice(
  source.indexOf('async function handleCharacterActivated'),
  source.indexOf('function cycleStageZoom'),
)

test('activating a character syncs the collaboration snapshot it was given', () => {
  assert.ok(handler.length > 0, 'handleCharacterActivated not found')
  assert.match(handler, /syncCollaborationSnapshot\(readyPayload\.collaboration\)/)
})

test('the snapshot is taken before any history is fetched', () => {
  const synced = handler.indexOf('syncCollaborationSnapshot')
  const fetched = handler.indexOf('refreshConversationHistory')
  assert.ok(synced >= 0 && fetched >= 0)
  assert.ok(synced < fetched, 'history would be fetched for the previous thread')
})

test('the previous transcript is cleared rather than merged into', () => {
  assert.match(handler, /events\.value = \[\]/)
  assert.match(handler, /eventCursor\.value = 0/)
})
