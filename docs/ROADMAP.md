# Joi Roadmap

Joi 的目标是一个角色人格包裹的多模态 Agent 伴侣：能陪看网页/视频/游戏画面，能执行游戏技能，也能通过 Codex/MCP/本地工具推进工程任务。

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
- Use clipboard paste for reliable Windows text input, including Chinese.
- Show friendly action summaries in task cards while keeping coordinates, text payloads, command-like details, and raw ids out of voice lines.

## P4 Watch Together Loop

Status: in progress

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

## P5 Model Router

Status: in progress

- Route chat, coding, vision, and expression to different models.
- Record provider, model, latency, and fallback reason.
- Show current model usage in settings.

## P6 Voice and Expression

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

## P7 Policy and Audit

Status: in progress

- Low risk actions run directly.
- Medium risk actions require task-level confirmation.
- High risk actions require step-by-step confirmation.
- Persist audit records for tool actions and approvals.

## P8 Memory

Status: planned

- Store local user preferences, project context, game habits, and task summaries.
- Add UI controls to view, delete, and disable memory.
- Mark memory entries as ephemeral/sensitive so screen observations do not become long-term memory by default.
- Keep sensitive content out of long-term memory unless explicitly saved.

## P9 Packaging

Status: planned

- Windows-first release build.
- First-run setup checklist.
- Mac handoff kept current.
- CI for Python tests, frontend build, and Tauri smoke build.
