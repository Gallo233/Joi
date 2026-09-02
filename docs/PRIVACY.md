# Joi Privacy Notice

This describes the data boundary of the Joi desktop application as it is built and shipped, version 0.1.0. Where a limit is enforced by the product rather than promised by this document, that is said so.

## Data stored on the device

Joi stores projects, conversation events, capability sessions, approved memories, character runtime state, settings, and sanitized startup logs in its application-data directory — `~/Library/Application Support/com.gallo233.joi` on macOS. **设置 → 关于** names that directory inside the app; deleting it removes everything Joi holds on the machine.

Imported character packages are stored separately from conversations, long-term memory, affinity, and other runtime data, so exporting a character does not export personal history.

BYOK credentials are written to the operating system credential store when it is available. Joi's configuration files contain a credential reference, not the plaintext key; deleting the data directory therefore does not revoke a stored key, which has to be removed from the keychain or at the provider. Session connection tokens are generated per launch, kept out of logs and ready files, and are not reused after restart.

## Data sent to providers

Joi has no account and no server of its own. Nothing leaves the machine until you configure a model provider with your own key, or select Codex CLI, and then only what the task you asked for requires. Depending on the capability you approved, that can include conversation text, character instructions, selected file content, screen-derived text, screenshots, or audio transcripts. It goes to that provider under that provider's terms, not under this notice.

Sensitive actions — anything that changes state outside Joi — are confirmed step by step, and reaching beyond a project's approved resources requires a new confirmation rather than an inherited one.

There is no product analytics, no advertising telemetry, and no crash reporting in this release. None is disabled by a setting; none is present in the build. If a later release adds any, it will be an explicit, documented opt-in.

## Realtime voice

Realtime voice is a separate, explicitly started session bound to one window. The Shell captures the microphone and sends ordered 16 kHz PCM16 frames; only Core holds the provider credential, and the provider is configured to return text, never audio.

Joi's voice is always her own character voice — synthesized on the machine by GPT-SoVITS, or, when the character is configured with a cloud voice, by that voice provider, **in which case her reply text is sent to that provider too**. The consent screen names the route the machine actually has before the session starts. The realtime provider's own audio and any system voice are never used; when the character voice is unavailable, the session keeps captions and stays explicitly muted rather than substituting another voice.

Raw audio is never written to disk. When a session ends, the sanitized text pairs — what was recognized and what Joi answered — are written to the local conversation history; the disclosure shown before the microphone opens says so.

Developer mode reports per-turn latency for this path. It is whole milliseconds and a turn count only: no transcript, no identifiers, no provider detail.

## Game sessions

A game session requires an explicitly confirmed scope: server, world, dimension, radius, players, blocks, build and container permissions, and action and block budgets. Every action is checked against that scope, costs from the confirmed budget, and produces a receipt. A failed or interrupted action is never replayed automatically; recovery requires a new confirmed session.

In-game coordinates never leave the bridge child process. Observations, receipts, captions, spoken lines, memories and anything sent to a model carry names, counts, bearings and distance bands instead.

When the game screen is read, the frame is captured on this machine. Without a configured vision model, only local text recognition runs and no image leaves the device; with one configured, the frame is sent to that model for a scene summary. Core reports which of the two is active, and the disclosure describes it before the session opens. Screenshots are not retained either way.

## Screen, audio, and Computer Use

Screen Recording, Accessibility, microphone, system-audio, and Apple Events access are requested only when a feature you invoked needs them, and a missing permission is reported plainly rather than worked around. Computer Use actions remain subject to capability permissions and to confirmation for sensitive actions.

Watch Together holds captured system audio in memory for recognition and does not write it to disk; screen frames are handled the same way as game frames above.

## Deletion and export

Conversations and projects can be deleted from inside Joi, and deletion reaches the compatibility event log as well as the database — a deleted conversation is gone, not hidden. Character and Skill packages can be uninstalled. Complete erasure is deleting the application-data directory named above.

What deletion cannot reach: anything a model provider retains under its own terms after you sent it there, and copies inside operating-system backups such as Time Machine.

## Reporting

Security and privacy reports go through the private channel described in `SECURITY.md`. Do not put API keys, private screenshots, or personal conversation data in a public GitHub issue.
