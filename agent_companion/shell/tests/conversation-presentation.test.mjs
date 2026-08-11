import assert from 'node:assert/strict'
import test from 'node:test'

import { assistantDisplayText, isAssistantPresentationEvent } from '../src/conversationPresentation.ts'

test('companion tool start keeps the animated thinking presence visible', () => {
  assert.equal(isAssistantPresentationEvent('tool_started'), false)
})

test('terminal response events replace thinking with an assistant message', () => {
  assert.equal(isAssistantPresentationEvent('runtime_final'), true)
  assert.equal(isAssistantPresentationEvent('tool_completed'), true)
  assert.equal(isAssistantPresentationEvent('tool_failed'), true)
})

test('the chat bubble uses display text even when the selected voice language differs', () => {
  const event = {
    display_card: { summary: '我是你的桌面伙伴。' },
    voice_line: { text: 'デスクトップの相棒です。' },
  }
  assert.equal(assistantDisplayText(event), '我是你的桌面伙伴。')
})
