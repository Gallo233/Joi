# Hermes Handoff: Joi

This file is the handoff contract for Hermes collaboration on Joi.

## Product Direction

Joi is a Windows-first multimodal agent companion. It is not a VN and not a plain chatbot: it is a character-fronted agent shell for watching screen content, playing games through safe adapters, and executing coding/tool tasks with auditable approvals.

Current main line:

- Python Core: `agent_companion/core/`
- Tauri/Vue Shell: `agent_companion/shell/`
- Project docs, roadmap, known issues, and feedback: `docs/`
- Public config example: `config.example.yaml`
- Local-only runtime/config/secrets/data must stay out of Git.

## Current State

As of 2026-05-17:

- Local `main` is ahead of `origin/main` by one commit:
  - `dcff669 Add vision summarizer routing`
- Python dependencies are installed in `.venv/`.
- Shell dependencies are installed in `agent_companion/shell/node_modules/`.
- Homebrew Node and Rust/Cargo are installed on this Mac.
- Python core tests, Vue build, Tauri debug build, and WebSocket bridge smoke test have passed locally.

Important local commands:

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi"
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python run_agent_companion_tests.py
```

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi/agent_companion/shell"
npm run build
npm run tauri -- build --debug
```

## Hard Boundaries

Do not reintroduce removed prototype code, old MVP folders, old chat/settings entrypoints, or public third-party character assets.

Do not commit:

- `.venv/`
- `node_modules/`
- `agent_companion/shell/dist/`
- `agent_companion/shell/src-tauri/target/`
- `data/`
- `logs/`
- `config.yaml`
- `secrets.yaml`
- real API keys
- local model paths
- voice model files
- imported character packs
- generated screenshots or runtime artifacts

Keep user-local values in `config.yaml` or `secrets.yaml`. Public examples belong in `config.example.yaml`.

## Current Priority

Continue P2 Vision Layer.

Already implemented:

- `VisionObservation` and observer protocol.
- Windows active-window/fullscreen screenshot observer.
- `observe.screen` tool route for watch requests.
- `VisionSummarizer` protocol.
- Mock vision summarizer for tests.
- OpenAI-compatible vision summarizer.
- Optional visual summary in `observe.screen`.
- Separate model routes:
  - `models.text`
  - `models.vision`
  - `models.expression`
- Voice sanitizer blocks raw paths, JSON, command flags, tool ids, tokens, logs, and model names.

Next tasks, in priority order:

1. Add OCR or structured screen text extraction to `VisionObservation`.
2. Feed extracted screen text into `VisionSummarizer` prompt and display card body.
3. Add richer watch-together follow-up turns that reuse the latest visual summary safely.
4. Improve summary failure telemetry without exposing paths, model names, tokens, or logs in speech.
5. Show current model route status in the shell settings surface.

## Suggested First Task

Implement a small OCR abstraction in `agent_companion/core/vision/`, preferably as a protocol plus a safe no-op/mock implementation first. Then wire optional extracted text into `ScreenObserveTool` and `VisionSummarizer` without making OCR failure fatal.

Keep the tool result split clean:

- `agent_state`: structured observation, artifact, optional OCR/summary metadata.
- `display_card`: user-facing summary and detail.
- `voice_line`: short natural speech only, never raw paths, JSON, command lines, model names, tokens, or logs.

## Preferred Edit Areas

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

## Verification

On this Mac:

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi"
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python -m compileall -q agent_companion run_agent_companion_tests.py tools/smoke_ws_bridge.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python run_agent_companion_tests.py
```

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi/agent_companion/shell"
npm run build
npm run tauri -- build --debug
```

For WebSocket bridge smoke:

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi"
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python -m agent_companion.core.server --workspace .
```

In a second terminal:

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi"
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python tools/smoke_ws_bridge.py
```

Stop the server after the smoke test.

Before handing back:

```bash
git status --short
git diff --check
git grep -n -E "<removed project or asset terms>" -- .
```

Expected: no removed prototype or asset leakage.

## Handoff Output

When Hermes finishes a task, report:

- files changed
- behavior added or changed
- tests/builds run
- any remaining risks
- commit hash if committed

Prefer small, reviewable commits. If dependencies or generated build outputs appear, confirm they are ignored and do not stage them.
