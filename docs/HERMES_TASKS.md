# Hermes Handoff: Joi

This file is the handoff contract for local Hermes collaboration on Joi.

## Current Product Direction

Joi is a Windows-first multimodal agent companion. The core product is not a VN and not a simple chatbot: it is a character-fronted agent shell for watching screen content, playing games through safe skills, and executing coding/tool tasks through auditable adapters.

Current main branch status:

- Python Core lives in `agent_companion/core/`.
- Tauri/Vue Shell lives in `agent_companion/shell/`.
- Roadmap and user feedback live in `docs/`.
- Runtime data, screenshots, local config, logs, imported characters, and model files must stay out of Git.

## Hard Boundaries

Do not reintroduce removed prototype code or public third-party character assets.

Do not add or commit directories or files from removed prototypes, such as:

- legacy Python prototype package directories
- secondary MVP experiment directories
- old chat/settings entrypoints
- generated screenshots under `data/`
- `.venv/`, `node_modules/`, Tauri `target/`, `dist/`
- real API keys, model paths, voice model files, imported character packs
- copyrighted or third-party character assets

Keep `config.yaml` local only. Public examples should go in `config.example.yaml`.

## Current Priority

Continue P4.2 Voice Input hardening and prepare voice interruption / TTS queue cleanup.

The current observation, first action layer, approval hardening, watch loop, and production ASR path are implemented:

- `agent_companion/core/vision/` captures Windows active-window/fullscreen screenshots.
- `agent_companion/core/vision/summarizer.py` can call an OpenAI-compatible vision model.
- `agent_companion/core/computer_use/` defines observation, action, backend, and result contracts.
- `agent_companion/core/tools/screen_observe.py` routes `observe.screen` through the Computer Use observation chain.
- `agent_companion/core/tools/computer.py` exposes `computer.click`, `computer.type_text`, `computer.scroll`, and `computer.hotkey`.
- `agent_companion/core/policy.py` treats Computer Use actions as medium risk, requiring confirmation.
- Approval now uses one-time `approval_id` values bound to task id, step index, tool name, and arguments hash.
- Computer Use actions automatically observe the active window after execution and attach the after screenshot to the task card.
- `computer.type_text` uses clipboard paste on Windows for reliable Chinese input instead of per-character key events.
- `agent_companion/core/watch.py` keeps recent watch context in the current app session.
- `agent_companion/core/tools/watch.py` exposes `watch.recall` for follow-up questions.
- Follow-up prompts like "你看到了什么" reuse recent summaries and screenshot artifacts instead of repeating screenshots.
- Watch follow-up answers use the role-aware text/expression model when configured, then fall back to deterministic templates.
- Watch observations are ephemeral by default and do not enter long-term memory unless an explicit save flow is added later.
- The Vue shell renders screenshot artifacts as clickable thumbnails with a preview modal.
- `agent_companion/core/speech_input.py` defines `SpeechInputProvider`, `MockAsrProvider`, and `AsrResult`.
- The WebSocket bridge exposes `voice.transcribe` / `audio.transcribe`, then routes transcripts through normal `user.message`.
- The shell has a click-to-record microphone button; first version does not save raw audio and does not run always-on listening.
- `config.example.yaml` documents `asr.enabled`, provider, endpoint/model/key, language, max duration, and max payload size.
- Production Core does not use `MockAsrProvider` unless explicitly injected for tests/developer mode.
- OpenAI-compatible ASR keeps audio in memory and sends it directly to the transcription endpoint.
- Shell disables the microphone and shows "ASR 未配置" when no real ASR is configured.
- Voice transcripts are shown in the stage and then routed through the normal task/approval flow.
- Core serializes `user.message`, `voice.transcribe`, and `approval.resolve` mutations with a command lock.

Next task options, in priority order:

1. Add voice interrupt/cancel behavior so a new command can stop stale TTS playback.
2. Add ASR provider health/status display in settings once settings UI exists.
3. Add OCR/vision target grounding so pages and video frames can be discussed with more precise references.
4. Add post-action verification that compares before/after screenshots and flags likely no-op actions.
5. Add an audit view for confirmed Computer Use actions, approvals, and sanitized arguments.
6. Extend Computer Use beyond Windows only after the Windows-first loop feels reliable.
7. Keep `voice_line` free of coordinates, raw typed text, JSON, command lines, paths, model names, tokens, logs, and task ids.

## Suggested First Task

Implement voice interruption and queue cleanup:

- Track currently playing audio in the shell.
- Stop stale TTS when the user submits text or starts a new recording.
- Add a Core event or client-side policy for dropping old `agent.voice_audio` payloads.
- Keep transcript and task events intact; only interrupt playback.
- Add tests or a small browser smoke check for stale audio cancellation.

## Allowed Edit Areas

Preferred:

- `agent_companion/core/vision/`
- `agent_companion/core/watch.py`
- `agent_companion/core/speech_input.py`
- `agent_companion/core/computer_use/`
- `agent_companion/core/tools/watch.py`
- `agent_companion/core/tools/computer.py`
- `agent_companion/core/tools/screen_observe.py`
- `agent_companion/core/policy.py`
- `agent_companion/core/planner.py`
- `agent_companion/core/config.py`
- `agent_companion/core/app.py`
- `run_agent_companion_tests.py`
- `docs/ROADMAP.md`
- `docs/KNOWN_ISSUES.md`
- `docs/CHANGELOG.md`
- `docs/FEEDBACK_LOG.md`

Use caution:

- `agent_companion/shell/src/App.vue`
- `agent_companion/shell/src/styles.css`
- `agent_companion/shell/src/api.ts`
- `agent_companion/shell/src/protocol.ts`

Avoid unless necessary:

- Tauri Rust files
- startup scripts
- repository layout

## Required Verification

Run all of these before handing back:

```powershell
.\.venv\Scripts\python.exe -m compileall -q agent_companion run_agent_companion_tests.py tools\smoke_ws_bridge.py
.\.venv\Scripts\python.exe run_agent_companion_tests.py
cd agent_companion\shell
D:\codex游戏\toolchains\node\npm.cmd run build
D:\codex游戏\toolchains\node\npm.cmd run tauri -- build --debug
```

Before commit, scan tracked files for removed project/asset leakage:

```powershell
git grep -n -E "<removed project or asset terms>" -- .
```

Expected result: no matches.

## Handoff Output

When finished, report:

- files changed
- behavior added
- tests/builds run
- any remaining risks
- commit hash if committed

Prefer small, reviewable commits.
