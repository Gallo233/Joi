# Joi Known Issues

## Active

- macOS accessibility observer requires Accessibility permission in System Settings → Privacy & Security.
- Multi-monitor Retina configurations may have coordinate offset for windows on secondary displays.
- Watch Together commentary depends on configured vision model; without it, only OCR-based context is available.
- Desktop workflow actions are step-by-step confirmed; batch auto-approval is not yet implemented.
- Plugin discovery only scans `agent_companion/plugins/`; user workspace plugins need config support.
- Subconscious loop auto-approves only low-risk candidates (preference/fact/note); complex memories require manual review.
- LLM planner requires configured text model; falls back to rule-based planner when unconfigured.

## Fixed

- (v0.2.0) Retina display coordinate mapping now uses screencapture pixel dimensions / logical screen dimensions.
- (v0.2.0) macOS clipboard no longer uses PySide6 (thread-safe pbcopy/pbpaste).
- (v0.2.0) Tool result compression removes heavy fields (screenshots, paths, raw logs) before LLM context.
- (v0.2.0) Memory FTS5 index auto-syncs on schema migration.
- (v0.2.0) Settings panel redesigned with sidebar navigation.
- (v0.2.0) Mascot mood state machine with 6 automatic states.
- (v0.2.0) Open app fallback: Spotlight → open -a.
