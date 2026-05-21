# Joi Agent Progress Memory

This file is the durable project memory for future Codex/Hermes/reviewer agents. Prefer this file over chat history when reconstructing Joi progress.

## Product North Star

Joi is a Windows-first multimodal agent companion, not a VN prototype and not a simple chatbot. It is a character-fronted shell for:

- watching the user's screen with safe visual/OCR context
- performing approval-gated Computer Use actions
- handling voice input/output without leaking technical details
- running coding/tool tasks through auditable adapters
- eventually supporting game/task skills through safe tool boundaries

The current mainline lives in:

- Python Core: `agent_companion/core/`
- Tauri/Vue Shell: `agent_companion/shell/`
- Test/eval gates: `run_agent_companion_tests.py`, `tools/eval_visual_detector.py`, `tools/generate_visual_fixtures.py`
- Handoff/project memory: `docs/`

## Hard Boundaries

- Do not reintroduce old prototype code, removed VN/chat/settings entrypoints, or third-party character assets.
- Do not commit runtime data, screenshots, logs, local config, model files, voice model files, `.venv`, `node_modules`, Tauri `dist`/`target`, secrets, API keys, endpoints, or private screenshot manifests.
- Local private calibration belongs under ignored `data/local_visual_eval/` and must only be promoted as synthetic generated fixtures.
- Keep `voice_line` free of coordinates, raw typed text, JSON, command lines, paths, model names, tokens, logs, screenshot filenames, approval ids, and task ids.

## Main Progress Arc

Joi's P4 arc is Computer Use / Watch grounding. The work moved from basic vision and tool calls into reliable, auditable, approval-gated screen understanding:

1. Vision observation and Watch Together:
   - Windows active-window/fullscreen screenshot capture.
   - Optional OpenAI-compatible vision summaries.
   - Watch Together recall over recent screen summaries and OCR snippets.
   - Screenshot thumbnails and preview modal in the shell.

2. Voice:
   - Click-to-record shell flow.
   - OpenAI-compatible ASR provider.
   - In-memory audio handling, payload size limits, ASR timeout handling.
   - Voice epoch/event timestamp hardening so stale TTS cannot revive after newer user intent.
   - Runtime status for ASR/TTS with sanitized errors.

3. Computer Use:
   - Windows-backed observe, click, type, scroll, hotkey adapters.
   - Medium-risk policy approval for all Computer Use actions.
   - One-time approval ids bound to task id, step, tool, and argument hash.
   - Post-action observation and verification.
   - Deterministic local image-diff signal plus OCR/title/dimension verification.
   - Developer-mode audit timeline with sanitized before/after artifacts.

4. Semantic target grounding:
   - OCR region grouping and target candidate ranking.
   - Capture-rect grounding from screenshot bbox to screen coordinates.
   - Candidate ambiguity gates for close scores and low confidence.
   - Session-only candidate continuation with `selection_id`.
   - Windows UI Automation names/roles/bounds integration.
   - Actionability hardening for static or disabled UIA controls.
   - Visual detector fallback for sparse canvas/game UI, selection-first and no visual-only auto-click.

5. Calibration and safety fixtures:
   - Synthetic visual detector fixtures.
   - Synthetic image verification fixtures.
   - Synthetic semantic grounding fixtures.
   - Local private calibration output is sanitized and kept out of Git.

6. Runtime/admin:
   - Developer runtime provider status panel.
   - Safe runtime config writer foundation.
   - Approval-gated runtime settings preview/apply flow for non-secret fields only.
   - Runtime settings JSON-RPC apply precheck fixed to fail synchronously for invalid/no-op requests.

7. Codex/autopilot:
   - Coding-task audit bridge parses `codex exec --json` into sanitized run state.
   - Unresumable real Codex permission prompts fail closed.
   - Open-source/runtime options were evaluated.
   - Default autopilot path is now Codex-only via ChatGPT-authenticated Codex CLI, not OpenAI API-key Agents SDK, to avoid separate API billing.

## Version Review Memory

The user started the review cadence at `review4.8.2`. Older detailed chat findings may be missing from compressed context, so use Git/docs as source of truth.

### P4.8.2 to P4.18: Foundation and Hardening

This period established the product-grade Computer Use / Watch loop:

