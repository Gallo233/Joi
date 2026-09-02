# Joi Known Issues

> Three headings, because "issue" was covering three different things: defects
> that are open, capabilities that have never been exercised on real hardware,
> and boundaries that are deliberate. Listing a fail-closed design beside an
> unfixed bug makes both harder to read.

## Open

- Joi Web has passed local broker/Core/dual-WebSocket integration and browser
  builds, but has not run behind Caddy on a real VPS/domain or inside the actual
  personal-site repository. TLS, systemd hardening, real-provider billing,
  microphone behavior, 50-session load and the final site layout remain
  deployment-owner checks; none is claimed by the local evidence.
- The curated JoiDebug seed preserves the asset set selected for Web, but its
  own manifests do not yet establish public-deployment rights for every file:
  AvatarSample_A says the bundled official VRMA samples must not be exported or
  shared while present, and the Miku package labels itself local-test-only. The
  seed report proves what was copied; it is not a licence grant. Resolve or
  replace those rights before publishing the generated seed from a public VPS.
- Only the earliest ten Minecraft primitives have ever run against a real world.
  The other fourteen actions, combat, screen evidence, plans, autonomy and the
  in-game chat channel are verified offline against the deterministic fake
  world, which has no pathfinder, no hostiles, no other players and no real
  latency. `docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md` is the checklist that
  closes this, and it needs a Java Edition world opened to LAN.
- Realtime voice latency is instrumented but not measured. Developer mode
  reports each turn's silence-to-audio breakdown and a running P50/P95; no
  real-microphone figures have been recorded. Sentence-level streaming TTS and
  the AudioWorklet capture path remain undone.
- Multi-display coordinates have no real-hardware evidence. The capture path
  resolves which display a window sits on, subtracts that display's origin
  before scaling, and uses its own backing scale; a crop that had to be clamped
  is reported `geometry_trusted=False` rather than returned as a smaller view.
  All of that is covered by `tests/test_capture_geometry.py` and
  `tests/test_mac_capture_path.py` against synthetic layouts. None of it has run
  on a second physical monitor.
- OCR needs the `tesseract` executable and the language data for whatever
  `ocr.language` asks for; pip can supply neither. The wrapper and Pillow ship
  with every build, so installing Tesseract -- or pointing `ocr.tesseract_cmd`
  at it -- is all a user needs, and readiness now checks the configured
  languages rather than only the binary. `tools/joi_doctor.py` and the provider
  preflight name whichever piece is missing.

## Deliberate boundaries

These are the designed behaviour, recorded so they are not rediscovered as bugs.

- Joi is distributed from GitHub without Apple notarization. macOS blocks the
  first open and the user allows it in System Settings; `docs/INSTALL_MACOS.md`
  is the path they follow. The build is still signed -- ad-hoc when no Developer
  ID is present -- because an unsigned bundle seals no resources and macOS calls
  that damaged rather than unverified, which reads as a broken download.
  `tools/build_macos_release.py` reads the signature back off the product and
  fails when the seal is missing.
- Joi lets users import their own Live2D models, which Live2D's terms class as
  an Expandable Application requiring a separate agreement. Shipping without
  that agreement is a deliberate decision, taken alongside how comparable
  projects distribute. The obligations that stand either way are met: the
  Cubism runtime is not covered by this repository's licence, the default model
  is credited as Live2D's own sample character Hiyori Momose, her design is
  unmodified, and she is not presented as an original character. What is not yet
  met is that those notices only exist in the repository -- the shipped app has
  no screen that displays them.

- macOS Accessibility and Screen Recording permissions are required for
  observation and Computer Use, and missing ones fail clearly rather than
  degrading silently.
- Desktop workflow actions are confirmed step by step. Batch auto-approval is
  not implemented, and sensitive actions never become silently automatic.
- The subconscious loop auto-approves only low-risk candidates (preference,
  fact, note); anything else waits for review.
- Watch Together commentary uses the configured vision model; with none
  configured it falls back to OCR-based context rather than guessing.
- The LLM planner requires a configured text model and falls back to the
  rule-based planner when there is none.
- Anonymous Joi Web deliberately exposes only chat, character read/activation,
  read-only memory and bounded voice. It does not expose screen observation,
  Computer Use, browser, files, Codex, Minecraft, settings or BYOK. Its IP and
  shared budget windows reset on a UTC day, and an expired session workspace is
  deleted rather than restored.
- Realtime Voice requires provider network access and a configured local
  GPT-SoVITS service. With the local voice unavailable, captions continue and
  Joi stays muted rather than substituting a system voice.
- A Minecraft disconnect is fail-closed: recovery requires a new explicitly
  confirmed session rather than replaying what was in flight.
- Minecraft version support trails the game. The bridge accepts mineflayer's
  tested versions plus the releases in `compatibleVersions` in
  `agent_companion/adapters/minecraft-bridge/index.js` (currently `26.1`); a
  newer release needs a shim for whatever packets it reshaped and a verified
  real join before that list grows.

## Fixed

- Screen capture and multi-display geometry now ship. Pillow sat in the optional
  OCR list and the macOS geometry package was installed by no release, so a
  signed build reported its screen observer unavailable and refused to derive
  click points, while the development environment did both. Both are declared
  dependencies now, and `packaging_smoke` fails if either stops shipping.
- A deleted conversation no longer survives in the copy taken before the SQLite
  migration. `forget()` reaches `events.jsonl.pre-sqlite-backup` as well as the
  live log.
- Codex run artifacts are bounded and reached by deletion.
- The Core regression suite runs in a throwaway workspace instead of the
  checkout.
- Plugin discovery is no longer a limitation: the loader that executed arbitrary
  Python inside Core was removed, along with the directory it scanned.
- Retina display coordinate mapping now uses screencapture pixel dimensions / logical screen dimensions.
- macOS clipboard no longer uses PySide6 (thread-safe pbcopy/pbpaste).
- Tool result compression removes heavy fields (screenshots, paths, raw logs) before LLM context.
- Memory FTS5 index auto-syncs on schema migration.
- Settings panel redesigned with sidebar navigation.
- Mascot mood state machine with 6 automatic states.
- Open app fallback: Spotlight → open -a.
