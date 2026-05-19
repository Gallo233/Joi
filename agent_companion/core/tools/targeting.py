from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any
import uuid

from agent_companion.core.computer_use import ComputerUseBackend, WindowsComputerUseBackend
from agent_companion.core.computer_use.schemas import ComputerObservation
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.vision import OcrExtractor, PytesseractOcrExtractor, VisionObserver, WindowsScreenObserver
from agent_companion.core.vision.ocr import run_ocr_safely
from agent_companion.core.vision.regions import group_ocr_regions, regions_to_agent_state, summarize_ocr_regions
from agent_companion.core.vision.targeting import TargetCandidate, resolve_target_candidates
from agent_companion.core.voice import safe_voice_line


@dataclass
class PendingSemanticTargetSelection:
    task_id: str
    query: str
    target_candidates: list[dict[str, Any]]
    observation: dict[str, Any]
    artifacts: list[str]
    created_at: float = field(default_factory=time.time)
    selection_id: str = field(default_factory=lambda: f"selection-{uuid.uuid4().hex[:12]}")


class SemanticTargetSelectionStore:
    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._selections: dict[str, PendingSemanticTargetSelection] = {}
        self._latest_selection_id: str | None = None

    def save(self, selection: PendingSemanticTargetSelection) -> None:
        self._selections[selection.selection_id] = selection
        self._latest_selection_id = selection.selection_id

    def current(self) -> PendingSemanticTargetSelection | None:
        if self._latest_selection_id is None:
            return None
        return self.get(self._latest_selection_id, require_current=False)

    def get(self, selection_id: str, require_current: bool = True) -> PendingSemanticTargetSelection | None:
        status = self.status(selection_id)
        if status != "ready":
            return None
        if require_current and selection_id != self._latest_selection_id:
            return None
        return self._selections.get(selection_id)

    def clear(self, selection_id: str | None = None) -> None:
        if selection_id is None:
            self._selections.clear()
            self._latest_selection_id = None
            return
        self._selections.pop(selection_id, None)
        if self._latest_selection_id == selection_id:
            self._latest_selection_id = None

    def is_expired(self, selection_id: str | None = None) -> bool:
        selection = self._selection_for_status(selection_id)
        if selection is None:
            return False
        return time.time() - selection.created_at > self.ttl_seconds

    def status(self, selection_id: str | None = None) -> str:
        if selection_id is None:
            selection_id = self._latest_selection_id
        if not selection_id:
            return "missing"
        selection = self._selections.get(selection_id)
        if selection is None:
            return "missing"
        if time.time() - selection.created_at > self.ttl_seconds:
            self.clear(selection_id)
            return "expired"
        if selection_id != self._latest_selection_id:
            return "not_current"
        return "ready"

    def has_pending(self) -> bool:
        return self._latest_selection_id in self._selections if self._latest_selection_id else False

    def _selection_for_status(self, selection_id: str | None = None) -> PendingSemanticTargetSelection | None:
        if selection_id is None:
            selection_id = self._latest_selection_id
        return self._selections.get(selection_id) if selection_id else None


