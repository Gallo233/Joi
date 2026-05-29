# Joi Roadmap

Joi 的目标是一个角色人格包裹的多模态 Agent 伴侣：能陪看网页/视频/游戏画面，能执行游戏技能，也能通过 Codex/MCP/本地工具推进工程任务。

OpenHuman-inspired direction is recorded in `docs/OPENHUMAN_INSIGHTS.md`. The key update is that Joi should become an embodied personal agent companion, not a generic integration dashboard: after P4, prioritize local memory, tool-result compression, model routing, skill manifests, and a constrained background companion loop.

P4.32 is the closeout experience-prep milestone: it adds a reproducible real-task demo checklist and local-only sanitized report path. It is not a new capability expansion. After the closeout pass, move to P5 Memory Core unless the report shows a blocking P4 regression.

## P0 Project Discipline

Status: in progress

- Keep roadmap, changelog, known issues, and feedback log in `docs/`.
- Every user experience report should be attached to one product branch.
- Every fix should leave a short note and, after commit, a commit id.

## P1 Product Shell

Status: in progress

- Hide raw tool/event names from the default UI.
- Show normal chat as dialogue only.
- Show tool work as task cards with concise status.
- Add a developer mode for raw event inspection.
- Add one-click Windows launch.
- Replace placeholder stage with character assets. Local configured sprites are supported; bundled original expression/state variants are next.

## P2 Vision Layer

Status: in progress

- Add screenshot capture for browser, active window, and full screen. Windows active-window/fullscreen capture is connected through `observe.screen`.
- Add `VisionSummarizer` with configurable vision model. OpenAI-compatible vision summaries are connected.
- Add OCR grounding for visible text and rough screen regions. Optional `pytesseract`/Pillow OCR is best-effort; unavailable or timed-out OCR does not fail screenshots or Watch Together.
- Group OCR text into coarse regions and expose semantic target candidates for phrases such as "登录按钮" or "右上角".
- Add optional Windows accessibility-tree snapshots for active-window UI controls, including name, role, bounds, enabled, and clickable state.
- Attach active-window capture rectangles and preview boxes so semantic targets can be reviewed visually before approval.
- Rank semantic target candidates with explainable confidence and hold ambiguous matches for user clarification.
- Continue pending semantic target selection from user phrases such as "选 2" or from candidate-card buttons, while preserving click approval.
- Bind candidate-card selection to explicit session-only `selection_id` values so old cards cannot accidentally reuse the newest pending target context.
- Show semantic target evidence cards with source, confidence band, ambiguity/actionability gates, capture trust, and a plain confirmation reason.
- Separate text model, vision model, and expression model. Model routing is connected for text, vision, and expression.
- Do not mark blank pages or failed captures as success.

## P3 Computer Use Adapter

Status: in progress

- Add `agent_companion.core.computer_use` with observation, action, backend, and result schemas.
- Route `observe.screen` through the computer-use observation chain.
- Add `computer.click`, `computer.type_text`, `computer.scroll`, and `computer.hotkey` adapters.
- Keep all computer actions at medium risk by default and require user confirmation.
- Bind confirmations to one-time `approval_id`, task id, step index, tool, and arguments hash.
- Observe the active window after each successful computer action and attach the after screenshot to the task card.
- Compare before/after observations for confirmed actions and label the task card as changed, likely no-op, or unavailable without overclaiming success.
- Run configured OCR on Computer Use before/after observations and wait briefly after actions so real Windows UI updates can settle before verification.
- Resolve semantic click requests into candidate OCR regions first, then ask for approval before executing the synthesized click.
- Fuse accessibility-tree controls with OCR boxes for semantic target grounding, preferring real UI names/roles when available and falling back to OCR when unavailable.
- Convert approved semantic target centers from screenshot-relative OCR bbox to Windows screen coordinates only when capture rect and scale are available.
- Continue ambiguous semantic target selections from the saved session context and require a fresh approval before clicking.
- Candidate-card selection now uses explicit `selection_id`/rank RPC; text and voice phrases such as "选 2" remain a latest-context fallback.
- Candidate and approval cards explain why confirmation is needed while keeping bbox, ids, paths, logs, and commands out of spoken lines.
- Use clipboard paste for reliable Windows text input, including Chinese.
- Show friendly action summaries in task cards while keeping coordinates, text payloads, command-like details, and raw ids out of voice lines.

## P4 Watch Together Loop

Status: closeout prep

- Watch together: observe visible content, summarize, and discuss. Screen capture and optional visual model summarization are connected.
- Keep recent watch context in the session: user question, window title, summary, screenshot artifact, and model status.
- Keep recent OCR snippets in the watch session so follow-up questions can cite visible text, titles, labels, and button-like strings.
- Route follow-up questions like "what did you see" through recent visual context instead of repeating screenshots.
- Generate watch follow-up answers through the role-aware text/expression model when available, with template fallback when not configured.
- Keep screen observations out of long-term memory by default; only session watch context is retained unless the user explicitly asks to save it.
- Show screenshot artifacts as clickable task-card thumbnails instead of plain labels.
- If the vision model is unavailable, save the screenshot and clearly tell the user that vision configuration is needed for summaries.
- Game: OK-WW dry-run, approval, launch, status callback.
- Coding: Codex approval, execution, task card, result summary.
- Closeout harness: `docs/P4_CLOSEOUT_EXPERIENCE.md` defines four real experience scripts for browser buttons, Watch Together, canvas/video controls, and game/HUD. Local results go to ignored `data/local_visual_eval/p4_closeout_report.local.md`.

