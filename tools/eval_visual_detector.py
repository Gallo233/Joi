from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.tools.targeting import SemanticTargetTool
from agent_companion.core.vision import CaptureRect, UnavailableAccessibilityObserver
from agent_companion.core.vision.ocr import OcrResult
from agent_companion.core.vision.visual_detector import HeuristicVisualDetector, VisualDetectionResult


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "visual_detector"
CASE_FILE = FIXTURE_DIR / "visual_cases.json"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str]
    top_candidates: list[str]


class _EmptyOcrExtractor:
    def extract(self, image_path: Path) -> OcrResult:
        return OcrResult("success", "没有识别到清晰文字。", [])


class _StaticComputerBackend:
    def __init__(self, observation: ComputerObservation) -> None:
        self.observation = observation
        self.actions: list[ComputerAction] = []

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        return self.observation

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        self.actions.append(action)
        return ComputerUseResult(ok=True, action=action, summary="完成了电脑操作。")


class _StaticVisualDetector:
    def __init__(self, result: VisualDetectionResult) -> None:
        self.result = result

    def detect(self, observation: ComputerObservation, query: str = "") -> VisualDetectionResult:
        return self.result


def run_eval(root: Path = ROOT, verbose: bool = True) -> int:
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    results = [_run_case(root, case) for case in cases]
    passed = sum(1 for result in results if result.passed)
    if verbose:
        print(f"visual detector eval: {passed}/{len(results)} passed")
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.case_id}")
            for candidate in result.top_candidates:
                print(f"  candidate: {candidate}")
            for failure in result.failures:
                print(f"  failure: {failure}")
    return 0 if passed == len(results) else 1


def _run_case(root: Path, case: dict[str, Any]) -> CaseResult:
    case_id = str(case.get("id") or "unknown")
    failures: list[str] = []
    image_path = FIXTURE_DIR / str(case["image"])
    width, height = _image_size(case)
    observation = ComputerObservation(
        target="active_window",
        screenshot_path=image_path,
        screenshot_rel=_rel(root, image_path),
        width=width,
        height=height,
        title="Synthetic visual detector fixture",
        window_handle=0,
        capture_rect=CaptureRect(0, 0, width, height),
        query=str(case.get("query") or ""),
    )
    detector = HeuristicVisualDetector(max_candidates=5)
    detection = detector.detect(observation, str(case.get("query") or ""))
    candidates = detection.candidates
    expected_count = case.get("expected_candidate_count") or {}
    min_count = int(expected_count.get("min", 1))
    max_count = int(expected_count.get("max", 5))
    if detection.status != "success":
        failures.append(f"detector status was {detection.status}")
    if len(candidates) < min_count:
        failures.append(f"expected at least {min_count} candidates, got {len(candidates)}")
    if len(candidates) > max_count:
        failures.append(f"expected at most {max_count} candidates, got {len(candidates)}")
    top = candidates[0] if candidates else None
    if top is not None:
        expected_regions = set(str(region) for region in case.get("expected_regions") or [])
        if expected_regions and top.region not in expected_regions:
            failures.append(f"top region {top.region} not in {sorted(expected_regions)}")
        allowed_area = _bbox(case.get("allowed_bbox_area"))
        if allowed_area is not None and not _center_inside(top.bbox, allowed_area):
            failures.append(f"top bbox {top.bbox} center outside allowed area {allowed_area}")
        approximate = _bbox(case.get("approximate_bbox"))
        if approximate is not None and _iou(top.bbox, approximate) < 0.2:
            failures.append(f"top bbox {top.bbox} did not overlap approximate bbox {approximate}")
        preview = top.preview
        if not isinstance(preview, dict) or not preview.get("bbox") or not preview.get("artifact"):
            failures.append("top candidate preview is not renderable")

    tool_result = SemanticTargetTool(
        root,
        computer_backend=_StaticComputerBackend(observation),
        ocr=_EmptyOcrExtractor(),
        accessibility=UnavailableAccessibilityObserver("fixture_no_uia"),
        visual_detector=_StaticVisualDetector(detection),
    ).run(ToolRequest("vision.resolve_target", {"query": str(case.get("query") or "")}))
    if case.get("should_require_selection", True):
        if tool_result.requires_approval:
            failures.append("visual-only candidate created direct approval")
        if not tool_result.agent_state.get("candidate_selection_required"):
            failures.append("visual-only candidate did not request selection")
    candidate_state = tool_result.agent_state.get("target_candidate")
    if isinstance(candidate_state, dict):
        preview = candidate_state.get("preview")
        if not isinstance(preview, dict) or not preview.get("bbox") or not preview.get("artifact"):
            failures.append("semantic target candidate preview is not renderable")
    forbidden = ["{", "}", "bbox", "source", "visual", str(case.get("image") or "")]
    if any(fragment and fragment in tool_result.voice_line.text for fragment in forbidden):
        failures.append("voice_line leaked technical visual detector details")

    return CaseResult(case_id, not failures, failures, [_candidate_summary(candidate) for candidate in candidates[:3]])


def _image_size(case: dict[str, Any]) -> tuple[int, int]:
    value = case.get("image_size")
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"case {case.get('id')} missing image_size")
    return int(value[0]), int(value[1])


def _bbox(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))


def _center_inside(bbox: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> bool:
    center_x = bbox[0] + bbox[2] / 2
    center_y = bbox[1] + bbox[3] / 2
    return area[0] <= center_x <= area[0] + area[2] and area[1] <= center_y <= area[1] + area[3]


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx1, ly1, lw, lh = left
    rx1, ry1, rw, rh = right
    lx2, ly2 = lx1 + lw, ly1 + lh
    rx2, ry2 = rx1 + rw, ry1 + rh
    inter_w = max(0, min(lx2, rx2) - max(lx1, rx1))
    inter_h = max(0, min(ly2, ry2) - max(ly1, ry1))
    inter = inter_w * inter_h
    union = lw * lh + rw * rh - inter
    return inter / union if union else 0.0


def _candidate_summary(candidate: Any) -> str:
    confidence = round(float(candidate.confidence) * 100)
    return f"{candidate.region} {candidate.bbox} {confidence}% {candidate.reason}"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(run_eval())