class SemanticTargetTool(ToolAdapter):
    name = "vision.resolve_target"

    def __init__(
        self,
        workspace: Path,
        observer: VisionObserver | None = None,
        computer_backend: ComputerUseBackend | None = None,
        ocr: OcrExtractor | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.observer = observer or WindowsScreenObserver(workspace)
        self.computer_backend = computer_backend or WindowsComputerUseBackend(workspace, self.observer)
        self.ocr = ocr or PytesseractOcrExtractor()

    def run(self, request: ToolRequest) -> ToolResult:
        query = str(request.arguments.get("query") or request.arguments.get("target") or "").strip()
        try:
            observation = self.computer_backend.observe(target="active_window", query=query)
        except Exception:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "observe_failed"},
                display_card=DisplayCard("目标定位", "我没能看清当前画面。", "当前窗口截图失败，请先确认目标窗口在前台。", status="failed"),
                voice_line=safe_voice_line("我没能看清当前画面，你再试一次。", sprite="4"),
            )

        ocr_result = run_ocr_safely(self.ocr, observation.screenshot_path)
        regions = group_ocr_regions(ocr_result, observation.width, observation.height)
        region_state = regions_to_agent_state(regions)
        candidates = resolve_target_candidates(query, region_state)
        artifacts = [observation.screenshot_rel] if observation.screenshot_rel else []
        candidate_states = [_candidate_state(row, observation) for row in candidates]
        if not candidates:
            body = "\n".join(
                [
                    f"目标描述：{query or '未提供'}",
                    summarize_ocr_regions(regions),
                    "结果：没有找到足够明确的候选区域，请换一种更具体的说法。",
                ]
            )
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "observation": observation.to_agent_state(),
                    "ocr": ocr_result.to_agent_state(),
                    "ocr_regions": region_state,
                    "target_candidates": [],
                    "needs_clarification": True,
                    "artifacts": artifacts,
                },
                display_card=DisplayCard("目标定位", "没有找到明确目标。", body, status="info", artifacts=artifacts),
                voice_line=safe_voice_line("我没有找到明确的区域，你再描述具体一点。", sprite="4"),
            )

        candidate = candidates[0]
        if not _should_approve_candidate(candidate):
            body = "\n".join(
                [
                    f"目标描述：{query or '未提供'}",
                    _candidate_list_body(candidates),
                    "结果：候选还不够唯一，请补充位置或从候选里指定编号。",
                ]
            )
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "observation": observation.to_agent_state(),
                    "ocr": ocr_result.to_agent_state(),
                    "ocr_regions": region_state,
                    "target_candidate": _candidate_state(candidate, observation),
                    "target_candidates": candidate_states,
                    "needs_clarification": True,
                    "candidate_selection_required": True,
                    "artifacts": artifacts,
                },
                display_card=DisplayCard("目标定位", "我找到了几个可能的目标，需要你再确认一下。", body, status="info", artifacts=artifacts),
                voice_line=safe_voice_line("我找到了几个可能的目标，还需要你再确认一下。", sprite="4"),
            )

        click_args = _click_arguments(candidate, observation)
        if click_args is None:
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "observation": observation.to_agent_state(),
                    "ocr": ocr_result.to_agent_state(),
                    "ocr_regions": region_state,
                    "target_candidate": _candidate_state(candidate, observation),
                    "target_candidates": candidate_states,
                    "needs_clarification": True,
                    "artifacts": artifacts,
                },
                display_card=DisplayCard(
                    "目标定位",
                    f"找到了可能的“{candidate.label}”，但屏幕位置还不可靠。",
                    "请换一种更具体的描述，或先把目标窗口保持在前台后重试。",
                    status="info",
                    artifacts=artifacts,
                ),
                voice_line=safe_voice_line("我找到了文字，但位置还不够可靠。", sprite="4"),
            )

        body = "\n".join(
            [
                f"候选目标：{candidate.label}",
                f"所在区域：{_friendly_region(candidate.region_label)}",
                _candidate_list_body(candidates),
                summarize_ocr_regions(regions),
                "下一步：确认后才会点击这个候选区域。",
            ]
        )
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "observation": observation.to_agent_state(),
                "ocr": ocr_result.to_agent_state(),
                "ocr_regions": region_state,
                "target_candidate": _candidate_state(candidate, observation, click_args),
                "target_candidates": candidate_states,
                "approval_request": {
                    "tool": "computer.click",
                    "arguments": click_args,
                    "reason": f"点击候选目标：{candidate.label}",
                },
                "artifacts": artifacts,
            },
            display_card=DisplayCard("需要确认", f"我找到了可能的“{candidate.label}”区域，是否点击？", body, status="approval", artifacts=artifacts),
            voice_line=safe_voice_line("我找到了一个可能的目标，需要你确认后再点。", sprite="4"),
            requires_approval=True,
            risk=RiskLevel.MEDIUM,
        )


