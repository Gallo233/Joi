# Joi Studio Repository Instructions

## Product direction

Joi is a **macOS-first**, local-first, character-fronted multimodal agent companion. Build the primary product experience for macOS native observation and Computer Use, Accessibility and Screen Recording permissions, the Tauri app bundle, and an authenticated Core sidecar. Treat `cua-driver` as an optional enhancement with a truthful native fallback. Preserve Windows through adapter contracts and compatibility checks where applicable.

Some older docs still describe Joi as Windows-first. That wording is historical and does not override this instruction.

Use `$joi-studio` for architecture, implementation, review, release, or any task crossing product areas. The skill defines the full role catalog, routing, handoff, safety gates, and project map.

## Start here

1. Inspect `git status --short --branch`; preserve all unrelated user changes.
2. Read the current user brief and the smallest relevant sources.
3. For cross-layer work, read:
   - `docs/COMMON_PRESENCE_ARCHITECTURE.md`
   - `docs/BACKEND_ARCHITECTURE.md`
   - `agent_companion/docs/architecture.md`
   - `docs/CHANGELOG.md`
   - `docs/KNOWN_ISSUES.md`
4. Reconcile docs with code and tests. Prefer the current user instruction, this file, code/tests, dated current architecture, then older handoffs.
5. State a compact Studio Brief: outcome, intelligent domains, execution lead, accountable directors, independent reviewers, edit surface, non-goals, risk, and checks.

## Studio routing

| Intelligent domain | Accountable director | Execution roles |
|---|---|---|
| Product design | `product-design-director` | `product-experience-designer` |
| Technical engineering | `technical-director` | Core Runtime, Presence & Context, Perception & Action, Skill & Integration |
| AI & companion | `ai-companion-director` | Intelligence & Media; AI behavior parts of Perception/Character |
| Art & character | `art-director` | Character & Shell visual/motion/presentation |
| Trust & safety | `trust-safety-director` | `trust-safety-reviewer` |
| Quality & delivery | `quality-release-director` | `quality-release-integrator` |

`studio-director` classifies the work and arbitrates cross-domain decisions. Assign exactly one execution lead and one accountable director for every affected domain.

Every domain director must issue two decisions:

1. **Scheme gate:** approve the problem framing, alternatives, boundaries, risks, and acceptance plan before implementation.
2. **Closeout gate:** review the integrated result and evidence before the work leaves the domain.

Use only `approved`, `approved-with-conditions`, `rework`, or `blocked`. A director cannot be the main implementer and final approver for the same material change. Trust & Safety and Quality & Release vetoes cannot be waived by other directors.

Keep one writer per file. Freeze shared RPC/event/schema contracts before parallel work. Use real subagents only when the user or active runtime instructions authorize delegation; otherwise perform role passes sequentially and do not claim separate agents ran.

## Non-negotiable contracts

- The shell consumes safe RPC/event fields, never raw tool output.
- Keep planner, display, voice, memory, and audit channels separate.
- Keep `voice_line` free of coordinates, typed text, JSON, commands, paths, model/provider names, tokens, logs, filenames, approval IDs, and task IDs.
- External-state actions require appropriate confirmation, scope, audit, and post-action verification.
- Sensitive payment, authentication, external messaging, deletion, installation, or permission expansion never becomes silently automatic.
- Platform code stays behind adapters/factories. Missing macOS permissions fail clearly; optional backends never fake success.
- Do not commit or package secrets, config, runtime data, logs, screenshots, private calibration data, local paths, model assets, `.venv`, `node_modules`, `dist`, or `target`.
- Do not reintroduce removed prototypes or unlicensed third-party character assets.

## Verification

Run the narrowest relevant checks first, then expand by risk:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python run_agent_companion_tests.py
cd agent_companion/shell && npm run build
cd agent_companion/shell/src-tauri && cargo check
```

For macOS release paths, also validate `npm run build:release` and `npm run tauri -- build` when the environment supports them. For Computer Use, permission, memory, voice, Skill execution, migration, or release changes, require an independent Trust & Safety pass plus Quality & Release integration.

Never claim a platform, signing, notarization, permission, or real-device path passed unless it actually ran.

## Handoff

Finish with:

- intelligent domains, execution lead, directors, and independent reviewers
- scheme-gate and closeout-gate decisions
- outcome and files/contracts changed
- exact checks and results
- checks not run and why
- remaining risk and next owner
