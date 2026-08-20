# Joi Known Issues

## Active

- Pillow is declared only in `requirements-ocr.txt`, which neither the CI lanes nor `release-macos.yml` install, while `vision/mac.py` imports it at module scope. macOS screen capture therefore degrades to `UnavailableScreenObserver` in a release build — honestly, as the platform contract requires, but permanently. CI now installs it so the capture path is exercised; whether the shipped sidecar should carry it is a release decision, and taking it would mean regenerating the third-party notices.
- `run_agent_companion_tests.py` runs against the repository root as its workspace, so the documented Core regression command writes real events, audit records and SQLite rows into `data/agent_companion/` on the machine running it. It is why the local event log reached 90MB. The suite needs a temporary workspace; until it has one, the command is not safe to run against an install whose data matters.
- `data/agent_companion/codex_runs/` and `codex_runtime/` grow without bound — three files per Codex run, about 11MB after roughly 1500 runs here, and nothing prunes them. They also hold the goal text and model output of runs whose conversation has since been deleted, so deletion does not reach them.
- `data/agent_companion/events.jsonl.pre-sqlite-backup` is the one-time copy taken before the SQLite migration. It still holds the conversation text of anything logged before that migration, and a later deletion does not reach it. Removing it is a maintainer decision, not an automatic one.
- macOS accessibility observer requires Accessibility permission in System Settings → Privacy & Security.
- Multi-monitor Retina configurations may have coordinate offset for windows on secondary displays.
- Watch Together commentary depends on configured vision model; without it, only OCR-based context is available.
- Desktop workflow actions are step-by-step confirmed; batch auto-approval is not yet implemented.
- Plugin discovery only scans `agent_companion/plugins/`; user workspace plugins need config support.
- Subconscious loop auto-approves only low-risk candidates (preference/fact/note); complex memories require manual review.
- LLM planner requires configured text model; falls back to rule-based planner when unconfigured.
- Realtime Voice requires Qwen Audio Realtime network access and a configured local GPT-SoVITS service; when the local voice is unavailable, captions remain available but Joi stays muted.
- Minecraft real-server smoke requires a Java Edition world opened to LAN and the displayed ephemeral LAN port. Disconnect recovery is fail-closed and requires a new explicitly confirmed session rather than automatic replay.
- Only the earliest ten Minecraft primitives have ever run against a real world. The other fourteen actions, combat, screen evidence, plans, autonomy and the in-game chat channel are verified offline against the deterministic fake world, which has no pathfinder, no hostiles, no other players and no real latency. `docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md` is the checklist that closes this.
- Realtime voice latency is now instrumented but not yet measured: developer mode reports each turn's silence-to-audio breakdown and a running P50/P95, and no real-microphone figures have been recorded. Sentence-level streaming TTS and the AudioWorklet capture path remain undone.
- Minecraft version support trails the game: the bridge accepts mineflayer's tested versions plus the releases listed in `compatibleVersions` in `agent_companion/adapters/minecraft-bridge/index.js` (currently `26.1`). A newer release needs a shim for whatever packets it reshaped and a verified real join before that list grows.

## Fixed

- (v0.2.0) Retina display coordinate mapping now uses screencapture pixel dimensions / logical screen dimensions.
- (v0.2.0) macOS clipboard no longer uses PySide6 (thread-safe pbcopy/pbpaste).
- (v0.2.0) Tool result compression removes heavy fields (screenshots, paths, raw logs) before LLM context.
- (v0.2.0) Memory FTS5 index auto-syncs on schema migration.
- (v0.2.0) Settings panel redesigned with sidebar navigation.
- (v0.2.0) Mascot mood state machine with 6 automatic states.
- (v0.2.0) Open app fallback: Spotlight → open -a.
