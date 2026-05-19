# Joi Changelog

## Unreleased

- Added project discipline docs: roadmap, changelog, known issues, and feedback log.
- Started product-shell cleanup: default UI separates chat, task cards, and developer events.
- Added one-click Windows launch script target for Joi.
- Verified Python core tests, Vue build, and Tauri debug packaging after the shell cleanup.
- Cleaned the public repository down to the Joi main line: removed old prototype entrypoints, third-party character assets, and local personal configuration from Git.
- Moved config parsing, GPT-SoVITS, and browser executor support into `agent_companion/` so Joi no longer depends on removed prototype modules.
- Replaced the embedded placeholder character PNG with local character sprite loading from Core `config.yaml`.
- Improved browser/watch task cards with product-oriented summaries, metadata chips, and non-raw artifact labels.
- Updated the Windows launcher to restart the project Core by default so the shell does not reuse stale protocol state.
- Embedded configured local sprites as data URLs in `core.ready` so Tauri can render them without local file protocol permissions.
- Added `agent_companion.core.vision` with `VisionObservation`, `VisionObserver`, Windows active-window/fullscreen screenshot capture, and an `observe.screen` tool adapter.
- Added `docs/HERMES_TASKS.md` as a local Hermes handoff contract for Joi collaboration.
- Added OpenAI-compatible vision summarization and text/vision/expression model routing.
- Added `agent_companion.core.computer_use` with Windows-backed observation plus click, type, scroll, and hotkey action adapters behind policy confirmation.
- Hardened approvals with one-time `approval_id` bindings and removed direct user-message approval bypass.
- Computer Use actions now attach an after screenshot, and Windows text input uses clipboard paste for reliable Chinese input.
- Added Watch Together session context and follow-up recall using recent visual summaries, titles, questions, and screenshot artifacts.
- Polished Watch Together: role-aware follow-up answers, session-only screen memory by default, and clickable screenshot thumbnails.
- Added voice input foundation with click-to-record shell state, mock ASR, and `voice.transcribe` JSON-RPC routing into normal user-message handling.
- Productionized voice input with ASR config parsing, OpenAI-compatible in-memory transcription, explicit unconfigured UI state, audio limits, transcript display, and serialized Core command handling.
- Hardened voice runtime safety with pre-decode base64 limits, shell-side Blob size checks, ASR timeout handling, friendly voice error task cards, and stale audio interruption on new user intent.
- Added voice queue epochs so late TTS from older intents is suppressed, and aligned `voice.transcribe` RPC timeout with configured ASR timeout.
- Hardened voice audio matching with event timestamps so repeated voice lines in the same task cannot revive stale audio.
- Added developer-mode ASR/TTS runtime status with sanitized TTS error reporting.
- Added best-effort OCR grounding for screen observations, including visible text blocks, task-card OCR details, and Watch Together recall over recent OCR snippets.
- Added OCR timeout configuration and optional OCR setup docs so slow or missing OCR cannot freeze observation.
- Added conservative Computer Use post-action verification with before/after artifacts, visible-change/no-op wording, and sanitized voice lines.
- Upgraded Computer Use verification to reuse configured OCR and wait a configurable post-action settle delay before after-observation.
- Fixed OCR verification semantics so successful empty-to-text OCR changes count as visible changes while failed or unavailable OCR stays inconclusive.
- Added OCR region grouping and semantic target candidate resolution for Watch Together and approval-gated Computer Use clicks.
- Added capture rect grounding for semantic targets, converting OCR screenshot boxes to screen coordinates only when the active-window rect is trusted, with approval-card candidate overlays.
- Added ranked semantic target confidence with ambiguity detection, so close OCR candidates are shown for clarification instead of becoming click approvals.
- Added session-only semantic target candidate continuation, allowing "选 2" or candidate-card selection to resume a pending target and create a fresh click approval.
- Bound semantic target candidate buttons to explicit `selection_id`/rank JSON-RPC selection, preventing old candidate cards from selecting the newest pending context by accident.
- Added optional Windows accessibility-tree grounding for semantic targets, fusing UI control names/roles/bounds with OCR candidates while keeping real clicks behind approval.
- Hardened UI Automation actionability so static text controls do not become click approvals without actionable role/clickable evidence or OCR fusion.

## 2026-05-14

- Renamed product and GitHub repository to Joi.
- Added Python WebSocket JSON-RPC bridge.
- Added LLM expression layer and GPT-SoVITS bridge.
- Connected Tauri/Vue shell to Python Core event stream.
- Connected Browser Executor and OK-WW adapter to real callback paths.
- Installed and verified Node/npm, Rust, MSVC Build Tools, and Tauri build chain.
