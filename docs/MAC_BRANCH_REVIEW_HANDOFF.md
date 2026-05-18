# Joi Mac Branch Review Handoff

This file is the portable top-level design and review contract for continuing Joi from a Mac branch.
Copy or keep this file in the Mac checkout so the Mac-side agent can review `main` with the same product standards.

## Product Thesis

Joi is a character-fronted multimodal agent companion.

It should not become a visual novel, a simple chatbot, or a pile of unrelated tools. The product promise is:

- The user talks to a trusted character.
- The character can watch visible context, listen to voice input, speak back, and operate tools.
- High-capability actions remain auditable and semi-automatic.
- Tool results are translated into character-safe display cards and short voice lines.

Initial market wedge: developers, players, and content consumers.

First demo loops:

1. Watch together: observe current window/page/video, summarize, discuss, and answer follow-up questions.
2. Coding: send engineering tasks to Codex, report progress, and show results in task cards.
3. Game skill: dry-run, confirm, launch OK-WW or future game skills, and report status without overclaiming completion.

## Architecture Contract

Main code areas:

- `agent_companion/core/`: Python Agent Core.
- `agent_companion/shell/`: Tauri/Vue desktop shell.
- `docs/`: roadmap, known issues, feedback, handoff files.
- `run_agent_companion_tests.py`: current integration-style regression tests.

Core layers:

- `observe`: screen/window/browser observation, vision summaries, future OCR.
- `think`: routing, planning, context, memory, future richer planner.
- `act`: Codex, browser, computer use, game skill, MCP, file tools.
- `policy`: risk classification, approval ids, audit boundaries.
- `express`: character speech, sprites, TTS-safe voice lines, display cards.

All tool results must stay split into:

- `agent_state`: structured data for planner/debug.
- `display_card`: user-readable task result.
- `voice_line`: short natural line for TTS. Never read JSON, ids, paths, commands, tokens, logs, coordinates, model names, or raw typed text.

## Current State

As of the Windows branch handoff:

- Vision observation exists for Windows active-window/fullscreen through `observe.screen`.
- OpenAI-compatible vision summarization is wired when configured.
- Computer Use has click/type/scroll/hotkey adapters.
- Computer Use actions require one-time approvals tied to task, step, tool, and argument hash.
- Computer Use attaches after-action screenshots.
- `computer.type_text` uses clipboard paste on Windows for reliable Chinese input.
- Watch Together stores recent visual context in session only.
- Watch follow-ups use `watch.recall`, with model-backed answers when configured and template fallback when not.
- Watch screenshots render as clickable thumbnails in the shell.
- Watch observations do not enter long-term memory by default.
- Voice input foundation exists: click-to-record UI, mock ASR, `voice.transcribe` / `audio.transcribe`, transcript routed through normal `user.message`.

## Current Priority

Next recommended task: P4.2.1 Voice Input Productionization.

Goal: move voice input from mock/demo behavior to a real, configurable, privacy-preserving ASR path.

Use this prompt for the Mac branch:

```text
Continue Joi main: implement P4.2.1 Voice Input Productionization.

Before coding, reference the local everything-claude-code library if available:
~/references/everything-claude-code

Focus references:
- skills/agent-harness-construction
- skills/security-review
- skills/eval-harness
- skills/ai-regression-testing
- skills/agent-introspection-debugging

Only absorb methods and checklists. Do not copy large content, install global hooks, or make ECC a Joi runtime dependency.

Goals:
1. Add ASR config parsing: asr.enabled, asr.provider, asr.base_url, asr.model, asr.api_key, asr.language, asr.max_seconds, asr.max_bytes.
2. Keep MockAsrProvider for tests/developer mode only. If real ASR is not configured, disable the shell microphone button or clearly show "ASR not configured".
3. Implement a cross-platform OpenAI-compatible ASR provider first. Keep audio in memory only; do not write raw audio to disk. Local Whisper can come later.
4. Add frontend max recording duration and backend payload size limits. Return friendly errors on limit failures.
5. Add a Core command queue or lock so user.message, voice.transcribe, and approval.resolve cannot mutate app state concurrently.
6. Show transcript in the UI, then route it through existing user.message and approval_id flows. Voice commands must not bypass approvals.
7. Map watch status llm_answer to a readable UI label such as "角色理解" or "模型回答".
8. Add tests for real/mock ASR selection, unconfigured ASR behavior, audio limits, transcript routing, voice-triggered approval, and serialized commands.
9. Update ROADMAP, KNOWN_ISSUES, FEEDBACK_LOG, HERMES_TASKS, and this handoff if the priority changes.
10. Run Python tests and frontend build before committing.
```

