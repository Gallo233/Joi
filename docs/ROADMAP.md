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
- Use clipboard paste for reliable Windows text input, including Chinese.
- Show friendly action summaries in task cards while keeping coordinates, text payloads, command-like details, and raw ids out of voice lines.

## P4 First Demo Loops

Status: planned

- Watch together: observe visible content, summarize, and discuss. Screen capture and optional visual model summarization are connected; richer multi-turn discussion is next.
- Game: OK-WW dry-run, approval, launch, status callback.
- Coding: Codex approval, execution, task card, result summary.

## P5 Model Router

Status: in progress

- Route chat, coding, vision, and expression to different models.
- Record provider, model, latency, and fallback reason.
- Show current model usage in settings.

## P6 Voice and Expression

Status: planned

- Queue voice separately from text display.
- Interrupt stale voice when new user input arrives.
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
- Keep sensitive content out of long-term memory unless explicitly saved.

## P9 Packaging

Status: planned

- Windows-first release build.
- First-run setup checklist.
- Mac handoff kept current.
- CI for Python tests, frontend build, and Tauri smoke build.
