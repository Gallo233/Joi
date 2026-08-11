import assert from 'node:assert/strict'
import test from 'node:test'

import { isPlayableVoiceEventType } from '../src/voiceRuntime.ts'

test('only user-relevant terminal and approval events may reach the speaker', () => {
  for (const eventType of ['approval_required', 'runtime_final', 'runtime_error', 'tool_completed', 'tool_failed']) {
    assert.equal(isPlayableVoiceEventType(eventType), true, eventType)
  }
})

test('system lifecycle and generic task announcements are never playable', () => {
  for (const eventType of [
    'runtime_started',
    'tool_started',
    'task_completed',
    'task_failed',
    'plan_created',
    'audit_event',
    'user_message',
    undefined,
  ]) {
    assert.equal(isPlayableVoiceEventType(eventType), false, String(eventType))
  }
})
