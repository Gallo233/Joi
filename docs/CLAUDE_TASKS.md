# Claude Code Handoff: Joi

This file is the handoff contract for local Claude Code collaboration on Joi.

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

Continue P2 Vision Layer.

The first screen observation layer is already implemented:

- `agent_companion/core/vision/schemas.py`
- `agent_companion/core/vision/observer.py`
- `agent_companion/core/vision/windows.py`
- `agent_companion/core/tools/screen_observe.py`
- route: watch requests -> `observe.screen`

Next task options, in priority order:

1. Add `VisionSummarizer` interface and first implementation for OpenAI-compatible vision models.
2. Add model routing config for separate text, vision, and expression models.
3. Feed `VisionObservation` screenshot plus user query into summarizer and return a user-facing summary in `observe.screen`.
4. Keep tool result split into `agent_state`, `display_card`, and `voice_line`.
5. Ensure `voice_line` never speaks screenshot paths, JSON, command lines, model names, tokens, or logs.

## Suggested First Task

Implement `agent_companion/core/vision/summarizer.py`:

- Define `VisionSummary` dataclass.
- Define `VisionSummarizer` protocol/interface.
- Add a mock summarizer for tests.
- Add an OpenAI-compatible summarizer that can call a configured vision model.
- Keep failures non-fatal: if vision summarization fails, `observe.screen` should still return the screenshot observation card.

Then update `ScreenObserveTool` so successful observations can include:

- screenshot artifact
- target/window metadata
- optional visual summary
- concise natural `voice_line`

## Allowed Edit Areas

Preferred:

- `agent_companion/core/vision/`
- `agent_companion/core/tools/screen_observe.py`
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
