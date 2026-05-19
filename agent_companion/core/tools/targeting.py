from __future__ import annotations

from pathlib import Path

from agent_companion.core.computer_use import ComputerUseBackend, WindowsComputerUseBackend
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
        click_args = _click_arguments(candidate)
        if click_args is None:
            return ToolResult(
                ok=True,
                agent_state={
                    "tool": self.name,
                    "observation": observation.to_agent_state(),
                    "ocr": ocr_result.to_agent_state(),
                    "ocr_regions": region_state,
                    "target_candidates": [candidate.to_agent_state() for candidate in candidates],
                    "needs_clarification": True,
                    "artifacts": artifacts,
                },
                display_card=DisplayCard("目标定位", f"找到了可能的“{candidate.label}”，但位置不够明确。", "请换一种更具体的描述，或直接给出坐标。", status="info", artifacts=artifacts),
                voice_line=safe_voice_line("我找到了文字，但位置还不够明确。", sprite="4"),
            )

        body = "\n".join(
            [
                f"候选目标：{candidate.label}",
                f"所在区域：{_friendly_region(candidate.region_label)}",
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
                "target_candidate": candidate.to_agent_state(),
                "target_candidates": [row.to_agent_state() for row in candidates],
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


def _click_arguments(candidate: TargetCandidate) -> dict[str, int] | None:
    if candidate.bbox is None:
        return None
    left, top, width, height = candidate.bbox
    return {"x": left + width // 2, "y": top + height // 2}


def _friendly_region(label: str) -> str:
    return {
        "top_bar": "顶部栏",
        "sidebar": "侧边区域",
        "main_content": "主内容",
        "bottom_controls": "底部控件",
    }.get(label, "未知区域")