- Vision summarizer routing and screen observation.
- Computer Use adapter and approval hardening.
- Watch Together recall loop.
- Voice input foundation and production ASR.
- Voice runtime safety and stale audio suppression.
- OCR grounding, OCR timeout, and OCR setup docs.
- Post-action verification and OCR verification semantics.
- OCR region target grounding and semantic target screen coordinates.
- Ranked candidates, candidate selection continuation, and `selection_id` binding.
- UI Automation semantic grounding, actionability hardening, disabled-control blocking.
- Visual detector fallback and fixture evals.
- Computer Use audit and local visual/image verification calibration.
- Runtime provider status panel and OCR runtime probe hardening.
- Codex permission audit bridge and fail-closed permission state.

Important boundary from P4.18:

- A real local Codex CLI probe did not expose a reliable permission-specific resume token.
- Joi must fail closed for unresumable real Codex CLI permission prompts.

### P4.19

Commit: `389457e Add safe runtime config writer foundation`

- Added safe runtime config mutation foundation.
- Only allowlisted non-secret provider/runtime fields can be previewed or written.
- Preserves unknown YAML fields and env placeholders.
- Leaves `secrets.yaml` untouched.
- Writes are approval-gated via `runtime.update_config`.

Review result: passed.

Next direction at the time: add compact runtime settings UI.

### P4.20

Commit: `58e4571 Add runtime settings approval flow`

- Added developer-mode runtime settings dry-run/apply controls.
- Kept secrets, endpoints, base URLs, local paths, model/audio filenames out of the UI.

Review finding:

- P2: `runtime.config.apply` JSON-RPC could report submitted/approval-style success even when invalid preview updates were discarded.

Next direction at the time: fix apply precheck contract.

### P4.21

Commit: `2f906f5 Fix runtime apply RPC precheck`

- Fixed invalid/no-op runtime config applies so they return sanitized precheck results synchronously.
- Only changed valid updates create approval cards.

Review result: passed.

### P4.22

Commit: `74aae34 Add dense semantic calibration fixtures`

- Added dense semantic calibration fixtures.
- Promoted safe dense real-layout abstractions into synthetic fixtures.
- Covered repeated browser navigation labels, desktop settings static/disabled neighbor conflicts, canvas/HUD visual-vs-OCR disagreement, and adjacent actionable fused controls.
- Semantic synthetic suite reached 18 cases.

Review result: passed.

### P4.23

Commit: `4ac24d8 Add modal capture scale semantic fixtures`

- Added modal/capture-scale semantic regressions.
- Covered foreground modal priority, nested popover/background conflict selection, nonzero origin with 1.25 scale, Retina-style 2.0 scale, abnormal fractional scale fail-closed, and edge-offset low-confidence selection.
- Semantic synthetic suite reached 24 cases.

Review result: passed.

### P4.24

Commit: `9b9e544 Add multi-window semantic calibration fixtures`

- Added multi-window and clipped-capture semantic regressions.
- Covered stale UIA snapshots, clipped foreground-edge candidates, overlapping background-window controls, truncated dropdown edge candidates, partial capture-rect fail-closed behavior, and a positive partial-capture approval path.
- Semantic synthetic suite reached 30 cases.

Review finding:

- P2: UIA/screen_bbox candidates could still create click args outside the trusted `capture_rect`. The code checked that the screen bbox overlapped the screenshot, but did not verify that the final original screen center was inside the trusted capture rectangle.
- Minimal reproduction at review time: clipped UIA bounds could produce `{'x': 410, 'y': 95}` with a `capture_rect` whose x range ended at 400.

Next direction at the time: P4.25 fix UIA screen-bounds capture trust.

### P4.25

Commit: `adc22c1 Fix UIA capture rect click trust`

- Added center-point trust checks for UIA/screen_bbox click grounding.
- Direct approvals and selected-candidate continuations now fail closed unless the original screen-bounds center sits inside the trusted capture rectangle.
- Clamped previews can still render for partially visible controls.
- Added fixtures for clipped UIA center outside, selected clipped UIA still untrusted, and trusted UIA inside approval.
- Semantic synthetic suite reached 33 cases.

Review result: passed.

### P4.26

Commit: `c176c9a Add multi-monitor semantic calibration fixtures`

- Added multi-monitor and mixed-scale semantic regressions.
- Covered negative-origin active-window offsets, moved-window stale bounds, mixed Retina/non-Retina capture scaling, overlapping same-label windows, and dense repeated actionable labels.
- Semantic synthetic suite reached 38 cases.

