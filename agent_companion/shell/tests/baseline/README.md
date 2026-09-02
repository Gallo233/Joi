# Refactor baselines

Evidence that the shell refactor changed what it meant to change and nothing else.

## `style-audit.baseline.json`

Structural health of the stylesheet at the start of the refactor. Regenerate and
compare with:

```bash
node scripts/audit-styles.mjs --baseline tests/baseline/style-audit.baseline.json
```

Every stage should move these numbers in one direction only. If dead classes or
hardcoded colours go *up*, something was copied rather than moved.

## `*.digest.txt`

A per-element fingerprint of a rendered view: geometry, font size, colour,
background, stacking level. This is what makes "S3 changes nothing on screen" a
checkable claim rather than an assurance — pixel baselines would mean adding a
headless browser to a project that has never needed one, and geometry plus
typography plus colour catches what actually regresses.

To capture or re-capture:

1. Start Core: `.venv/bin/python -m agent_companion.core.main --serve --workspace .`
2. Start the shell dev server and size the window to the width in the filename.
3. Navigate to the view named in the filename.
4. Evaluate `scripts/layout-digest.js` in the page and save the returned string.

Then `diff` the old and new files. Reordered lines mean the DOM order moved;
changed coordinates mean the layout moved.

**Capture before and after against the same Core state.** The conversation view
renders whatever turns are in the local database, so a digest taken after
sending new messages will differ for reasons that have nothing to do with the
refactor.

### A tab that is not painting will lie to you

A backgrounded or headless tab does not paint, and a CSS animation in a tab
that does not paint sits at 0% forever. `getAnimations()` still reports it as
`running`, with `currentTime: 0` that never advances, so every animated element
measures at its `from` keyframe.

This produced a convincing 38-line "regression" during the S3 split: the
project sheet measured at `x: -332`, its slide-in start, while the app on
screen was perfectly correct. It also caused a real change to be made for a
wrong reason — the sheet's exit animation was removed on the theory that it
never ran, when it runs fine anywhere that paints.

`scripts/layout-digest.js` now calls `.finish()` on every running animation
before measuring, so a capture records where things come to rest and is
identical whether or not the tab happens to be painting. If you measure the DOM
by hand instead, do the same, or take a screenshot first to force a frame —
and treat `document.timeline.currentTime` not advancing as "these numbers are
not real".

Baselines are captured per stage, immediately before the stage that risks them,
rather than all at once up front — a digest taken days earlier against different
Core data compares badly and gets ignored, which is worse than no baseline.
