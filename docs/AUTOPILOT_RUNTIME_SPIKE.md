# Joi Autopilot Runtime Spike

## Snapshot

- Candidate evaluated: `axeldelafosse/loop`
- Local clone: `<spike-checkout>/loop`
- Upstream commit checked: `ecbab1f 1.0.32`
- Date: 2026-05-20

## Fit

`loop` is the closest current open-source match for Joi's desired autonomous worker/reviewer loop. It runs Codex and Claude as a persistent pair, lets them exchange messages directly through bridge tools, supports run resume, tmux sessions, git worktrees, proof criteria, repeated review loops, and draft PR creation.

This maps well to the desired Joi pattern:

- worker agent implements one scoped task
- reviewer agent verifies the diff
- feedback loops until review passes
- run state can be resumed
- work can happen in an isolated branch/worktree

## Current Local Readiness

Available on this machine:

- `codex` installed: `codex-cli 0.131.0-alpha.9`
- `claude` installed: `2.1.133`
- `gh` installed: `2.92.0`

Missing for direct `loop` source run:

- `bun`
- `tmux`

## Safety Notes

Do not run upstream `loop` directly against Joi main on the host machine.

Reasons:

- Codex exec path uses `--yolo`.
- Claude path uses `--dangerously-skip-permissions`.
- Codex app-server thread starts with `approvalPolicy: "never"`.
- After review pass, upstream `loop` asks the agent to create or update a draft PR.
- The upstream README explicitly recommends running inside a VM because the agents run in YOLO mode.

## Recommendation

Use `loop` as the first implementation reference, not as an unmodified production runtime for Joi.

Decision update: Joi will pursue a Codex-only autopilot path first, using the local Codex CLI signed in with ChatGPT subscription access. The OpenAI Agents SDK + Codex MCP route remains the richer native multi-agent architecture, but it requires API-key-backed OpenAI API usage, which is separate from ChatGPT/Codex subscription access. `loop` remains a useful reference for paired-agent ergonomics, resume behavior, and worktree discipline.

Preferred path:

1. Create a sandbox-only spike with `loop --worktree --max-iterations 1` on a docs-only task.
2. Run it in a VM or disposable macOS user/worktree environment, not on main.
3. If stable, fork or wrap it into `joi-autopilot` with stricter defaults:
   - no `--yolo` for Codex unless inside an approved sandbox
   - no auto push or auto PR by default
   - one small task per loop
   - mandatory proof commands
   - stop on P1/P2 review findings
   - write all state to `docs/REVIEW_HANDOFF.md` and `docs/AUTOPILOT_LOG.md`
4. If `loop` cannot be safely constrained, build a small Joi-specific runtime using ChatGPT-authenticated Codex CLI roles first. Revisit OpenAI Agents SDK + Codex MCP only if API billing is explicitly acceptable.

## Proposed Joi Safe Mode

```text
Branch:
codex/nightly-autopilot

Rules:
- Never push main.
- Never merge.
- Never delete files unless the task explicitly requires it.
- One loop = one small implementation or one review.
- Always run acceptance commands before commit.
- If tests fail twice, stop and write a blocker.
- If review finds P1/P2, worker fixes that before new feature work.
- If network or permission is required, stop and write a blocker.
- All coordination goes through docs/REVIEW_HANDOFF.md.
```

## Next Spike

Install missing local prerequisites in a disposable environment, then run a docs-only `loop` task:

```text
Task:
Create or update docs/AUTOPILOT_SANDBOX_TEST.md with a one-paragraph smoke-test note. Do not edit code.

Proof:
git diff -- docs/AUTOPILOT_SANDBOX_TEST.md
git status --short
```

Only proceed to code-level autopilot after this docs-only run proves that resume, worktree isolation, review feedback, and stop conditions are controllable.