## everything-claude-code Usage

Install as a reference library, not as global hooks:

```bash
mkdir -p ~/references
git clone --depth 1 https://github.com/affaan-m/everything-claude-code.git ~/references/everything-claude-code
```

Use it during task planning and review:

- Architecture: `skills/agent-harness-construction`, `skills/autonomous-agent-harness`, `skills/architecture-decision-records`.
- Review: `skills/security-review`, `agents/code-reviewer.md`, `agents/security-reviewer.md`.
- Tests: `skills/eval-harness`, `skills/agent-eval`, `skills/ai-regression-testing`.
- Tool/MCP design: `skills/mcp-server-patterns`, `skills/agent-introspection-debugging`.

Do not:

- Install ECC global hooks.
- Copy ECC rules wholesale into Joi.
- Enable ECC MCP configs by default.
- Add ECC as a runtime dependency.

## Review Checklist

Every Mac-side review of `main` should check:

1. Product fit: does the change make Joi a better agent companion, or just add tool surface?
2. Privacy: are screen, voice, transcript, audio, screenshots, or tool logs stored longer than needed?
3. Policy: can voice/text/WebSocket calls bypass approval?
4. Voice safety: does `voice_line` avoid raw machine text?
5. UI clarity: can the user see what Joi observed, heard, planned, and executed?
6. Cross-platform safety: are Windows-only assumptions isolated behind adapters?
7. Tests: are routing, approval, fallback, failure, and no-leak paths covered?
8. Docs: are ROADMAP, KNOWN_ISSUES, FEEDBACK_LOG, and HERMES_TASKS updated?

Recommended review output:

```text
Findings:
- P1/P2/P3 issue with file and line reference.

Verification:
- commands run and results.

Next task:
- one concrete prompt for the main branch.
```

## Mac Verification Commands

Use the local environment if already set up. Adjust Python/Node commands to the Mac toolchain.

```bash
python -m compileall -q agent_companion run_agent_companion_tests.py tools/smoke_ws_bridge.py
python run_agent_companion_tests.py
cd agent_companion/shell
npm run build
npm run tauri -- build --debug
```

If dependencies are not installed yet, install them explicitly and keep generated dependency folders out of Git.

## Hard Boundaries

Do not commit:

- `config.yaml`, `secrets.yaml`, API keys, model paths, imported character packs.
- Raw audio, generated TTS audio, screenshots, logs, SQLite memory files.
- `.venv/`, `node_modules/`, `target/`, `dist/`, `gen/`.
- Third-party copyrighted character assets.
- Old prototype code from removed MVP/VN branches.

Public examples should use `config.example.yaml` and placeholders.

## Phase Map

- MVP: Windows-first shell with watch, coding, game skill, voice in/out, safe approvals.
- Alpha: settings UI, real ASR/TTS model management, memory controls, audit view, stronger browser/computer use.
- Beta: Live2D/VRM rendering plugins, richer MCP/tools, multiple game skills, packaged installer and update path.
- V1: stable Windows release with privacy/audit controls and polished first-run setup.
- V2: Mac/Linux/mobile companion surfaces and sync.
- V3+: deeper multimodal agent platform.
- V4/V5: XR, spatial presence, robotics/embodiment research paths.

Keep MVP small. If the core grows without stabilizing the three demo loops, stop and cut scope.