class SemanticTargetSelectionTool(ToolAdapter):
    name = "vision.select_target"

    def __init__(self, store: SemanticTargetSelectionStore) -> None:
        self.store = store

    def run(self, request: ToolRequest) -> ToolResult:
        selection_id = str(request.arguments.get("selection_id") or "").strip()
        status = self.store.status(selection_id or None)
        selection = self.store.get(selection_id, require_current=True) if selection_id else self.store.current()
        if selection is None:
            summary = _selection_status_summary(status)
            state_key = {
                "expired": "selection_expired",
                "not_current": "selection_not_current",
                "missing": "selection_missing",
            }.get(status, "selection_missing")
            return ToolResult(
                ok=True,
                agent_state={"tool": self.name, "needs_clarification": True, state_key: True, "selection_id": selection_id},
                display_card=DisplayCard("目标定位", summary, status="info"),
                voice_line=safe_voice_line("之前的候选不能继续用了，请重新说一下目标。", sprite="4"),
            )
        index = _selection_index(request.arguments.get("selection") or request.arguments.get("index"))
        if index is None or index < 1 or index > len(selection.target_candidates):
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "needs_clarification": True,
                    "selection_invalid": True,
                    "selection_id": selection.selection_id,
                    "target_candidates": selection.target_candidates,
                    "artifacts": selection.artifacts,
                },
                display_card=DisplayCard("目标定位", "没有找到这个编号的候选。", "请从候选列表里选择一个有效编号。", status="info", artifacts=selection.artifacts),
                voice_line=safe_voice_line("我没有找到这个编号的候选。", sprite="4"),
            )

        candidate = selection.target_candidates[index - 1]
        click_args = click_arguments_from_state(candidate, selection.observation)
        if click_args is None:
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "needs_clarification": True,
                    "coordinate_untrusted": True,
                    "selection_id": selection.selection_id,
                    "selected_rank": index,
                    "target_candidate": candidate,
                    "target_candidates": selection.target_candidates,
                    "artifacts": selection.artifacts,
                },
                display_card=DisplayCard("目标定位", f"已选择候选 {index}，但屏幕位置还不可靠。", "请重新观察当前窗口，或换一种更具体的目标描述。", status="info", artifacts=selection.artifacts),
                voice_line=safe_voice_line("我知道你选了哪个，但位置还不够可靠。", sprite="4"),
            )

        label = str(candidate.get("label") or "候选目标")
        body = "\n".join(
            [
                f"已选择候选：{index}",
                f"目标：{label}",
                f"所在区域：{_friendly_region(str(candidate.get('region_label') or 'unknown'))}",
                "下一步：确认后才会点击这个候选区域。",
            ]
        )
        self.store.clear(selection.selection_id)
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "selection_id": selection.selection_id,
                "selected_rank": index,
                "target_candidate": candidate,
                "target_candidates": selection.target_candidates,
                "approval_request": {
                    "tool": "computer.click",
                    "arguments": click_args,
                    "reason": f"点击已选择的候选目标：{label}",
                },
                "artifacts": selection.artifacts,
            },
            display_card=DisplayCard("需要确认", f"已选择候选 {index}，等待点击确认。", body, status="approval", artifacts=selection.artifacts),
            voice_line=safe_voice_line("我已经选好了，确认后再点击。", sprite="4"),
            requires_approval=True,
            risk=RiskLevel.MEDIUM,
        )


def _click_arguments(candidate: TargetCandidate, observation: ComputerObservation) -> dict[str, int] | None:
    screen_center = _screen_center(candidate, observation)
    if screen_center is None:
        return None
    return {"x": screen_center[0], "y": screen_center[1]}


def click_arguments_from_state(candidate_state: dict[str, Any], observation_state: dict[str, Any]) -> dict[str, int] | None:
    bbox = _bbox_tuple(candidate_state.get("bbox"))
    rect = _capture_rect_from_state(observation_state.get("capture_rect"))
    if bbox is None or rect is None:
        return None
    width = _positive_int(observation_state.get("width"))
    height = _positive_int(observation_state.get("height"))
    if width is None or height is None:
        return None
    screen_center = _screen_center_from_values(bbox, width, height, rect)
    if screen_center is None:
        return None
    return {"x": screen_center[0], "y": screen_center[1]}


def _screen_center(candidate: TargetCandidate, observation: ComputerObservation) -> tuple[int, int] | None:
    if candidate.bbox is None:
        return None
    left, top, width, height = candidate.bbox
    if width <= 0 or height <= 0 or left < 0 or top < 0:
        return None
    if left + width > observation.width + 2 or top + height > observation.height + 2:
        return None
    center_x = left + width / 2
    center_y = top + height / 2
    rect = observation.capture_rect
    if rect is None:
        return None
    return _screen_center_from_values(candidate.bbox, observation.width, observation.height, rect.to_agent_state())