Remaining P4 closeout risks:

- UIA quality depends on the target program.
- Canvas/game target discovery still uses lightweight heuristics.
- Overlay/capture trust needs real multi-monitor and display-scaling validation.

## P5 Memory Core

Status: in progress

- Add local SQLite storage for user preferences, project summaries, game habits, recent task outcomes, and companion relationship notes.
- Add human-readable Markdown summaries for durable handoff, similar to a local memory vault.
- Keep screen observations, raw OCR, screenshots, logs, local paths, and secrets out of long-term memory by default.
- Add explicit UI actions for remember, forget, delete, and disable memory.
- Emit `memory_candidate` records from Codex, browser/watch, game skill, and normal chat flows, but save only after policy allows it.

## P6 JoiJuice Tool Compression

Status: planned

- Centralize tool-result splitting into `agent_state`, `display_card`, `voice_line`, `memory_candidate`, and `audit_log`.
- Compress large tool outputs before they reach planner/model context.
- Keep `voice_line` free of JSON, ids, raw commands, paths, tokens, logs, coordinates, screenshots, provider names, and inflated results.
- Add tests for Codex logs, browser/OCR output, Computer Use events, ASR/TTS errors, and game-skill results.

## P7 Model Router

Status: in progress

- Route chat, coding, vision, and expression to different models.
- Record provider, model, latency, and fallback reason.
- Show current model usage in settings.
- Keep stable route labels: `fast`, `reasoning`, `vision`, `code`, `summarize`, and `voice_style`.
- Core router now accepts `llm.routes` overrides, preserves `text`/`expression` aliases, and reports only safe model usage metadata.

## P8 Voice, Expression, And Skill Manifest

Status: in progress

- Add click-to-record voice input. Shell recording, explicit ASR readiness, transcript display, OpenAI-compatible ASR, payload limits, and JSON-RPC routing are connected.
- Harden voice runtime safety: oversized base64 is rejected before decode, recorded blobs are size-checked before upload, and ASR timeout/error paths produce friendly task cards.
- Keep `MockAsrProvider` for tests/developer mode only; production microphone UI is disabled when ASR is not configured.
- Serialize `user.message`, `voice.transcribe`, and `approval.resolve` mutations through a Core command lock.
- Queue voice separately from text display and suppress stale voice audio with a client-side epoch when user intent changes.
- Align voice transcription RPC timeout with configured ASR timeout so ASR failures surface as friendly Joi messages.
- Match voice audio by event identity, including timestamp, to avoid collisions from repeated task lines.
- Show sanitized ASR/TTS runtime status in developer mode.
- Avoid speaking logs, JSON, paths, commands, tool ids, and inflated results.
- Formalize native Joi skills with manifest, input schema, result schema, permission level, dry-run support, local capability checks, state policy, and tests.
- Treat Codex, Browser/Computer Use, OK-WW, Memory, ASR, and TTS as native core skills before chasing broad third-party integrations.
- Native skill manifest V1 now reports built-in skill ids, tool/RPC bindings, permission level, dry-run support, local capability, state policy, and audit policy through safe `core.ready` / `skills.list` payloads.
- Plan, approval, tool result, task lifecycle, and Computer Use audit events now carry native skill boundary metadata for permission and audit UI work.
- Native skill enable switches are now safe runtime config fields; disabled skills show as off in the manifest and are blocked by policy before approval or execution.

## P9 Policy, Audit, And Background Companion Loop

Status: in progress

- Low risk actions run directly.
- Medium risk actions require task-level confirmation.
- High risk actions require step-by-step confirmation.
- Persist audit records for tool actions and approvals.
- Persistent audit V1 now records approval, tool, task, and policy-block lifecycle rows to a local sanitized JSONL and exposes safe status plus `audit.recent`.
- Add constrained background observation only for user-approved windows, projects, and games.
- Background context controls now require an approved window/project/game scope and store summary-only context, with no video recording by default.
- Summarize approved context without recording video by default.
- Let users inspect, clear, or disable background context.
- Shell developer controls now expose background status, approved scopes, recent summaries, disable, clear, and scope approval.

## P10 Packaging

Status: in progress

- Windows-first release build.
- Portable Windows release packager now creates a safe zip from allowlisted runtime files and the release shell.
- First-run setup checklist.
- First-run doctor now checks Python packages, optional OCR/audio packages, frontend toolchain, shell build state, `config.yaml`, Tesseract, and Core port readiness.
- Packaging smoke now validates version alignment, Tauri shell metadata, window permissions, and launcher wiring.
- Mac handoff kept current.
- CI workflow now runs Python tests, packaging smoke, frontend build, and Tauri debug no-bundle build on Windows.
