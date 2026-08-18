import assert from 'node:assert/strict'
import test from 'node:test'

import { RealtimeVoiceSession, parseRealtimeVoiceEvent } from '../src/realtimeVoice.ts'


test('core event parsing exposes only the reviewed projection', () => {
  assert.deepEqual(
    // The epoch travels with the heard text so the chat can pair it with the answer.
    parseRealtimeVoiceEvent({ type: 'user_transcript', text: '你好 Joi', final: true, epoch: 4 }),
    { kind: 'user_transcript', text: '你好 Joi', final: true, epoch: 4 },
  )
  assert.deepEqual(
    parseRealtimeVoiceEvent({ type: 'barge_in', epoch: 3, event_id: 'provider-secret' }),
    { kind: 'barge_in', epoch: 3 },
  )
  assert.deepEqual(
    parseRealtimeVoiceEvent({ type: 'game_action', action: 'collect', status: 'acting', recovery_required: false }),
    { kind: 'game_action', action: 'collect', status: 'acting', recoveryRequired: false },
  )
  assert.equal(parseRealtimeVoiceEvent('{bad json'), null)
  assert.equal(parseRealtimeVoiceEvent({ type: 'provider.raw', error: { message: 'secret raw error' } }), null)
})


test('a spoken motion request arrives as a playable clip, and only a known one', () => {
  assert.deepEqual(
    parseRealtimeVoiceEvent({
      type: 'character_motion',
      epoch: 7,
      motion: { name: 'dance', label: '跳舞', duration_ms: 6000, loop: false, intensity: 0.9 },
    }),
    { kind: 'character_motion', motion: 'dance', epoch: 7, durationMs: 6000, loop: false, intensity: 0.9 },
  )
  assert.equal(parseRealtimeVoiceEvent({ type: 'character_motion', motion: { name: 'backflip' } }), null)
})


test('a local skill proposal reports acceptance without claiming completion', () => {
  assert.deepEqual(
    parseRealtimeVoiceEvent({ type: 'skill_action', skill: 'computer_use', status: 'started', requires_confirmation: true }),
    { kind: 'skill_action', skill: 'computer_use', status: 'started', requiresConfirmation: true, error: undefined },
  )
  assert.deepEqual(
    parseRealtimeVoiceEvent({ type: 'skill_action', status: 'rejected', error: 'realtime_skill_not_actionable' }),
    { kind: 'skill_action', skill: '', status: 'rejected', requiresConfirmation: false, error: 'realtime_skill_not_actionable' },
  )
})


test('PCM session opens only after microphone consent and sends exact 16 kHz frames', async () => {
  const order = []
  const track = { stopped: false, stop() { this.stopped = true } }
  const stream = { getTracks: () => [track] }
  let captureCallback
  let captureStopped = false
  const appended = []
  const stopped = []
  const states = []
  const session = new RealtimeVoiceSession({
    mode: 'minecraft',
    minecraftSessionId: 'session-minecraft-1',
    getUserMedia: async () => { order.push('microphone'); return stream },
    startSession: async (params) => { order.push('provider'); return { ok: true, session_id: 'realtime-1234567890abcdef', params } },
    appendAudio: (params) => appended.push(params),
    stopSession: (sessionId) => stopped.push(sessionId),
    createCapture: (_stream, callback) => {
      captureCallback = callback
      return { stop() { captureStopped = true } }
    },
    onState: (state) => states.push(state),
  })

  await session.start()
  assert.deepEqual(order, ['microphone', 'provider'])
  assert.equal(states.at(-1), 'listening')
  captureCallback(new Uint8Array(1280))
  captureCallback(new Uint8Array(1280).fill(1))
  assert.deepEqual(appended.map(({ session_id, sequence, sample_rate, channels, sample_width }) => ({ session_id, sequence, sample_rate, channels, sample_width })), [
    { session_id: 'realtime-1234567890abcdef', sequence: 1, sample_rate: 16000, channels: 1, sample_width: 2 },
    { session_id: 'realtime-1234567890abcdef', sequence: 2, sample_rate: 16000, channels: 1, sample_width: 2 },
  ])
  assert.equal(Buffer.from(appended[0].audio_base64, 'base64').byteLength, 1280)

  session.stop()
  assert.equal(track.stopped, true)
  assert.equal(captureStopped, true)
  assert.deepEqual(stopped, ['realtime-1234567890abcdef'])
  assert.equal(states.at(-1), 'idle')
})


test('microphone denial opens neither provider nor capture', async () => {
  const session = new RealtimeVoiceSession({
    getUserMedia: async () => { throw new Error('browser-specific permission details') },
    startSession: async () => { assert.fail('provider must stay closed') },
    appendAudio: () => {},
    stopSession: () => {},
    createCapture: () => { assert.fail('capture must stay closed') },
  })

  await assert.rejects(session.start(), /microphone_unavailable/)
  assert.equal(session.lastError, 'microphone_unavailable')
  assert.equal(session.state, 'error')
})


test('stopping while microphone permission is pending releases a late stream', async () => {
  let resolveMicrophone
  const track = { stopped: false, stop() { this.stopped = true } }
  const pendingMicrophone = new Promise((resolve) => { resolveMicrophone = resolve })
  const session = new RealtimeVoiceSession({
    getUserMedia: () => pendingMicrophone,
    startSession: async () => { assert.fail('a cancelled session must not reach Core') },
    appendAudio: () => {},
    stopSession: () => {},
    createCapture: () => { assert.fail('a cancelled session must not capture') },
  })

  const starting = session.start()
  session.stop()
  resolveMicrophone({ getTracks: () => [track] })
  await starting

  assert.equal(track.stopped, true)
  assert.equal(session.state, 'idle')
})


test('events from another owner session are ignored and barge-in is local', async () => {
  const states = []
  const events = []
  const session = new RealtimeVoiceSession({
    getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
    startSession: async () => ({ ok: true, session_id: 'realtime-1234567890abcdef' }),
    appendAudio: () => {},
    stopSession: () => {},
    createCapture: () => ({ stop() {} }),
    onState: (state) => states.push(state),
    onEvent: (event) => events.push(event),
  })
  await session.start()
  session.handleCoreEvent({ session_id: 'realtime-otherowner1234', type: 'barge_in', epoch: 1 })
  assert.equal(events.length, 0)
  session.handleCoreEvent({ session_id: session.sessionId, type: 'barge_in', epoch: 2 })
  assert.deepEqual(events, [{ kind: 'barge_in', epoch: 2 }])
  assert.equal(states.at(-1), 'user_speaking')
  session.stop()
})


test('provider loss releases microphone capture and closes the Core session', async () => {
  const track = { stopped: false, stop() { this.stopped = true } }
  let captureStopped = false
  const stopped = []
  const session = new RealtimeVoiceSession({
    getUserMedia: async () => ({ getTracks: () => [track] }),
    startSession: async () => ({ ok: true, session_id: 'realtime-1234567890abcdef' }),
    appendAudio: () => {},
    stopSession: (sessionId) => stopped.push(sessionId),
    createCapture: () => ({ stop() { captureStopped = true } }),
  })
  await session.start()
  session.handleCoreEvent({ session_id: session.sessionId, type: 'error', error: 'realtime_disconnected' })
  assert.equal(session.sessionId, '')
  assert.equal(session.state, 'error')
  assert.equal(track.stopped, true)
  assert.equal(captureStopped, true)
  assert.deepEqual(stopped, ['realtime-1234567890abcdef'])
})
