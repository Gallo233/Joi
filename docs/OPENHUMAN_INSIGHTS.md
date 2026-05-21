# OpenHuman-Inspired Joi Plan

Date: 2026-05-21

This note records the product and architecture lessons Joi should absorb from `tinyhumansai/openhuman` without turning Joi into a generic productivity assistant.

## Source Projects Reviewed

- `tinyhumansai/openhuman`: personal AI desktop app with memory, native tools, model routing, voice, browser/computer control, and integration-first workflows.
- `tinyhumansai/openhuman-skills`: skill package direction with manifests, schemas, setup, local state, and scheduled/background execution.

## Product Takeaway

OpenHuman is closest to a local Personal AI OS. Joi should not copy that positioning directly. Joi's sharper product thesis is:

> an embodied personal agent companion: a character-fronted local agent that can watch, remember, act through tools, and express progress naturally.

The market gap Joi should focus on is not "more integrations". It is a trusted agent with presence:

- character expression and voice around high-capability tools
- screen/game/web context as first-class input
- auditable semi-automatic actions
- local memory with explicit user control
- coding, watching, and game-skill loops that feel coherent instead of bolted together

## Ideas To Absorb

### 1. Local Memory Tree

OpenHuman's memory direction suggests Joi needs a durable local memory layer, not only chat history.

For Joi, memory should store:

- user preferences and recurring instructions
- project summaries and recent coding-task outcomes
- game habits and skill preferences
- watch-session summaries only when explicitly saved
- relationship/persona notes for the active companion

Sensitive screen observations, raw OCR, private screenshots, logs, local paths, and secrets must not enter long-term memory by default.

### 2. JoiJuice Tool Compression

OpenHuman's token compression is a strong fit for Joi. Joi should formalize its existing no-machine-speech rule into a tool result compression layer.

Every tool result should be split into:

- `agent_state`: structured planner input
- `display_card`: human-readable UI result
- `voice_line`: short natural line for TTS
- `memory_candidate`: optional sanitized memory summary
- `audit_log`: developer/debug trace with sanitized technical detail

`voice_line` must never contain JSON, ids, raw commands, paths, tokens, logs, coordinates, screenshots, provider names, or large outputs.

### 3. Model Router

OpenHuman's model routing confirms that Joi should not run every task through one model.

Joi should keep route labels stable:

- `fast`: short chat, UI copy, lightweight persona response
- `reasoning`: planning and multi-step tool use
- `vision`: screenshots, screen summaries, visual grounding
- `code`: Codex or coding-task orchestration
- `summarize`: memory, logs, diffs, OCR compression
- `voice_style`: turning tool outcomes into character-safe spoken lines

The router should record provider, model, latency, fallback reason, and whether the output was safe for speech.

### 4. Native Core Tools First

OpenHuman treats browser, computer use, files, voice, and scheduling as native tools. Joi should follow the same pattern for its MVP pillars:

- Watch / screen observe
- Computer Use
- Browser control
- Codex coding tasks
- OK-WW / game skill adapter
- ASR / TTS
- local memory

MCP and third-party skills should extend the core, not be required for the three MVP demos.

### 5. Skill Manifest V1

OpenHuman's skill package direction is useful, but Joi should keep the first version smaller and more auditable.

Each Joi skill should eventually declare:

- `id`, `name`, `description`
- input schema and result schema
- permission level
- dry-run support
- required local capabilities
- state directory policy
- test/eval hooks
- safe display and voice summaries

OK-WW should become the first production game skill under this shape.

## Updated Post-P4 Direction

P4 remains the current Computer Use / Watch grounding track. After P4 reaches its收口体验, the recommended order becomes:

1. **P5 Memory Core**: local SQLite plus human-readable Markdown summaries, explicit save/delete controls, no automatic sensitive memory.
2. **P6 JoiJuice**: centralized tool-result splitting and compression for planner, UI, voice, memory, and audit.
3. **P7 Model Router**: stable model routes for fast/reasoning/vision/code/summarize/voice-style work.
4. **P8 Skill Manifest V1**: formalize Codex, Browser/Computer Use, OK-WW, and Memory as auditable skills.
5. **P9 Background Companion Loop**: constrained observation/summarization for user-approved windows, projects, and games.
6. **P10 Packaging / RC**: Windows-first release packaging, setup wizard, provider checks, privacy panel, and MVP demo scripts.

Voice, policy, and audit are not separate postponed features. They remain cross-cutting requirements across every stage.

## What Joi Should Not Copy

- Do not chase a large integration count before the three MVP loops feel good.
- Do not make memory always-on by default.
- Do not turn the character shell into a generic dashboard.
- Do not require OAuth/integration setup before local watch/coding/game demos work.
- Do not let skills bypass Joi's approval, audit, and voice-sanitization rules.

## Review Checklist For Future Tasks

When a new feature is proposed, ask:

1. Does it improve watch, coding, game skill, memory, or character expression?
2. Is the tool result split into planner/display/voice/memory/audit forms?
3. Can the user inspect or delete what Joi remembered?
4. Does the feature work without leaking logs, paths, secrets, ids, or private text into speech?
5. Is it native-core MVP capability, or should it wait for the skill layer?
