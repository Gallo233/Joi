# Joi Architecture

## Product Shape

Joi is a character-fronted multimodal agent. The character is not a skin for chat only; it is the policy and expression layer around high-capability tools.

## Runtime Layers

- `observe`: screen/window/browser observation, OCR hooks, game state hooks.
- `think`: planner, character harness, memory retrieval, task decomposition.
- `act`: Codex, browser, MCP, shell/files, game skills.
- `policy`: risk classification, approval, audit log, denial handling.
- `express`: display cards, voice lines, sprite/emotion, bounded semantic character motions, task timeline.

## Result Contract

Every tool must return:

- `agent_state`: structured data for later planning.
- `display_card`: human-readable UI card.
- `voice_line`: short speakable line after sanitizer.

The voice line must never include JSON, paths, command flags, task ids, tokens, or logs.

## First Demonstrations

- Coding: user asks for a code task, Codex executes after confirmation.
- Watch together: user asks about current screen/video/page, observe adapter records the request and returns a card.
- Game: user asks for Wuthering Waves automation, OK-WW adapter dry-runs and asks confirmation before launch.

## Frontend Boundary

The Tauri/Vue shell consumes `AgentEvent` objects and renders:

- character stage
- speech bubble
- task card
- event timeline
- approval prompts

The shell should not parse raw tool output. It receives already-separated display and voice fields from core.

The anonymous website reuses this Shell through a browser platform adapter and
two iframes connected to one isolated Core. It is a deliberately narrower
transport profile, not another runtime: Core enforces Origin, RPC and paid-usage
limits before the ordinary application layers. See
`docs/WEB_EXPERIENCE_ARCHITECTURE.md` for the broker and deployment boundary.
