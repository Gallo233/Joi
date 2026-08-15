import assert from 'node:assert/strict'
import test from 'node:test'

import { asrLatencyLabel, isPlayableVoiceEventType, voiceGenerationId } from '../src/voiceRuntime.ts'

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

test('voice input generations are bounded stable client tokens', () => {
  assert.equal(voiceGenerationId(4), 'voice-4')
  assert.equal(voiceGenerationId(-20), 'voice-0')
  assert.equal(voiceGenerationId(Number.NaN), 'voice-0')
})

test('debug latency labels separate provider time from perceived total time', () => {
  assert.equal(
    asrLatencyLabel({ prepare_ms: 12, encode_ms: 3, rpc_ms: 510, provider_ms: 420, total_ms: 525 }),
    'ASR 420ms · 端到端 525ms',
  )
  assert.equal(asrLatencyLabel({}), '')
  assert.equal(asrLatencyLabel({ provider_ms: -1, total_ms: Number.NaN }), '')
})
