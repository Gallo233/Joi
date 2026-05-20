from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_companion.core.computer_use import verify_post_action
from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.tools.targeting import SemanticTargetTool
from agent_companion.core.vision import AccessibilitySnapshot, AccessibleElement, CaptureRect, UnavailableAccessibilityObserver
from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock
from agent_companion.core.vision.visual_detector import HeuristicVisualDetector, VisualDetectionResult
from agent_companion.core.vision.visual_detector import VisualCandidate


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "visual_detector"
CASE_FILE = FIXTURE_DIR / "visual_cases.json"
IMAGE_DIFF_DIR = ROOT / "tests" / "fixtures" / "image_verification"
IMAGE_DIFF_CASE_FILE = IMAGE_DIFF_DIR / "image_diff_cases.json"
SEMANTIC_DIR = ROOT / "tests" / "fixtures" / "semantic_grounding"
SEMANTIC_CASE_FILE = SEMANTIC_DIR / "semantic_cases.json"
LOCAL_FIXTURE_DIR = ROOT / "data" / "local_visual_eval"
LOCAL_CASE_FILE = LOCAL_FIXTURE_DIR / "visual_cases.local.json"
LOCAL_IMAGE_DIFF_CASE_FILE = LOCAL_FIXTURE_DIR / "image_diff_cases.local.json"
LOCAL_SEMANTIC_CASE_FILE = LOCAL_FIXTURE_DIR / "semantic_cases.local.json"


@dataclass
class CaseResult:
    suite: str
    case_id: str
    passed: bool
    failures: list[str]
    top_candidates: list[str]


class _EmptyOcrExtractor:
    def extract(self, image_path: Path) -> OcrResult:
        return OcrResult("success", "没有识别到清晰文字。", [])


class _CaseOcrExtractor:
    def __init__(self, result: OcrResult) -> None:
        self.result = result

    def extract(self, image_path: Path) -> OcrResult:
        return self.result


class _CaseAccessibilityObserver:
    def __init__(self, snapshot: AccessibilitySnapshot) -> None:
        self.snapshot = snapshot

    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        return self.snapshot


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
    synthetic_results = _run_suite(root, "synthetic", CASE_FILE, FIXTURE_DIR)
    local_results: list[CaseResult] = []
    local_skipped = not LOCAL_CASE_FILE.exists()
    if not local_skipped:
        local_results = _run_suite(root, "local_private", LOCAL_CASE_FILE, LOCAL_FIXTURE_DIR)
    synthetic_image_results = _run_image_diff_suite(root, "synthetic_image_diff", IMAGE_DIFF_CASE_FILE, IMAGE_DIFF_DIR)
    local_image_results: list[CaseResult] = []
    local_image_skipped = not LOCAL_IMAGE_DIFF_CASE_FILE.exists()
    if not local_image_skipped:
        local_image_results = _run_image_diff_suite(root, "local_private_image_diff", LOCAL_IMAGE_DIFF_CASE_FILE, LOCAL_FIXTURE_DIR)
    semantic_results = _run_semantic_suite(root, "synthetic_semantic", SEMANTIC_CASE_FILE, SEMANTIC_DIR)
    local_semantic_results: list[CaseResult] = []
    local_semantic_skipped = not LOCAL_SEMANTIC_CASE_FILE.exists()
    if not local_semantic_skipped:
        local_semantic_results = _run_semantic_suite(root, "local_private_semantic", LOCAL_SEMANTIC_CASE_FILE, LOCAL_FIXTURE_DIR)
    if verbose:
        _print_results("committed synthetic visual detector eval", synthetic_results)
        if local_skipped:
            print(f"local private visual detector eval: skipped ({_rel(root, LOCAL_CASE_FILE)} not found)")
        else:
            _print_private_results("local private visual detector eval", local_results)
        _print_results("committed synthetic image verification eval", synthetic_image_results)
        if local_image_skipped:
            print(f"local private image verification eval: skipped ({_rel(root, LOCAL_IMAGE_DIFF_CASE_FILE)} not found)")
        else:
            _print_private_results("local private image verification eval", local_image_results)
        _print_results("committed synthetic semantic grounding eval", semantic_results)
        if local_semantic_skipped:
            print(f"local private semantic grounding eval: skipped ({_rel(root, LOCAL_SEMANTIC_CASE_FILE)} not found)")
        else:
            _print_private_results("local private semantic grounding eval", local_semantic_results)
    all_results = [*synthetic_results, *local_results, *synthetic_image_results, *local_image_results, *semantic_results, *local_semantic_results]
    return 0 if all(result.passed for result in all_results) else 1


