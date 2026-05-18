# Joi Known Issues

## Active

- Vision now supports Windows active-window/fullscreen screenshots and optional visual model summaries, but OCR is not implemented yet.
- Computer Use actions are intentionally minimal: click/type/scroll/hotkey are Windows-only and do not yet include visual target grounding or post-action observation.
- The stage can load local character sprites from configuration, but bundled original VN expression variants and Live2D/VRM rendering are not product grade yet.
- OK-WW callback currently proves launch/return code, but does not yet read detailed in-game completion state.
- Codex permission requests do not yet flow back into Joi UI step by step.
- Model routing is split between text, vision, and expression models, but the settings UI does not yet expose current provider/model usage.

## Fixed

- Ordinary chat no longer needs to show plan/task-completed events in the product UI.
- Duplicate approval responses are ignored instead of creating a failure card.
- Blank browser observations are no longer treated as successful visual understanding.
- OK-WW lines avoid claiming that game tasks are complete unless the tool proves completion.
- Raw tool names are filtered out of voice lines.
- Computer Use voice lines avoid speaking coordinates, text payloads, JSON, command-like strings, paths, and task ids.
