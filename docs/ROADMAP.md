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

Status: planned

- Add screenshot capture for browser, active window, and full screen. Browser executor capture is connected; active-window and full-screen capture are next.
- Add `VisionSummarizer` with configurable vision model.
- Separate text model, vision model, and expression model.
- Do not mark blank pages or failed captures as success.

## P3 First Demo Loops

Status: planned

- Watch together: observe visible content, summarize, and discuss.
- Game: OK-WW dry-run, approval, launch, status callback.
- Coding: Codex approval, execution, task card, result summary.

## P4 Model Router

Status: planned

- Route chat, coding, vision, and expression to different models.
- Record provider, model, latency, and fallback reason.
- Show current model usage in settings.

## P5 Voice and Expression

Status: planned

- Queue voice separately from text display.
- Interrupt stale voice when new user input arrives.
- Avoid speaking logs, JSON, paths, commands, tool ids, and inflated results.

## P6 Policy and Audit

Status: planned

- Low risk actions run directly.
- Medium risk actions require task-level confirmation.
- High risk actions require step-by-step confirmation.
- Persist audit records for tool actions and approvals.

## P7 Memory

Status: planned

- Store local user preferences, project context, game habits, and task summaries.
- Add UI controls to view, delete, and disable memory.
- Keep sensitive content out of long-term memory unless explicitly saved.

## P8 Packaging

Status: planned

- Windows-first release build.
- First-run setup checklist.
- Mac handoff kept current.
- CI for Python tests, frontend build, and Tauri smoke build.
