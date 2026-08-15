# Joi Known Issues

## Active

- macOS accessibility observer requires Accessibility permission in System Settings → Privacy & Security.
- Multi-monitor Retina configurations may have coordinate offset for windows on secondary displays.
- Watch Together commentary depends on configured vision model; without it, only OCR-based context is available.
- Desktop workflow actions are step-by-step confirmed; batch auto-approval is not yet implemented.
- Plugin discovery only scans `agent_companion/plugins/`; user workspace plugins need config support.
- Subconscious loop auto-approves only low-risk candidates (preference/fact/note); complex memories require manual review.
- LLM planner requires configured text model; falls back to rule-based planner when unconfigured.
- Realtime Voice requires Qwen Audio Realtime network access and a configured local GPT-SoVITS service; when the local voice is unavailable, captions remain available but Joi stays muted.
- Minecraft real-server smoke requires a Java Edition world opened to LAN and the displayed ephemeral LAN port. Disconnect recovery is fail-closed and requires a new explicitly confirmed session rather than automatic replay.
- Minecraft version support trails the game: the bridge accepts mineflayer's tested versions plus the releases listed in `compatibleVersions` in `agent_companion/adapters/minecraft-bridge/index.js` (currently `26.1`). A newer release needs a shim for whatever packets it reshaped and a verified real join before that list grows.

## Fixed

- (v0.2.0) Retina display coordinate mapping now uses screencapture pixel dimensions / logical screen dimensions.
- (v0.2.0) macOS clipboard no longer uses PySide6 (thread-safe pbcopy/pbpaste).
- (v0.2.0) Tool result compression removes heavy fields (screenshots, paths, raw logs) before LLM context.
- (v0.2.0) Memory FTS5 index auto-syncs on schema migration.
- (v0.2.0) Settings panel redesigned with sidebar navigation.
- (v0.2.0) Mascot mood state machine with 6 automatic states.
- (v0.2.0) Open app fallback: Spotlight → open -a.
