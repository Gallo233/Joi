# Joi Review Handoff

Status: Blocked on Codex CLI preflight
Updated: 2026-05-21
Runtime: Codex CLI with ChatGPT subscription login

## Review Status

P0 Codex-only autopilot skeleton has passed the docs-only write smoke, but Windows preflight cannot read the current Codex CLI login state because the discovered WindowsApps `codex.exe` returns `PermissionError`. The runner now fails closed instead of crashing. This file is the shared state mailbox between the developer, tester, and reviewer roles. The default runtime intentionally avoids OpenAI API keys so it can use the existing Codex/ChatGPT subscription login.

## Current Guardrails

- Never push `main`.
- Never merge.
- Never auto-create PRs.
- Never delete files unless the current task explicitly requires it.
- One loop means one small implementation or one review.
- Use a dedicated worktree/branch for autonomous runs.
- Stop if network, credentials, or permissions are required.
- Stop if acceptance commands fail twice.
- P1/P2 review findings must be fixed before new feature work.

## Next Task

P0.1 Codex CLI Preflight Accessibility

Resolve the local Codex CLI login-status probe so `tools/joi_autopilot.py --preflight` can report a real ChatGPT login state without requiring OpenAI API keys. Keep the runner fail-closed if the command is missing, inaccessible, or API-key authenticated.

## Acceptance Commands

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 -m compileall -q tools/joi_autopilot.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight --strict
git diff -- tools/joi_autopilot.py docs/REVIEW_HANDOFF.md docs/AUTOPILOT_LOG.md
git status --short
```

## Worker Result

Implemented P0 docs-only smoke by adding `docs/AUTOPILOT_SANDBOX_TEST.md`.

Hardened `tools/joi_autopilot.py` so subprocess launch failures return a preflight check result instead of a Python traceback.

Validation:

- `python -m compileall -q tools/joi_autopilot.py`: passed.
- `python tools/joi_autopilot.py --preflight`: failed closed with `codex ChatGPT login: PermissionError`.
- Elevated Windows preflight produced the same fail-closed result.

## Reviewer Result

Pending.