def _screen_center_from_values(
    bbox: tuple[int, int, int, int],
    observation_width: int,
    observation_height: int,
    rect: dict[str, Any],
) -> tuple[int, int] | None:
    left, top, width, height = bbox
    if width <= 0 or height <= 0 or left < 0 or top < 0:
        return None
    if left + width > observation_width + 2 or top + height > observation_height + 2:
        return None
    center_x = left + width / 2
    center_y = top + height / 2
    rect_width = _positive_int(rect.get("width"))
    rect_height = _positive_int(rect.get("height"))
    screen_origin_x = _int_value(rect.get("screen_x"))
    screen_origin_y = _int_value(rect.get("screen_y"))
    if observation_width <= 0 or observation_height <= 0 or rect_width is None or rect_height is None or screen_origin_x is None or screen_origin_y is None:
        return None
    scale_x = _positive_float(rect.get("scale_x")) or observation_width / rect_width
    scale_y = _positive_float(rect.get("scale_y")) or observation_height / rect_height
    if scale_x <= 0 or scale_y <= 0:
        return None
    if not _scale_is_trusted(scale_x, scale_y):
        return None
    screen_x = screen_origin_x + round(center_x / scale_x)
    screen_y = screen_origin_y + round(center_y / scale_y)
    return (int(screen_x), int(screen_y))


def _candidate_state(
    candidate: TargetCandidate,
    observation: ComputerObservation,
    click_args: dict[str, int] | None = None,
) -> dict:
    state = candidate.to_agent_state()
    if candidate.bbox is not None:
        state["preview"] = {
            "artifact": observation.screenshot_rel,
            "bbox": list(candidate.bbox),
            "center": [candidate.bbox[0] + candidate.bbox[2] // 2, candidate.bbox[1] + candidate.bbox[3] // 2],
            "image_width": observation.width,
            "image_height": observation.height,
            "label": candidate.label,
            "region_label": candidate.region_label,
            "region_name": _friendly_region(candidate.region_label),
            "confidence": round(float(candidate.confidence), 3),
        }
    screen_center = click_args or _click_arguments(candidate, observation)
    if screen_center is not None:
        state["screen_center"] = [screen_center["x"], screen_center["y"]]
    return state


def _friendly_region(label: str) -> str:
    return {
        "top_bar": "顶部栏",
        "sidebar": "侧边区域",
        "main_content": "主内容",
        "bottom_controls": "底部控件",
    }.get(label, "未知区域")


def _scale_is_trusted(scale_x: float, scale_y: float) -> bool:
    if not (0.2 <= scale_x <= 5.0 and 0.2 <= scale_y <= 5.0):
        return False
    return abs(scale_x - scale_y) / max(scale_x, scale_y) <= 0.25


def _should_approve_candidate(candidate: TargetCandidate) -> bool:
    return candidate.confidence >= 0.72 and candidate.ambiguity == "none"


def _candidate_list_body(candidates: list[TargetCandidate]) -> str:
    if not candidates:
        return "候选：无"
    rows: list[str] = ["候选："]
    for candidate in candidates[:5]:
        confidence = round(candidate.confidence * 100)
        ambiguity = _friendly_ambiguity(candidate.ambiguity)
        reason = candidate.reason or "OCR 候选"
        rows.append(
            f"{candidate.rank or len(rows)}. {candidate.label} / {_friendly_region(candidate.region_label)} / {confidence}% / {ambiguity} / {reason}"
        )
    return "\n".join(rows)


def _friendly_ambiguity(value: str) -> str:
    return {
        "none": "较明确",
        "close_score": "分数接近",
        "low_confidence": "置信偏低",
    }.get(value, "需要确认")


def _selection_status_summary(status: str) -> str:
    return {
        "expired": "候选目标已经失效，请重新观察当前窗口。",
        "not_current": "这个候选卡已经不是当前选择，请使用最新的候选卡。",
        "missing": "没有找到可继续的候选目标，请重新观察当前窗口。",
    }.get(status, "候选目标不能继续使用，请重新观察当前窗口。")


def _selection_index(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bbox_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    except (TypeError, ValueError):
        return None


def _capture_rect_from_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = ("screen_x", "screen_y", "width", "height")
    if any(key not in value for key in required):
        return None
    return value


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
