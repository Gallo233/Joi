# Joi MVP Privacy Notice (Draft)

This document describes the intended data boundary of the Joi desktop MVP. It must be reviewed against the final signed package before a public release.

## Data stored on the device

Joi stores projects, conversation events, capability sessions, approved memories, character runtime state, settings, and sanitized startup logs in its application-data directory. Imported character packages are stored separately from user conversations, long-term memory, affinity, and other runtime data so exporting a character does not export personal history.

BYOK credentials are written to the operating system credential store when it is available. Joi configuration files contain a credential reference, not the plaintext key. Session connection tokens are generated per launch, kept out of logs and ready files, and are not reused after restart.

## Data sent to providers

Joi does not include a shared cloud account. When the user selects Codex CLI or configures a BYOK provider, requests needed for the selected task may be sent to that provider under the provider's terms. Depending on the approved capability, this can include conversation text, role instructions, selected file content, screen-derived text, screenshots, or audio/transcripts. Joi should show the active provider and ask before expanding beyond the project's approved resources.

No product analytics or advertising telemetry is intentionally included in the MVP. Crash reporting must remain disabled unless a later release adds an explicit, documented opt-in.

## Realtime voice

Realtime voice is a separate, explicitly started session bound to one window. The Shell captures the microphone and sends ordered 16 kHz PCM16 frames; only Core holds the provider credential, and the provider is configured to return text, never audio. Joi's voice is always her own character voice — synthesized on the machine by GPT-SoVITS, or by the cloud voice that character is configured with, in which case her reply text is sent to that voice provider and the consent screen says so before the session starts. The realtime provider's own audio and any system voice are never used; when the character voice is unavailable the session keeps captions and stays explicitly muted.

Raw audio is never written to disk. When a session ends, the sanitized text pairs — what was recognized and what Joi answered — are written to the local conversation history; the disclosure shown before the microphone opens says so.

Developer mode reports per-turn latency for this path. It is whole milliseconds and a turn count only: no transcript, no identifiers, no provider detail.

## Game sessions

A game session requires an explicitly confirmed scope: server, world, dimension, radius, players, blocks, build and container permissions, and action and block budgets. Every action is checked against that scope, costs from the confirmed budget, and produces a receipt. A failed or interrupted action is never replayed automatically.

In-game coordinates never leave the bridge child process. Observations, receipts, captions, spoken lines, memories and anything sent to a model carry names, counts, bearings and distance bands instead.

When the game screen is read, the frame is captured on this machine. Without a configured vision model, only local text recognition runs and no image leaves the device; with one configured, the frame is sent to that model for a scene summary. Core reports which of the two is active, and the microphone disclosure describes it before the session opens. Screenshots are not retained either way.

## Screen, audio, and Computer Use

Screen Recording, Accessibility, microphone, system-audio, and Apple Events access are requested only when the corresponding user-invoked feature needs them. Computer Use actions remain subject to capability permissions and confirmation for sensitive actions. Watch Together should not retain raw screen video or raw system audio by default; the clean-machine release checklist must verify this behavior.

## Deletion and export

Users can delete or archive conversations and uninstall character or Skill packages from Joi. Before public release, the product must also expose one clear way to locate and erase all Joi application data and explain what deletion cannot remove from external model providers or operating-system backups.

Security or privacy reports should use the repository's private security-reporting channel once it is configured. Do not put API keys, private screenshots, or personal conversation data in a public GitHub issue.
