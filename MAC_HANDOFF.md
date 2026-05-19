# Mac Handoff: Joi

This is the portable handoff for continuing Joi from a Mac checkout.

Latest Windows-side main observed here:

- Commit: `c138083 Expand visual detector fixture evals`
- Branch state: `main...origin/main`
- Product line: `agent_companion/`

## Product Goal

Joi is a character-fronted multimodal agent companion, not a VN fork and not a generic chatbot.

The MVP keeps three demo loops stable:

1. Watch together: observe current screen/page/video, summarize, discuss, and answer follow-up questions.
2. Coding: send engineering tasks to Codex, report progress, and show results in task cards.
3. Game skill: dry-run, confirm, launch OK-WW or future game skills, and report status without overclaiming completion.

All high-capability actions must stay auditable and semi-automatic.

## Architecture Snapshot

Main folders:

- `agent_companion/core/`: Python Agent Core.
- `agent_companion/shell/`: Tauri/Vue desktop shell.
- `docs/`: roadmap, known issues, changelog, handoff docs.
- `tests/fixtures/visual_detector/`: committed synthetic visual detector fixtures.
- `tools/eval_visual_detector.py`: visual detector fixture eval.
- `tools/generate_visual_fixtures.py`: synthetic fixture generator.
- `run_agent_companion_tests.py`: broad regression harness.

Core result contract:

- `agent_state`: structured data for planner/debug.
- `display_card`: readable user-facing task card.
- `voice_line`: short natural TTS line.

Never put JSON, ids, paths, commands, coordinates, tokens, logs, model names, raw typed text, or screenshot names into `voice_line`.

## Current Implemented State

- Python Core and Tauri/Vue Shell communicate through local WebSocket JSON-RPC.
- Model routing exists for text, vision, and expression providers.
- GPT-SoVITS/TTS bridge and voice audio epoch handling exist.
- OpenAI-compatible ASR is wired with config, size limits, timeout handling, and serialized command execution.
- Watch Together has Windows screen observation, optional vision summary, OCR grounding, session-only recall, and clickable screenshot thumbnails.
- Computer Use supports click/type/scroll/hotkey behind approval.
- `computer.type_text` uses Windows clipboard paste for reliable Chinese input.
- Computer Use actions attach after screenshots and compare before/after observations.
- Semantic target grounding now combines OCR, Windows UI Automation, and visual heuristic candidates.
- UIA static text and explicitly disabled controls cannot create direct click approvals.
- Candidate selection is bound to `selection_id`; stale cards cannot select the latest context accidentally.
- Visual-only candidates never auto-click. They require candidate selection and then a separate medium-risk click approval.
- Visual detector has synthetic fixture evals for HUD/canvas layouts and a local-only private screenshot path.

## Current Known Risks

- Windows active-window capture, UIA grounding, clipboard paste, and OK-WW integration are Windows-first.
- On Mac, keep platform-specific code isolated behind adapters; do not pretend Windows-only loops are production Mac features.
- Visual detector is still lightweight heuristic, not a trained detector.
- Committed visual fixtures are synthetic. Real screenshots should stay local-only under ignored `data/local_visual_eval/`.
- OK-WW callback proves launch/return code, not detailed in-game completion state.
- Codex permission requests do not yet flow back into Joi UI step by step.
- Settings UI does not yet expose current ASR/TTS/OCR/model provider state.

## Mac Setup

Clone and enter the repo:

```bash
git clone git@github.com:Gallo233/Joi.git
cd Joi
```

Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Optional OCR/visual screenshot dependencies:

```bash
pip install -r requirements-ocr.txt
```

Tesseract is still a system dependency for OCR. The visual detector can run committed PPM fixture evals without Pillow, but real PNG/JPEG screenshot detection needs Pillow from `requirements-ocr.txt`.

Tauri/Vue shell:

```bash
brew install node rust
cd agent_companion/shell
npm install
```

If Tauri asks for extra macOS system dependencies, install the missing pieces through the official Tauri setup instructions.

## Local Config

Do not commit real secrets.

Use environment variables or local ignored files:

- `config.yaml`
- `secrets.yaml`
- `.env`

