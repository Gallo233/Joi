# Joi Changelog

## Unreleased

- Added project discipline docs: roadmap, changelog, known issues, and feedback log.
- Started product-shell cleanup: default UI separates chat, task cards, and developer events.
- Added one-click Windows launch script target for Joi.
- Verified Python core tests, Vue build, and Tauri debug packaging after the shell cleanup.
- Cleaned the public repository down to the Joi main line: removed old prototype entrypoints, third-party character assets, and local personal configuration from Git.
- Moved config parsing, GPT-SoVITS, and browser executor support into `agent_companion/` so Joi no longer depends on removed prototype modules.
- Added a first original Joi character PNG asset and wired it into the Tauri shell stage.
- Improved browser/watch task cards with product-oriented summaries, metadata chips, and non-raw artifact labels.

## 2026-05-14

- Renamed product and GitHub repository to Joi.
- Added Python WebSocket JSON-RPC bridge.
- Added LLM expression layer and GPT-SoVITS bridge.
- Connected Tauri/Vue shell to Python Core event stream.
- Connected Browser Executor and OK-WW adapter to real callback paths.
- Installed and verified Node/npm, Rust, MSVC Build Tools, and Tauri build chain.
