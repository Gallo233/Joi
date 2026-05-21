# Semantic Grounding Private Calibration

Joi supports a local-only calibration loop for real Windows layouts. This is for collecting lessons from your own browser, game, or app screens without committing private screenshots or text.

## Local Files

Private semantic calibration inputs live under:

```text
data/local_visual_eval/
```

The default manifest is:

```text
data/local_visual_eval/semantic_cases.local.json
```

That directory is ignored by Git. Do not commit real screenshots, OCR text, page titles, URLs, account data, local paths, window titles, or app-specific private labels.

## Run Calibration

Evaluate the local manifest with sanitized output:

```powershell
.\.venv\Scripts\python.exe tools\calibrate_semantic_grounding.py
```

The runner prints only pass/fail counts and abstract failure categories, for example:

```text
local semantic calibration: 3/5 passed
failure_categories:
  ambiguous_repeated_label: 1
  stale_accessibility_geometry: 1
```

It does not print screenshot filenames, paths, OCR text, window titles, URLs, private labels, or account data.

## Capture A Private Case

On Windows, you can capture the current active window into the ignored local manifest:

```powershell
.\.venv\Scripts\python.exe tools\calibrate_semantic_grounding.py --capture-active-window --query "点目标按钮"
```

The generated local case may contain private OCR/UI Automation text in the ignored manifest. Review it locally, add expected behavior if needed, and rerun the calibration command.

## Failure Categories

Use these categories when summarizing local findings:

- `stale_accessibility_geometry`
- `ambiguous_repeated_label`
- `visual_only_low_confidence`
- `capture_rect_untrusted`
- `screen_center_outside_capture`
- `modal_background_conflict`
- `sparse_canvas_no_uia`
- `unexpected_direct_approval`

## Promoting Findings

Only promote abstract lessons into committed synthetic fixtures. Translate the private finding into generated geometry and generic labels in `tools/generate_visual_fixtures.py`, then regenerate fixtures and run:

```powershell
.\.venv\Scripts\python.exe tools\generate_visual_fixtures.py
.\.venv\Scripts\python.exe tools\eval_visual_detector.py
```

Never copy real screenshots or private text into committed fixtures.
