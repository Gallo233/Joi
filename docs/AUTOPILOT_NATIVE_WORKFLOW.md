# Joi Codex-Only Autopilot Workflow

This is the preferred no-extra-API-billing autopilot path for Joi: a small local scheduler runs the ChatGPT-authenticated Codex CLI as Developer, Tester, and Reviewer roles.

Official billing/auth baseline:

- Codex CLI supports signing in with ChatGPT for subscription access.
- Codex CLI also supports API-key usage, but API-key usage is billed through the OpenAI Platform at standard API rates.
- Joi's default autopilot path should therefore prefer the existing ChatGPT/Codex login and avoid API keys.

## Roles

- Developer: implements only the current Next Task.
- Tester: runs the current Acceptance Commands when safe.
- Reviewer: reviews the resulting diff and writes PASS or FAIL.

## Safe Mode

- Never run autonomous work on `main`.
- Use a branch or worktree whose branch begins with `codex/`, `joi-autopilot/`, or `autopilot/`.
- Never push, merge, or create PRs from the autopilot runner.
- Use one small task per loop.
- Stop on missing credentials, network, permissions, or repeated test failures.
- Use `docs/REVIEW_HANDOFF.md` as the shared mailbox and `docs/AUTOPILOT_LOG.md` as the audit trail.

## Setup

```bash
cd "/Users/liujialuo/Documents/New project 2/Joi"
codex login status
```

Expected login state: `Logged in using ChatGPT`.

## Preflight

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight --strict
```

Non-strict preflight is informational. Strict preflight must pass before an autonomous run.

## First Safe Run

Use a disposable branch/worktree first:

```bash
git switch -c codex/nightly-autopilot
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --run-once
```

The initial task in `docs/REVIEW_HANDOFF.md` is intentionally docs-only. Do not move to code tasks until that smoke test proves the handoff loop, acceptance commands, and stop behavior are controllable.

## Review Gates

The reviewer must treat these as blockers:

- P1/P2 behavior or safety regressions.
- Any push, merge, PR creation, or secret/path leak.
- Edits outside the current task scope.
- Failing acceptance commands.
- Generated artifacts or private local screenshots committed by mistake.
