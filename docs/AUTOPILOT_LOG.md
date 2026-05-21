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
