# Joi Review Handoff

Status: Ready to Rerun Docs-Only Autopilot With Scope Guard
Updated: 2026-05-21
Runtime: Codex CLI with ChatGPT subscription login

## Review Status

P0.1 Codex CLI preflight accessibility is implemented. P0.2 was attempted on a safe branch: non-elevated Codex failed on local Codex config access, elevated Codex allowed the Developer role to complete, but the smoke exposed a scope-control failure because Developer produced code/test/tool changes instead of staying docs-only and the run timed out before Tester/Reviewer completed. The runner now includes a docs-only scope guard that checks changes after each role and fails closed on code, test, fixture, tool, or non-autopilot-doc changes.

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

P0.3 Rerun Safe Branch Docs-Only Autopilot With Scope Guard

Rerun one docs-only `tools/joi_autopilot.py --run-once` loop on a dedicated safe branch. The expected outcome is either a completed Developer -> Tester -> Reviewer handoff touching only `docs/REVIEW_HANDOFF.md`, `docs/AUTOPILOT_LOG.md`, and `docs/AUTOPILOT_*.md`, or a fast fail-closed scope-violation result with no code/test/tool changes left unreviewed.

## Acceptance Commands

```bash
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 -m compileall -q tools/joi_autopilot.py
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --preflight --strict
PYTHONPYCACHEPREFIX=/private/tmp/joi-pycache python3 tools/joi_autopilot.py --run-once
git diff -- tools/joi_autopilot.py docs/REVIEW_HANDOFF.md docs/AUTOPILOT_LOG.md
git status --short
```

## Worker Result

Implemented P0.1 Codex CLI preflight accessibility.

The runner now discovers multiple Codex CLI candidates, including the local Codex sandbox bin, probes each candidate with `--version` and `login status`, sets `CODEX_HOME` for the probe and execution path, selects a ChatGPT-authenticated CLI, checks the `codex exec` interface, and redacts local paths from probe detail output. It no longer depends on the inaccessible WindowsApps shim. Older Codex CLI builds that lack `--ask-for-approval` use the config override path for `approval_policy=never`.

P0.2 safe-branch smoke was attempted and failed in a useful way:

- First non-elevated run failed because Codex CLI could not access its local config/skills directory.
- Elevated run got through the Developer role but timed out before the full loop completed.
- Developer exceeded the docs-only scope by producing semantic calibration code/test/tool changes.
- Added docs-only scope enforcement to stop the loop immediately after any role that changes files outside the autopilot docs set.

Validation:

- `python -m compileall -q tools/joi_autopilot.py`: passed.
- `python tools/joi_autopilot.py --preflight`: passed with ChatGPT subscription login via local Codex sandbox bin and exec approval-policy config support.
- `python tools/joi_autopilot.py --preflight --strict`: passed on `autopilot/smoke`.

## Reviewer Result

Pending.
