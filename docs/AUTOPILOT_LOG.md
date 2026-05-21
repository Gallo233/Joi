# Joi Autopilot Log

## 2026-05-20

- Created the Codex-only autopilot skeleton plan around the local Codex CLI signed in with ChatGPT subscription access.
- Established `docs/REVIEW_HANDOFF.md` as the shared mailbox for worker/reviewer handoff.
- Safe default remains docs-only, one-loop, no push, no merge, and fail-closed on missing credentials, network, or permissions.
- Avoided the OpenAI API-key Agents SDK path as the default because API-key usage is billed separately from ChatGPT/Codex subscription access.

## 2026-05-21

- Added `docs/AUTOPILOT_SANDBOX_TEST.md` as the P0 docs-only smoke change.
- Hardened `tools/joi_autopilot.py` so inaccessible local commands produce fail-closed preflight output instead of a Python traceback.
- Windows preflight currently finds the WindowsApps Codex executable, but `codex login status` returns `PermissionError`; autonomous runs remain blocked until a usable ChatGPT-authenticated Codex CLI path is available.
- Implemented P0.1 Codex CLI preflight accessibility: the runner now discovers the local Codex sandbox-bin executable, sets `CODEX_HOME`, verifies ChatGPT subscription login, checks the `codex exec` interface, adapts approval-policy arguments for older CLI builds, redacts local probe details, and reserves strict failure for unsafe branch or dirty-worktree guardrails.

- 2026-05-21T12:14:30+08:00: Codex-only autopilot run starting

- 2026-05-21T12:14:31+08:00: developer failed with exit code 1

- 2026-05-21T12:14:50+08:00: Codex-only autopilot run starting

- 2026-05-21T12:21:56+08:00: developer completed

- 2026-05-21T12:35:00+08:00: P0.2 smoke exposed a docs-only scope violation: Developer produced code/test/tool changes and the run did not reach a complete Tester/Reviewer loop.

- 2026-05-21T12:35:00+08:00: Added docs-only scope guard to stop autopilot after any role that changes files outside `docs/REVIEW_HANDOFF.md`, `docs/AUTOPILOT_LOG.md`, and `docs/AUTOPILOT_*.md`.
