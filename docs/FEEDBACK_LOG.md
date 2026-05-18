# Joi Feedback Log

Use this file to keep user experience feedback tied to product branches.

| Date | Branch | Feedback | Status | Resolution |
| --- | --- | --- | --- | --- |
| 2026-05-14 | Product Shell | UI still looked like a debug state with raw task events. | fixed | Raw events moved behind developer mode; the default shell now separates task cards, chat, approvals, and character speech. |
| 2026-05-14 | Repository Hygiene | GitHub still contained old prototype files and third-party character content. | fixed | Public Git now keeps only the Joi main line, generic examples, docs, tools, tests, and shell/core code. |
| 2026-05-14 | Product Shell | Character stage still looked like a low-quality placeholder. | fixed | Removed the embedded placeholder asset and switched the shell to load local character sprites from Core configuration. |
| 2026-05-15 | Vision | Screen watching needed to move beyond browser-only observation. | doing | Added Windows screenshot observation and optional OpenAI-compatible vision summaries; OCR remains next. |
| 2026-05-18 | Computer Use | Joi needs a controlled way to operate the current computer like a real agent, while keeping actions auditable and confirmed. | doing | Added the first Computer Use adapter, routed screen observation through it, and gated click/type/scroll/hotkey as medium-risk confirmed actions. |
| 2026-05-14 | Game Skill | OK-WW appeared to work, but wording should not overclaim completion. | fixed | OK-WW status copy now says the skill accepted/took over unless completion is proven. |
| 2026-05-14 | Core/Codex | Codex was reported missing even though the desktop app includes a codex alias. | fixed | Codex adapter now probes `codex --version` before falling back to path lookup. |
| 2026-05-14 | Vision | "陪我看视频" could observe `about:blank` and still report success. | fixed | Blank observations are treated as failed/no-content results. |
