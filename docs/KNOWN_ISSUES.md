# Joi Known Issues

## Active

- Vision now supports Windows active-window/fullscreen screenshots, optional visual model summaries, and best-effort OCR grounding with timeout; OCR quality depends on optional local dependencies, Tesseract language data, and captured image clarity.
- Watch Together can reuse recent visual summaries in the current app session, but context is not persisted across restarts yet.
- Voice input has OpenAI-compatible ASR wiring and explicit disabled state when unconfigured; local Whisper is not wired yet.
- Computer Use actions are intentionally minimal: click/type/scroll/hotkey are Windows-only and now include OCR-aware before/after verification with a short settle delay, but they do not yet have semantic visual target grounding or pixel-diff confidence.
- The stage can load local character sprites from configuration, but bundled original VN expression variants and Live2D/VRM rendering are not product grade yet.
- OK-WW callback currently proves launch/return code, but does not yet read detailed in-game completion state.
- Codex permission requests do not yet flow back into Joi UI step by step.
- Model routing is split between text, vision, and expression models, but the settings UI does not yet expose current provider/model usage.

## Fixed

- Ordinary chat no longer needs to show plan/task-completed events in the product UI.
- Duplicate approval responses are ignored instead of creating a failure card.
- Blank browser observations are no longer treated as successful visual understanding.
- OK-WW lines avoid claiming that game tasks are complete unless the tool proves completion.
- Raw tool names are filtered out of voice lines.
- Computer Use voice lines avoid speaking coordinates, text payloads, JSON, command-like strings, paths, and task ids.
- Computer Use actions attach an after screenshot to the task card.
- Computer Use actions compare before/after observations and avoid claiming success when changes are not visible.
- Computer Use verification now reuses configured OCR and waits briefly after actions to reduce false no-op results.
- Watch Together follow-up questions reuse recent visual context instead of forcing another screenshot.
- Watch observations are kept out of long-term memory by default.
- Screenshot artifacts have task-card thumbnails and a click-to-preview modal.
- Mock ASR is no longer used by default in production Core startup.
- Voice transcript routing uses the same approval path as typed commands.
- Voice input rejects oversized recordings in the shell and oversized base64 payloads in Core before decode.
- Voice ASR timeout/error paths now create friendly task cards and sanitized voice lines instead of silent failures.
- Stale spoken audio is stopped when a new typed or voice command starts.
- Late TTS audio from older user intents is suppressed by a client-side voice epoch.
- Voice transcription uses the configured ASR timeout plus grace time instead of the generic Core request timeout.
- Watch Together can cite recent OCR snippets for visible text and labels, but region grouping is still coarse bbox data rather than semantic UI targets.
- Voice audio identity now includes event timestamps so repeated lines in the same task do not collide.
- Developer mode shows ASR/TTS runtime status, but there is not yet a full settings panel for changing providers.
