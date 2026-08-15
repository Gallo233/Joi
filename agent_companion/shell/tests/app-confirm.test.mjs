import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

test('consequential Joi actions use a visible in-app confirmation instead of WebView confirm', () => {
  assert.doesNotMatch(source, /window\.confirm\(/, 'macOS Tauri does not surface window.confirm reliably')
  assert.match(source, /function requestAppConfirm\(/)
  assert.match(source, /<dialog[^>]+ref="appConfirmDialog"/)
  assert.match(source, /@cancel\.prevent="resolveAppConfirm\(false\)"/)
  assert.match(source, /@click="resolveAppConfirm\(false\)"/)
  assert.match(source, /@click="resolveAppConfirm\(true\)"/)
})

test('adapter install, scope approval, and realtime disclosure all await the in-app gate', () => {
  assert.match(source, /installGameAdapter[\s\S]{0,500}await requestAppConfirm/)
  assert.match(source, /startMinecraftSession[\s\S]{0,1800}await requestAppConfirm/)
  assert.match(source, /toggleRealtimeVoice[\s\S]{0,1600}await requestAppConfirm/)
})