Public examples belong in `config.example.yaml`.

## Verification Commands

From repo root:

```bash
python -m compileall -q agent_companion run_agent_companion_tests.py tools/smoke_ws_bridge.py tools/eval_visual_detector.py tools/generate_visual_fixtures.py
python tools/eval_visual_detector.py
python run_agent_companion_tests.py
```

From shell folder:

```bash
cd agent_companion/shell
npm run build
npm run tauri -- build --debug
```

Before committing:

```bash
git status -sb
git grep -n -E "七海|爱弥斯|Shinsekai MVP|sk-[A-Za-z0-9]" -- .
```

Expected: no real secrets or third-party character assets. Placeholder keys in tests/docs must be obvious fake examples.

## everything-claude-code Reference

Use `everything-claude-code` as a reference library only, not as a runtime dependency.

Suggested Mac install:

```bash
mkdir -p ~/references
git clone --depth 1 https://github.com/affaan-m/everything-claude-code.git ~/references/everything-claude-code
```

Use these references during planning/review:

- `skills/agent-harness-construction`
- `skills/security-review`
- `skills/eval-harness`
- `skills/ai-regression-testing`
- `skills/agent-introspection-debugging`

Do not:

- Install ECC global hooks.
- Copy ECC rules wholesale into Joi.
- Enable ECC MCP configs by default.
- Add ECC as a Joi runtime dependency.

## First Mac Action

Start by reviewing the latest main change:

```text
Review P4.8.2 / commit c138083 "Expand visual detector fixture evals".

Check:
1. Synthetic fixture expansion is meaningful: game skill bar, modal confirm/cancel, radial menu, canvas button cluster, video controls.
2. `tools/generate_visual_fixtures.py` is deterministic and does not require private images.
3. `tools/eval_visual_detector.py` reports committed synthetic cases separately from local private cases.
4. `data/local_visual_eval/` is ignored and missing private cases are skipped, not failed.
5. Visual-only candidates still require selection plus medium-risk click approval.
6. `voice_line` does not leak bbox/source/path/JSON/screenshot names.
7. README, HERMES_TASKS, KNOWN_ISSUES, CHANGELOG are updated.
8. Full verification commands pass on Mac or any platform-specific failures are documented.
```

If P4.8.2 review passes, next main-branch task should be P4.9.

## Next Task: P4.9 Computer Use Audit View

Prompt for the main branch:

```text
Based on everything-claude-code agent-harness-construction, security-review, and eval-harness, add a Computer Use audit view to Joi.

Goal:
Make every observe -> target candidate -> approval -> action -> verification sequence inspectable without exposing raw machine text to voice.

Requirements:
1. Add a stable audit event model for Computer Use:
   - task_id
   - event type
   - timestamp
   - sanitized summary
   - risk level
   - approval id/status when relevant
   - tool/action name
   - sanitized arguments
   - before/after artifacts
   - verification result
2. Keep raw paths, coordinates, command args, JSON, tokens, logs, and screenshot filenames out of `voice_line`.
3. UI should show an audit timeline/card panel in developer or task detail mode.
4. Confirmed click/type/scroll/hotkey actions should be visible with before/after screenshots and verification status.
5. Approval denied, expired, duplicate approval, and no-op verification should have clear audit entries.
6. Add tests for audit event creation, sanitization, approval lifecycle, verification lifecycle, and no voice leaks.
7. Update docs: HERMES_TASKS, KNOWN_ISSUES, CHANGELOG, and README if user-facing.
8. Run full verification:
   - compileall
   - run_agent_companion_tests.py
   - tools/eval_visual_detector.py
   - npm run build
   - npm run tauri -- build --debug
```

## Hard Boundaries

Do not commit:

- `config.yaml`, `secrets.yaml`, real API keys, model paths, imported character packs.
- Raw audio, generated TTS audio, screenshots, logs, SQLite memory files.
- `.venv/`, `node_modules/`, Tauri `target/`, `dist/`, generated private eval screenshots.
- Third-party copyrighted character assets.
- Old prototype code from removed MVP/VN branches.

Keep MVP scope tight. If the codebase grows without improving the three demo loops, cut scope and stabilize.