def _run_suite(root: Path, suite: str, case_file: Path, base_dir: Path) -> list[CaseResult]:
    cases = json.loads(case_file.read_text(encoding="utf-8"))
    return [_run_case(root, suite, base_dir, case) for case in cases]


def _run_image_diff_suite(root: Path, suite: str, case_file: Path, base_dir: Path) -> list[CaseResult]:
    cases = json.loads(case_file.read_text(encoding="utf-8"))
    return [_run_image_diff_case(root, suite, base_dir, case) for case in cases]


def _run_semantic_suite(root: Path, suite: str, case_file: Path, base_dir: Path) -> list[CaseResult]:
    cases = json.loads(case_file.read_text(encoding="utf-8"))
    return [_run_semantic_case(root, suite, base_dir, case) for case in cases]


def _print_results(label: str, results: list[CaseResult]) -> None:
    passed = sum(1 for result in results if result.passed)
    print(f"{label}: {passed}/{len(results)} passed")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.case_id}")
        for candidate in result.top_candidates:
            print(f"  candidate: {candidate}")
        for failure in result.failures:
            print(f"  failure: {failure}")


def _print_private_results(label: str, results: list[CaseResult]) -> None:
    passed = sum(1 for result in results if result.passed)
    print(f"{label}: {passed}/{len(results)} passed")
    for index, result in enumerate(results, start=1):
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] local_private_case_{index}")
        categories = sorted({_failure_category(failure) for failure in result.failures})
        for category in categories:
            print(f"  failure_category: {category}")


def _run_case(root: Path, suite: str, base_dir: Path, case: dict[str, Any]) -> CaseResult:
    case_id = str(case.get("id") or "unknown")
    failures: list[str] = []
    image_path = _resolve_image_path(base_dir, str(case["image"]))
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

    return CaseResult(suite, case_id, not failures, failures, [_candidate_summary(candidate) for candidate in candidates[:3]])


def _run_image_diff_case(root: Path, suite: str, base_dir: Path, case: dict[str, Any]) -> CaseResult:
    case_id = str(case.get("id") or "unknown")
    failures: list[str] = []
    before_path = _resolve_image_path(base_dir, str(case["before_image"]))
    after_path = _resolve_image_path(base_dir, str(case["after_image"]))
    width, height = _image_size(case)
    before = ComputerObservation(
        target="active_window",
        screenshot_path=before_path,
        screenshot_rel=_rel(root, before_path),
        width=width,
        height=height,
        title="Image verification fixture",
        window_handle=0,
        capture_rect=CaptureRect(0, 0, width, height),
        query=str(case.get("query") or "image diff"),
        ocr={"status": "success", "text_blocks": [{"text": "stable", "bbox": [0, 0, 10, 10], "confidence": 1.0}]},
    )
    after = ComputerObservation(
        target="active_window",
        screenshot_path=after_path,
        screenshot_rel=_rel(root, after_path),
        width=width,
        height=height,
        title="Image verification fixture",
        window_handle=0,
        capture_rect=CaptureRect(0, 0, width, height),
        query=str(case.get("query") or "image diff"),
        ocr={"status": "success", "text_blocks": [{"text": "stable", "bbox": [0, 0, 10, 10], "confidence": 1.0}]},
    )
    verification = verify_post_action(before, after)
    expected_changed = case.get("expected_image_changed")
    expected_status = str(case.get("expected_verification_status") or "")
    if expected_changed is True and verification.signals.image_changed is not True:
        failures.append(f"expected image_changed=True, got {verification.signals.image_changed}")
    if expected_changed is False and verification.signals.image_changed is not False:
        failures.append(f"expected image_changed=False, got {verification.signals.image_changed}")
    if expected_changed is None and verification.signals.image_changed is not None:
        failures.append(f"expected image_changed=None, got {verification.signals.image_changed}")
    if expected_status and verification.status != expected_status:
        failures.append(f"expected verification status {expected_status}, got {verification.status}")
    forbidden = ["{", "}", str(case.get("before_image") or ""), str(case.get("after_image") or ""), _rel(root, before_path), _rel(root, after_path)]
    voice_line = "操作后画面有变化。" if verification.status == "changed" else "操作执行了，但画面变化不明显。"
    if any(fragment and fragment in voice_line for fragment in forbidden):
        failures.append("verification voice leaked image details")
    summary = [
        f"status={verification.status}",
        f"image_changed={verification.signals.image_changed}",
        f"artifact_changed={verification.signals.artifact_changed}",
    ]
    return CaseResult(suite, case_id, not failures, failures, summary)


