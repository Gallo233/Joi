/**
 * Only terminal response events may replace the companion's transient
 * thinking presence. In particular, companion.chat emits a tool_started event
 * while the model is still working; treating that as an assistant response
 * bypasses the animated thinking component and shows a static event summary.
 */
export function isAssistantPresentationEvent(eventType: string): boolean {
  return eventType === 'runtime_final'
    || eventType === 'tool_completed'
    || eventType === 'tool_failed'
}

/**
 * The chat bubble is the display channel, never the TTS channel.
 *
 * `voice_line.text` may intentionally be Japanese while the user-visible
 * `display_card.summary` is Chinese. Keeping this projection here makes it
 * difficult for a character-motion special case to join those channels again.
 */
export function assistantDisplayText(event: {
  display_card?: { summary?: string }
  voice_line?: { text?: string }
}): string {
  return String(event.display_card?.summary || '').trim()
}
