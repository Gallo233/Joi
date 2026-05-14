# Joi Known Issues

## Active

- Vision is still browser-executor based. Active-window and full-screen capture are not implemented yet.
- The stage can load local character sprites from configuration, but bundled original VN expression variants and Live2D/VRM rendering are not product grade yet.
- OK-WW callback currently proves launch/return code, but does not yet read detailed in-game completion state.
- Codex permission requests do not yet flow back into Joi UI step by step.
- Model routing is not yet split between text, vision, and expression models.

## Fixed

- Ordinary chat no longer needs to show plan/task-completed events in the product UI.
- Duplicate approval responses are ignored instead of creating a failure card.
- Blank browser observations are no longer treated as successful visual understanding.
- OK-WW lines avoid claiming that game tasks are complete unless the tool proves completion.
- Raw tool names are filtered out of voice lines.
