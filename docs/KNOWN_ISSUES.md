# Joi Known Issues

## Active

- Vision now supports Windows active-window/fullscreen screenshots, optional visual model summaries, and best-effort OCR grounding with timeout; OCR runtime status now reflects both Python package and local Tesseract runtime availability, while OCR quality still depends on Tesseract language data and captured image clarity.
- Watch Together can reuse recent visual summaries in the current app session, but context is not persisted across restarts yet.
- Voice input has OpenAI-compatible ASR wiring and explicit disabled state when unconfigured; local Whisper is not wired yet.
- Computer Use actions are Windows-only and now include OCR/title/dimension plus local image-diff verification, an inspectable developer-mode audit timeline, and ranked semantic OCR/accessibility/visual target candidates with capture-rect grounding and selection-id binding, but UI Automation tree quality depends on the target program and the visual detector is still a lightweight heuristic rather than a trained detector. Current synthetic coverage includes sparse controls, mixed right/bottom HUDs, low-contrast modal actions, UIA/OCR disagreement gates, static/disabled UIA controls, repeated dense browser labels, desktop settings neighbor conflicts, modal/popover foreground-background conflicts, unsafe/fractional capture-scale gates, stale UIA snapshots, clipped foreground windows, UIA screen-bounds capture trust, partial capture-rect gates, truncated dropdowns, visual-only HUD selection gates, and small visible image changes; larger dense real layouts and trained visual detection remain open boundaries.
- Local real-screenshot calibration is supported only through ignored manifests under `data/local_visual_eval/`; shared regression coverage must still be converted into synthetic fixtures before commit.
- The stage can load local character sprites from configuration, but bundled original VN expression variants and Live2D/VRM rendering are not product grade yet.
- OK-WW callback currently proves launch/return code, but does not yet read detailed in-game completion state.
- Codex permission requests now surface as sanitized task cards and can resume only when a JSONL event explicitly proves permission-specific resumable handling; the local real CLI probe did not expose a reliable permission resume token, so real Codex CLI permission prompts still fail closed with "Codex 需要外部权限确认，Joi 暂不能继续。"
- Runtime provider settings are inspectable in developer mode, and a compact approval-gated settings panel can dry-run/apply allowlisted non-secret fields. There is still no full provider editor; secret, endpoint, base URL, model-file path, and local audio/path entry remains intentionally deferred.

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
- Computer Use verification now includes a local screenshot comparison signal, so meaningful visual changes can count as verified even when OCR and window title are unchanged.
- OCR regions now support Watch Together region questions and semantic click proposals, while final execution still goes through one-time Computer Use approval.
- Semantic target approvals now show candidate preview boxes and refuse to synthesize clicks when the screenshot-to-screen coordinate transform is not trustworthy.
- Close semantic OCR candidates now show as selectable candidates in the task card instead of becoming a click approval automatically.
- Semantic target grounding can use optional Windows UI Automation control names/roles/bounds when `uiautomation` is installed; static UIA text and explicitly disabled UIA controls are not treated as clickable targets unless a later observation provides actionable evidence.
- Canvas/game-like screens with sparse OCR/UIA can now expose visual heuristic candidates, but visual-only targets still require user selection and a separate click approval; committed fixture coverage is synthetic, while private real screenshots should stay under ignored `data/local_visual_eval/`.
- Candidate-card selection is now bound to a specific pending semantic target id, while phrases like "选 2" remain a latest-context fallback; candidate contexts are still session-only and expire quickly.
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
- Developer mode shows sanitized ASR/TTS/OCR/text-model/vision-model/expression-model runtime status, Computer Use platform availability, and audit/verification capability without exposing provider secrets, local paths, or raw logs.
- Computer Use approval, action, and verification audit entries are visible in developer mode with sanitized arguments and before/after screenshot links.
- Computer Use denied, expired, duplicate, unavailable, inconclusive, and likely no-op paths now produce clear audit entries without speaking raw ids, paths, coordinates, typed text, or screenshot filenames.
- Computer Use audit entries include sanitized image-change signals for post-action verification.
- Visual detector and image-diff evals now report committed synthetic suites separately from skipped or locally run private suites.
- Coding tasks now expose sanitized Codex run status, event timeline, permission-required state, denial/expiry handling, and safe artifact labels without speaking raw commands, paths, JSONL, stderr, task ids, approval ids, or tokens.