def _run_semantic_case(root: Path, suite: str, base_dir: Path, case: dict[str, Any]) -> CaseResult:
    case_id = str(case.get("id") or "unknown")
    failures: list[str] = []
    image_path = _resolve_image_path(base_dir, str(case["image"]))
    width, height = _image_size(case)
    observation = ComputerObservation(
        target="active_window",
        screenshot_path=image_path,
        screenshot_rel=_rel(root, image_path),
        width=width,
        height=height,
        title="Synthetic semantic grounding fixture",
        window_handle=0,
        capture_rect=CaptureRect(0, 0, width, height),
        query=str(case.get("query") or ""),
    )
    tool_result = SemanticTargetTool(
        root,
        computer_backend=_StaticComputerBackend(observation),
        ocr=_CaseOcrExtractor(_ocr_from_case(case)),
        accessibility=_CaseAccessibilityObserver(_accessibility_from_case(case)),
        visual_detector=_StaticVisualDetector(_visual_from_case(case)),
    ).run(ToolRequest("vision.resolve_target", {"query": str(case.get("query") or "")}))
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    if "requires_approval" in expected and bool(tool_result.requires_approval) != bool(expected.get("requires_approval")):
        failures.append(f"expected requires_approval={bool(expected.get('requires_approval'))}, got {bool(tool_result.requires_approval)}")
    if "candidate_selection_required" in expected and bool(tool_result.agent_state.get("candidate_selection_required")) != bool(expected.get("candidate_selection_required")):
        failures.append(f"expected candidate_selection_required={bool(expected.get('candidate_selection_required'))}, got {bool(tool_result.agent_state.get('candidate_selection_required'))}")
    if "needs_clarification" in expected and bool(tool_result.agent_state.get("needs_clarification")) != bool(expected.get("needs_clarification")):
        failures.append(f"expected needs_clarification={bool(expected.get('needs_clarification'))}, got {bool(tool_result.agent_state.get('needs_clarification'))}")
    candidates = tool_result.agent_state.get("target_candidates")
    candidate_rows = candidates if isinstance(candidates, list) else []
    min_candidates = expected.get("min_candidates")
    if min_candidates is not None and len(candidate_rows) < int(min_candidates):
        failures.append(f"expected at least {int(min_candidates)} semantic candidates, got {len(candidate_rows)}")
    max_candidates = expected.get("max_candidates")
    if max_candidates is not None and len(candidate_rows) > int(max_candidates):
        failures.append(f"expected at most {int(max_candidates)} semantic candidates, got {len(candidate_rows)}")
    top = tool_result.agent_state.get("target_candidate")
    if isinstance(top, dict):
        for key, expected_key in (
            ("source", "top_source"),
            ("ambiguity", "top_ambiguity"),
            ("role", "top_role"),
            ("enabled", "top_enabled"),
        ):
            if expected_key in expected and top.get(key) != expected[expected_key]:
                failures.append(f"expected {key}={expected[expected_key]}, got {top.get(key)}")
        max_confidence = expected.get("max_top_confidence")
        if max_confidence is not None and float(top.get("confidence") or 0) > float(max_confidence):
            failures.append(f"expected top confidence <= {float(max_confidence)}, got {top.get('confidence')}")
        preview = top.get("preview")
        if candidate_rows and (not isinstance(preview, dict) or not preview.get("bbox") or not preview.get("artifact")):
            failures.append("semantic target preview is not renderable")
    elif candidate_rows:
        failures.append("semantic candidates exist but top candidate state is missing")
    forbidden = ["{", "}", "bbox", "source", str(case.get("image") or ""), ".ppm", "data/"]
    forbidden.extend(_case_visible_terms(case))
    if any(fragment and fragment in tool_result.voice_line.text for fragment in forbidden):
        failures.append("voice_line leaked semantic grounding details")
    summary = [
        f"approval={bool(tool_result.requires_approval)}",
        f"selection={bool(tool_result.agent_state.get('candidate_selection_required'))}",
        f"candidates={len(candidate_rows)}",
    ]
    if isinstance(top, dict):
        summary.append(f"top={top.get('source', 'none')}:{top.get('ambiguity', 'none')}")
    return CaseResult(suite, case_id, not failures, failures, summary)


