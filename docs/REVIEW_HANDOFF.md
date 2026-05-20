# Joi Review Handoff

Status: Ready for Work
Updated: 2026-05-20
Runtime: Codex CLI with ChatGPT subscription login

## Review Status

P0 Codex-only autopilot skeleton is ready to test in docs-only mode. This file is the shared state mailbox between the developer, tester, and reviewer roles. The default runtime intentionally avoids OpenAI API keys so it can use the existing Codex/ChatGPT subscription login.

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

P0 Native Autopilot Docs-Only Smoke

Create or update `docs/AUTOPILOT_SANDBOX_TEST.md` with one short paragraph confirming that the native autopilot loop can perform a docs-only change. Do not edit application code.

## Acceptance Commands

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 -m compileall -q tools/joi_autopilot.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
git diff -- docs/AUTOPILOT_SANDBOX_TEST.md
git status --short
```

## Worker Result

Pending.

## Reviewer Result

Pending.
