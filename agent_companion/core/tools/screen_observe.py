from __future__ import annotations

from pathlib import Path

from agent_companion.core.computer_use import ComputerUseBackend, WindowsComputerUseBackend
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult, VoiceLine
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.vision import OcrExtractor, PytesseractOcrExtractor, VisionObserver, VisionSummarizer, WindowsScreenObserver
from agent_companion.core.vision.ocr import OcrResult, run_ocr_safely
from agent_companion.core.vision.regions import group_ocr_regions, regions_to_agent_state, summarize_ocr_regions
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.voice import safe_voice_line


class ScreenObserveTool(ToolAdapter):
    name = "observe.screen"

    def __init__(
        self,
        workspace: Path,
        observer: VisionObserver | None = None,
        computer_backend: ComputerUseBackend | None = None,
        summarizer: VisionSummarizer | None = None,
        ocr: OcrExtractor | None = None,
    ) -> None:
        self.workspace = workspace
        self.observer = observer or WindowsScreenObserver(workspace)
        self.computer_backend = computer_backend or WindowsComputerUseBackend(workspace, self.observer)
        self.summarizer = summarizer
        self.ocr = ocr or PytesseractOcrExtractor()

    def run(self, request: ToolRequest) -> ToolResult:
        query = str(request.arguments.get("query") or "").strip()
        target = self._target_from_request(request)
        try:
            computer_observation = self.computer_backend.observe(target=target, query=query)
            observation = computer_observation.to_vision()
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "target": target, "error": type(exc).__name__, "detail": str(exc)[:500]},
                display_card=DisplayCard("画面观察", "当前画面没有截取成功。", str(exc)[:1800], status="failed"),
                voice_line=safe_voice_line("我没能截到当前画面，细节在卡片里。", sprite="4"),
            )

        ocr_result = self._run_ocr(observation)
        ocr_regions = group_ocr_regions(ocr_result, observation.width, observation.height)
        region_summary = summarize_ocr_regions(ocr_regions)
        summary_text = ""
        summary_error = ""
        model_status = "unconfigured"
        if self.summarizer is not None:
            try:
                vs = self.summarizer.summarize(observation, query)
                summary_text = (vs.text or "").strip()
                summary_error = vs.error
                model_status = "ok" if summary_text else "error"
                if not summary_text and not summary_error:
                    summary_error = "empty vision summary"
            except Exception as exc:
                summary_error = f"{type(exc).__name__}: {exc}"
                model_status = "error"

        body = observation.detail_text()
        body = f"{body}\n\n{ocr_result.detail_text()}"
        body = f"{body}\n{region_summary}"
        card_summary = self._summary(observation)
        if summary_text:
            body = f"{body}\n\n视觉摘要：{summary_text}"
            card_summary = summary_text
        elif self.summarizer is None:
            body = f"{body}\n\n视觉摘要：未配置视觉模型，截图已保存。"
            card_summary = "截图已保存；配置视觉模型后可以生成画面摘要。"
        elif summary_error:
            body = f"{body}\n\n视觉摘要：生成失败，截图已保存。"
            card_summary = "截图已保存；视觉摘要暂时没有生成出来。"

        observation_state = {
            **observation.to_agent_state(),
            "ocr": ocr_result.to_agent_state(),
            "ocr_regions": regions_to_agent_state(ocr_regions),
        }
        agent_state: dict = {
            "tool": self.name,
            "computer_observation": computer_observation.to_agent_state(),
            "observation": observation_state,
            "model_status": model_status,
            "artifacts": [observation.screenshot_rel],
        }
        if summary_text:
            agent_state["vision_summary"] = summary_text
        if summary_error:
            agent_state["vision_summary_error"] = summary_error

        voice = self._voice_for(observation, summary_text, summary_error)

        return ToolResult(
            ok=True,
            agent_state=agent_state,
            display_card=DisplayCard(
                "画面观察",
                card_summary,
                body,
                status="success",
                artifacts=[observation.screenshot_rel],
            ),
            voice_line=voice,
        )

    def _run_ocr(self, observation: VisionObservation) -> OcrResult:
        return run_ocr_safely(self.ocr, observation.screenshot_path)

    @staticmethod
    def _summary(observation: VisionObservation) -> str:
        target = "全屏" if observation.target == "fullscreen" else "当前窗口"
        title = f"《{observation.title[:28]}》" if observation.title else target
        return f"已截取{title}，尺寸 {observation.width}x{observation.height}。"

    @staticmethod
    def _target_from_request(request: ToolRequest) -> str:
        target = str(request.arguments.get("target") or "").strip()
        query = str(request.arguments.get("query") or "")
        if target:
            return target
        if any(token in query for token in ("全屏", "整个屏幕", "全桌面", "fullscreen")):
            return "fullscreen"
        return "active_window"

    @staticmethod
    def _voice_for(observation: VisionObservation, summary_text: str, summary_error: str = "") -> VoiceLine:
        if summary_text:
            return safe_voice_line("我看到了主要内容，摘要已经放进卡片里。", sprite="5")
        if summary_error:
            return safe_voice_line("我截到画面了，摘要暂时没生成出来。", sprite="4")
        return safe_voice_line("我截到画面了，需要配置视觉模型才能总结内容。", sprite="4")
