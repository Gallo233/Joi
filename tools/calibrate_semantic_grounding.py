from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_companion.core.computer_use import WindowsComputerUseBackend
from agent_companion.core.computer_use.schemas import ComputerObservation
from agent_companion.core.vision import HeuristicVisualDetector, WindowsAccessibilityObserver, WindowsScreenObserver
from agent_companion.core.vision.ocr import PytesseractOcrExtractor, run_ocr_safely
from tools.eval_visual_detector import (
    LOCAL_FIXTURE_DIR,
    LOCAL_SEMANTIC_CASE_FILE,
    SEMANTIC_CALIBRATION_FAILURE_CATEGORIES,
    run_local_semantic_calibration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sanitized local semantic grounding calibration.")
    parser.add_argument("--manifest", type=Path, default=LOCAL_SEMANTIC_CASE_FILE)
    parser.add_argument("--list-categories", action="store_true", help="Print stable abstract calibration categories and exit.")
    parser.add_argument("--capture-active-window", action="store_true", help="Capture the current active window into the ignored local manifest.")
    parser.add_argument("--query", default="", help="Private local target query to save with a captured case.")
    parser.add_argument("--target", default="active_window", choices=("active_window", "fullscreen"))
    args = parser.parse_args()

    if args.list_categories:
        for category in SEMANTIC_CALIBRATION_FAILURE_CATEGORIES:
            print(category)
        return 0

    manifest = args.manifest if args.manifest.is_absolute() else (ROOT / args.manifest)
    base_dir = manifest.parent
    if args.capture_active_window:
        captured = _capture_private_case(base_dir, manifest, args.query, args.target)
        if not captured:
            return _print_invalid_manifest_report()
        print("local semantic calibration capture: saved 1 private case")

    try:
        ok, results, categories, skipped = run_local_semantic_calibration(ROOT, manifest, base_dir)
    except Exception:
        return _print_invalid_manifest_report()
    return _print_sanitized_report(ok, results, categories, skipped)


def _print_sanitized_report(ok: bool, results: list[Any], categories: dict[str, int], skipped: bool) -> int:
    if skipped:
        print("local semantic calibration: skipped")
        print("private manifest: missing")
        return 0
    passed = sum(1 for result in results if result.passed)
    print(f"local semantic calibration: {passed}/{len(results)} passed")
    print("failure_categories:")
    if categories:
        for category in SEMANTIC_CALIBRATION_FAILURE_CATEGORIES:
            count = int(categories.get(category, 0))
            if count:
                print(f"  {category}: {count}")
    else:
        print("  none: 0")
    return 0 if ok else 1


def _print_invalid_manifest_report() -> int:
    print("local semantic calibration: failed")
    print("private manifest: invalid")
    print("failure_categories:")
    print("  capture_rect_untrusted: 1")
    return 1


def _capture_private_case(base_dir: Path, manifest: Path, query: str, target: str) -> bool:
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        observer = WindowsScreenObserver(ROOT)
        backend = WindowsComputerUseBackend(ROOT, observer)
        observation = backend.observe(target=target, query=query)
        local_image = base_dir / f"semantic_capture_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        shutil.copyfile(observation.screenshot_path, local_image)
        local_observation = ComputerObservation(
            target=observation.target,
            screenshot_path=local_image,
            screenshot_rel="local_private_capture",
            width=observation.width,
            height=observation.height,
            window_handle=observation.window_handle,
            capture_rect=observation.capture_rect,
            source=observation.source,
            query=query,
        )
        case = _case_from_observation(local_observation, local_image.name, query)
        cases = _read_manifest(manifest)
        cases.append(case)
        manifest.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _case_from_observation(observation: ComputerObservation, image_name: str, query: str) -> dict[str, Any]:
    ocr = run_ocr_safely(PytesseractOcrExtractor(), observation.screenshot_path)
    accessibility = WindowsAccessibilityObserver().observe(observation.window_handle, "")
    visual = HeuristicVisualDetector(max_candidates=5).detect(observation, query)
    row: dict[str, Any] = {
        "id": f"local_private_semantic_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "image": image_name,
        "query": query,
        "image_size": [observation.width, observation.height],
        "capture_rect": observation.capture_rect.to_agent_state() if observation.capture_rect else None,
        "ocr_blocks": [block.to_agent_state() for block in ocr.text_blocks[:24]],
        "accessibility_elements": [element.to_agent_state() for element in accessibility.elements[:120]],
        "visual_candidates": [candidate.to_agent_state() for candidate in visual.candidates[:5]],
        "expected": {},
        "calibration_categories": [],
    }
    return row


def _read_manifest(manifest: Path) -> list[dict[str, Any]]:
    if not manifest.exists():
        return []
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


if __name__ == "__main__":
    raise SystemExit(main())
