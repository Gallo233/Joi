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

Continue P3.1 Policy + Computer Use Hardening.

The current observation, first action layer, and approval hardening are implemented:

- `agent_companion/core/vision/` captures Windows active-window/fullscreen screenshots.
- `agent_companion/core/vision/summarizer.py` can call an OpenAI-compatible vision model.
- `agent_companion/core/computer_use/` defines observation, action, backend, and result contracts.
- `agent_companion/core/tools/screen_observe.py` routes `observe.screen` through the Computer Use observation chain.
- `agent_companion/core/tools/computer.py` exposes `computer.click`, `computer.type_text`, `computer.scroll`, and `computer.hotkey`.
- `agent_companion/core/policy.py` treats Computer Use actions as medium risk, requiring confirmation.
- Approval now uses one-time `approval_id` values bound to task id, step index, tool name, and arguments hash.
- Computer Use actions automatically observe the active window after execution and attach the after screenshot to the task card.
- `computer.type_text` uses clipboard paste on Windows for reliable Chinese input instead of per-character key events.

Next task options, in priority order:

1. Add visual target grounding so natural language like "click the search box" can resolve to screen coordinates through screenshots/OCR/vision.
2. Add an audit view for confirmed Computer Use actions, approvals, and sanitized arguments.
3. Add post-action verification that compares before/after screenshots and flags likely no-op actions.
4. Extend Computer Use beyond Windows only after the Windows-first loop feels reliable.
5. Keep `voice_line` free of coordinates, raw typed text, JSON, command lines, paths, model names, tokens, logs, and task ids.

## Suggested First Task

Implement grounded click planning:

- Add a `computer.locate` or planner helper that accepts a natural-language target and current screenshot.
- Use vision/OCR to return a candidate bounding box plus confidence.
- Keep target resolution low risk; keep the actual click as a medium-risk confirmed action.
- Show "target candidate" in the task card using friendly wording, not raw coordinates.
- If confidence is low, ask for confirmation or request a clearer instruction instead of clicking.

## Allowed Edit Areas

Preferred:

- `agent_companion/core/vision/`
- `agent_companion/core/computer_use/`
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
