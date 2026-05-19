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

## 2026-05-14

- Renamed product and GitHub repository to Joi.
- Added Python WebSocket JSON-RPC bridge.
- Added LLM expression layer and GPT-SoVITS bridge.
- Connected Tauri/Vue shell to Python Core event stream.
- Connected Browser Executor and OK-WW adapter to real callback paths.
- Installed and verified Node/npm, Rust, MSVC Build Tools, and Tauri build chain.