def _ocr_from_case(case: dict[str, Any]) -> OcrResult:
    blocks = []
    for row in case.get("ocr_blocks") or []:
        if not isinstance(row, dict):
            continue
        bbox = _bbox(row.get("bbox"))
        if bbox is None:
            continue
        blocks.append(OcrTextBlock(str(row.get("text") or ""), bbox, float(row.get("confidence", 0.9))))
    return OcrResult("success", "synthetic OCR", blocks)


def _accessibility_from_case(case: dict[str, Any]) -> AccessibilitySnapshot:
    elements = []
    for row in case.get("accessibility_elements") or []:
        if not isinstance(row, dict):
            continue
        bounds = _bbox(row.get("bounds"))
        if bounds is None:
            continue
        elements.append(
            AccessibleElement(
                str(row.get("name") or ""),
                str(row.get("role") or ""),
                bounds,
                enabled=bool(row.get("enabled", True)),
                clickable=bool(row.get("clickable", False)),
                confidence=float(row.get("confidence", 0.78)),
            )
        )
    return AccessibilitySnapshot("success", title="Synthetic semantic grounding fixture", window_handle=0, elements=elements)


def _visual_from_case(case: dict[str, Any]) -> VisualDetectionResult:
    candidates = []
    for row in case.get("visual_candidates") or []:
        if not isinstance(row, dict):
            continue
        bbox = _bbox(row.get("bbox"))
        if bbox is None:
            continue
        candidates.append(
            VisualCandidate(
                str(row.get("label") or "目标"),
                bbox,
                float(row.get("confidence", 0.6)),
                str(row.get("reason") or "视觉候选"),
                region=str(row.get("region") or "main_content"),
            )
        )
    return VisualDetectionResult("success", "synthetic visual candidates", candidates)


def _case_visible_terms(case: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for row in case.get("ocr_blocks") or []:
        if isinstance(row, dict):
            terms.append(str(row.get("text") or ""))
    for row in case.get("accessibility_elements") or []:
        if isinstance(row, dict):
            terms.append(str(row.get("name") or ""))
            terms.append(str(row.get("role") or ""))
    for row in case.get("visual_candidates") or []:
        if isinstance(row, dict):
            terms.append(str(row.get("label") or ""))
    return [term for term in terms if term]


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


def _failure_category(failure: str) -> str:
    text = failure.casefold()
    if "at least" in text:
        return "candidate_count_low"
    if "at most" in text:
        return "candidate_count_high"
    if "outside allowed area" in text or "overlap approximate" in text:
        return "localization_miss"
    if "preview" in text:
        return "preview_unrenderable"
    if "voice_line" in text or "voice leaked" in text:
        return "voice_leak"
    if "direct approval" in text:
        return "approval_gate_regression"
    if "request selection" in text:
        return "selection_gate_regression"
    if "status" in text:
        return "status_mismatch"
    if "image_changed=true" in text:
        return "image_change_false_negative"
    if "image_changed=false" in text:
        return "image_change_false_positive"
    return "calibration_failure"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _resolve_image_path(base_dir: Path, image: str) -> Path:
    path = Path(image)
    if path.is_absolute():
        return path
    return base_dir / path


if __name__ == "__main__":
    raise SystemExit(run_eval())
