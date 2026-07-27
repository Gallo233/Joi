# Joi macOS MVP Release Guide

Joi's first public package targets Apple Silicon macOS. The application bundle contains the Vue/Tauri shell and a standalone `joi-core` sidecar; release users do not need this repository, Python, npm, or Rust.

## One-time GitHub setup

Configure these repository Actions secrets:

- `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`
- `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`
- `JOI_RELEASE_ASSETS_URL`, a private HTTPS URL for a ZIP whose root contains `public/`
- `JOI_RELEASE_ASSETS_SHA256`, the SHA-256 of that ZIP

The licensed asset ZIP must contain the Live2D model and Live2D browser runtime paths listed in `agent_companion/shell/release-assets.json`. The workflow verifies both the archive and every extracted release file. Do not commit private model/runtime files merely to make CI pass.

## Local release check

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-build.txt
npm ci --prefix agent_companion/shell
.venv/bin/python run_agent_companion_tests.py
.venv/bin/python tools/packaging_smoke.py
JOI_LIVE2D_SOURCE=/absolute/path/to/public npm run tauri --prefix agent_companion/shell -- build --target aarch64-apple-darwin
```

`npm run tauri build` is deliberately fail-closed: it builds the standalone Core, requires the complete Live2D source, and verifies pinned hashes before packaging.

## Draft release

Push a semver tag such as `v0.1.0`, or manually run **Joi macOS Draft Release**. The workflow runs tests and packaging gates, creates a signed and notarized Apple Silicon package, uploads it to a draft prerelease, and records a GitHub build-provenance attestation.

Before publishing the draft:

1. Install the DMG on a clean Apple Silicon Mac that has no Python, Node, Rust, repository checkout, or Joi configuration.
2. Verify launch, native traffic-light controls, character rendering, a local conversation, BYOK setup, file/folder attachment, character switching/uninstall, memory isolation, and app relaunch.
3. Verify that `~/Library/Logs` and the Joi app-data `logs/` directory contain actionable startup errors without API keys, prompts, local file contents, or session tokens.
4. Revoke Accessibility and Screen Recording, then confirm Joi explains the missing permission without looping or silently acting.
5. Confirm Gatekeeper acceptance, signing identity, notarization, version, bundle identifier `com.gallo233.joi`, SHA-256, and download contents.

## Human/legal release blockers

- Choose and add the repository's software license. No license is selected automatically by this refactor.
- Confirm distribution rights and required notices for every model, texture, font, voice, Cubism component, and third-party dependency.
- Complete any Live2D Cubism **Expandable Application** approval required for Joi's user-importable character system before public distribution.
- Publish a concise privacy statement covering local memories, screenshots/audio, BYOK secrets, telemetry, crash reports, and deletion behavior.
- Replace the draft privacy and third-party notices with artifact-verified final copies.
- Replace the current provisional application icon if it is not the approved Joi brand asset, then regenerate PNG/ICNS/ICO together.

Until these decisions are complete, keep GitHub releases in draft/prerelease state.