Review status:

- Needs formal `review4.26` in the normal review cadence. Do not assume it passed until reviewed.

### Autopilot Skeleton

Commit: `ddb624b Add Codex-only autopilot skeleton`

- Added `tools/joi_autopilot.py`.
- Added `docs/REVIEW_HANDOFF.md`, `docs/AUTOPILOT_LOG.md`, `docs/AUTOPILOT_NATIVE_WORKFLOW.md`, `docs/AUTOPILOT_RUNTIME_SPIKE.md`, and `requirements-autopilot.txt`.
- Default runtime uses local Codex CLI signed in with ChatGPT subscription access.
- OpenAI Agents SDK + Codex MCP is recorded as a richer optional architecture, but not the default because API-key usage is billed separately.
- The autopilot runner is safe by default: no run on `main` under strict mode, no push, no merge, no PR creation, one small loop, and preflight checks for ChatGPT Codex login.

## Current State

As of commit `ddb624b`:

- Mainline has strong P4 Computer Use / Watch / semantic target grounding foundations.
- The semantic grounding suite is documented at 38 committed synthetic cases after P4.26.
- Runtime settings have safe non-secret preview/apply support.
- Codex task audit is sanitized and fail-closed for unresumable permission prompts.
- Codex-only autopilot skeleton exists but is not yet proven on Windows.

## Known Open Work

Priority order:

1. Formal `review4.26`.
2. Run Windows preflight for `tools/joi_autopilot.py`.
3. Test a docs-only autopilot loop on a safe branch such as `codex/nightly-autopilot`.
4. Continue local private semantic calibration for larger dense browser/desktop/game layouts, especially focus churn, cross-monitor drag/drop states, and dense game/canvas HUD overlays.
5. Keep real screenshots/manifests private and promote only safe synthetic fixtures.
6. Revisit real Codex permission resume only if a documented permission-specific resume contract appears.
7. Full provider/secret runtime settings UI remains deferred.
8. Non-Windows Computer Use remains deferred until Windows-first loop is reliable.

## OpenHuman-Inspired Direction

On 2026-05-21, `tinyhumansai/openhuman` and `tinyhumansai/openhuman-skills` were reviewed for product/architecture inspiration. The useful lesson is not to copy a generic Personal AI OS, but to strengthen Joi's embodied agent-companion layer.

After P4 reaches a stable Computer Use / Watch grounding收口体验, the preferred post-P4 order is:

1. P5 Memory Core: local SQLite plus human-readable Markdown summaries, explicit save/delete controls, and no automatic sensitive memory.
2. P6 JoiJuice: central tool-result compression into planner state, UI cards, voice-safe lines, memory candidates, and audit logs.
3. P7 Model Router: stable model routes for `fast`, `reasoning`, `vision`, `code`, `summarize`, and `voice_style`.
4. P8 Skill Manifest V1: make Codex, Browser/Computer Use, OK-WW, Memory, ASR, and TTS auditable native skills before chasing broad integrations.
5. P9 Background Companion Loop: constrained observation/summarization for user-approved windows, projects, and games.
6. P10 Packaging / RC: Windows-first setup, privacy panel, provider checks, and demo scripts.

Do not let this direction distract from the current P4 closeout. OpenHuman is a reference for memory, model routing, native tools, and skill packaging; Joi's differentiator remains character presence plus auditable high-capability action.

## Required Review/Verification Pattern

For semantic grounding changes:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python tools/generate_visual_fixtures.py
git diff --stat
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python -m compileall -q agent_companion run_agent_companion_tests.py tools/smoke_ws_bridge.py tools/eval_visual_detector.py tools/generate_visual_fixtures.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python tools/eval_visual_detector.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache .venv/bin/python run_agent_companion_tests.py
cd agent_companion/shell && npm run build
cd agent_companion/shell && npm run tauri -- build --debug
git diff --check
```

For autopilot preflight:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight --strict
```

Before commits, scan for private/persona/path leakage in touched docs/fixtures/tools.

## Agent Instruction

When summarizing Joi progress, do not only mention the latest autopilot work. Include the main P4 product progress, especially Computer Use, Watch grounding, semantic target calibration, runtime settings, and Codex audit safety.
