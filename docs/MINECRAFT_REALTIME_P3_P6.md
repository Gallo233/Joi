# Minecraft Realtime P3–P6 closeout

## Delivered

- P3: Qwen Audio 3.0 Realtime Flash WSS transport owned by Core; 16 kHz PCM16 Shell capture; server VAD; text-only response; local GPT-SoVITS output; barge-in epoch cancellation.
- P4: Minecraft Skill setup for HMCL/LAN, companion/delegate choice, digest-bound exact scope approval, owner-bound Realtime mode, ten strict function proposals, pause/resume/cancel controls, live state and sanitized action status.
- P5: exact Mineflayer/pathfinder/npm lock, ncc Bridge bundle with pinned `minecraft-data` vendor, built-bridge fake smoke for all ten primitives, no-replay recovery smoke, private password-free connection profile.
- P6: Qwen key imported to macOS Keychain, online audio turn verified, source and package privacy gates, full Python/Shell/TypeScript/build checks, rebuilt macOS debug package.

## Trust boundaries

Cloud audio and transcripts are ephemeral. Core never writes raw audio, transcript, provider messages, request IDs or function-call IDs to SQLite/audit. Public events are a closed projection. Qwen receives only strict tool schemas and sanitized primitive/status/summary results; it never receives receipt IDs, permission IDs, scope internals, Bridge IDs, checkpoints, coordinates, inventory detail, stderr or paths.

Mineflayer has no arbitrary command/code primitive. Pathfinder uses `canDig=false`. World effects are bounded by a confirmed server/world/dimension/spatial scope and budgets, validated again for every goal. Bridge observations are evidence; Core is the permission, budget, state and receipt authority.

## Gates

- `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
- `npm run test:shell && npx vue-tsc --noEmit && npm run build`
- `.venv/bin/python tools/minecraft_p5_smoke.py`
- `.venv/bin/python tools/qwen_realtime_smoke.py --pcm <16k-mono-pcm16le-file>`
- `node --check agent_companion/adapters/minecraft-bridge/index.js`
- `.venv/bin/python tools/packaging_smoke.py`
- `npm run tauri -- build --debug`

The final real-server gate is deliberately interactive: launch HMCL, enter a Java world, choose “Open to LAN”, then provide the displayed port in Minecraft Skill. Real actions must not be attempted before that explicit user step.
