from __future__ import annotations

from pathlib import Path

from agent_companion.core.computer_use import ComputerUseBackend, WindowsComputerUseBackend
from agent_companion.core.computer_use.schemas import ComputerObservation
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.vision import OcrExtractor, PytesseractOcrExtractor, VisionObserver, WindowsScreenObserver
from agent_companion.core.vision.ocr import run_ocr_safely
from agent_companion.core.vision.regions import group_ocr_regions, regions_to_agent_state, summarize_ocr_regions
from agent_companion.core.vision.targeting import TargetCandidate, resolve_target_candidates
from agent_companion.core.voice import safe_voice_line


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


def _click_arguments(candidate: TargetCandidate, observation: ComputerObservation) -> dict[str, int] | None:
    screen_center = _screen_center(candidate, observation)
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
    if observation.width <= 0 or observation.height <= 0 or rect.width <= 0 or rect.height <= 0:
        return None
    scale_x = float(rect.scale_x or 0) if rect.scale_x else observation.width / rect.width
    scale_y = float(rect.scale_y or 0) if rect.scale_y else observation.height / rect.height
    if scale_x <= 0 or scale_y <= 0:
        return None
    if not _scale_is_trusted(scale_x, scale_y):
        return None
    screen_x = rect.screen_x + round(center_x / scale_x)
    screen_y = rect.screen_y + round(center_y / scale_y)
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
