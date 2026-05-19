# Joi Feedback Log

Use this file to keep user experience feedback tied to product branches.

| Date | Branch | Feedback | Status | Resolution |
| --- | --- | --- | --- | --- |
| 2026-05-14 | Product Shell | UI still looked like a debug state with raw task events. | fixed | Raw events moved behind developer mode; the default shell now separates task cards, chat, approvals, and character speech. |
| 2026-05-14 | Repository Hygiene | GitHub still contained old prototype files and third-party character content. | fixed | Public Git now keeps only the Joi main line, generic examples, docs, tools, tests, and shell/core code. |
| 2026-05-14 | Product Shell | Character stage still looked like a low-quality placeholder. | fixed | Removed the embedded placeholder asset and switched the shell to load local character sprites from Core configuration. |
| 2026-05-15 | Vision | Screen watching needed to move beyond browser-only observation. | doing | Added Windows screenshot observation, optional OpenAI-compatible vision summaries, and best-effort OCR grounding for visible text. |
| 2026-05-18 | Computer Use | Joi needs a controlled way to operate the current computer like a real agent, while keeping actions auditable and confirmed. | doing | Added the first Computer Use adapter, routed screen observation through it, and gated click/type/scroll/hotkey as medium-risk confirmed actions. |
| 2026-05-18 | Policy | Computer Use approvals should not be bypassable or reusable. | fixed | Removed `user.message` approval, added one-time approval ids bound to task/step/tool/argument hash, and covered reuse/bypass tests. |
| 2026-05-18 | Computer Use | After actions, users need visual confirmation of what changed. | fixed | Computer actions now automatically observe the active window and attach the after screenshot to the task card. |
| 2026-05-18 | Watch Together | "陪我看" needs to feel like a continuous shared-viewing loop instead of repeated mechanical screenshots. | doing | Added session watch context and follow-up recall so recent summaries, titles, questions, and screenshot artifacts are reused for natural answers. |
| 2026-05-18 | Watch Together | Watch summaries should not silently become long-term memory, and screenshot results need to be visually inspectable. | fixed | Watch observations now stay session-only by default, and task cards render screenshot thumbnails with a preview modal. |
| 2026-05-18 | Voice Input | Joi needs a first microphone path without always-on recording or audio retention. | doing | Added click-to-record UI state, a mock ASR provider, and `voice.transcribe` JSON-RPC routing into the existing user-message and approval flow. |
| 2026-05-19 | Voice Input | The microphone should not pretend to understand speech when real ASR is not configured. | fixed | Added ASR config parsing, disabled/unconfigured UI state, OpenAI-compatible ASR, payload limits, transcript display, and serialized Core command handling. |
| 2026-05-19 | Voice Input | Voice runtime should not freeze silently or let oversized audio stress the Core. | fixed | Added pre-decode base64 limits, shell Blob limits, ASR timeout handling, friendly voice error task cards, and stale audio interruption. |
| 2026-05-14 | Game Skill | OK-WW appeared to work, but wording should not overclaim completion. | fixed | OK-WW status copy now says the skill accepted/took over unless completion is proven. |
| 2026-05-14 | Core/Codex | Codex was reported missing even though the desktop app includes a codex alias. | fixed | Codex adapter now probes `codex --version` before falling back to path lookup. |
| 2026-05-14 | Vision | "陪我看视频" could observe `about:blank` and still report success. | fixed | Blank observations are treated as failed/no-content results. |
