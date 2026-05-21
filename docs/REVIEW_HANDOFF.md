# Joi Review Handoff

Status: Ready for Safe Branch Autopilot Smoke
Updated: 2026-05-21
Runtime: Codex CLI with ChatGPT subscription login

## Review Status

P0.1 Codex CLI preflight accessibility is implemented. Windows preflight now discovers an accessible Codex CLI from the local Codex sandbox bin, sets the existing `CODEX_HOME`, verifies ChatGPT subscription login without requiring OpenAI API keys, and checks that `codex exec` can run with a non-interactive approval policy. The WindowsApps Codex shim can remain inaccessible; the runner selects the accessible ChatGPT-authenticated CLI and still fails strict preflight on unsafe branches.

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

P0.2 Safe Branch Docs-Only Autopilot Run

Create or switch to a dedicated branch whose name starts with `codex/`, `joi-autopilot/`, or `autopilot/`, then run one docs-only `tools/joi_autopilot.py --run-once` loop. Keep the task small, do not push, do not merge, and stop if credentials, network, permissions, or repeated test failures appear.

## Acceptance Commands

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 -m compileall -q tools/joi_autopilot.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight --strict
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --run-once
git diff -- docs/REVIEW_HANDOFF.md docs/AUTOPILOT_LOG.md
git status --short
```

## Worker Result

Implemented P0.1 Codex CLI preflight accessibility.

The runner now discovers multiple Codex CLI candidates, including the local Codex sandbox bin, probes each candidate with `--version` and `login status`, sets `CODEX_HOME` for the probe and execution path, selects a ChatGPT-authenticated CLI, checks the `codex exec` interface, and redacts local paths from probe detail output. It no longer depends on the inaccessible WindowsApps shim. Older Codex CLI builds that lack `--ask-for-approval` use the config override path for `approval_policy=never`.

Validation:

- `python -m compileall -q tools/joi_autopilot.py`: passed.
- `python tools/joi_autopilot.py --preflight`: passed with ChatGPT subscription login via local Codex sandbox bin and exec approval-policy config support.
- `python tools/joi_autopilot.py --preflight --strict`: failed only on `safe branch` because the current branch is `main`.

## Reviewer Result

Pending.
