from __future__ import annotations

from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.vision import VisionObserver, WindowsScreenObserver
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.voice import safe_voice_line


class ScreenObserveTool(ToolAdapter):
    name = "observe.screen"

    def __init__(self, workspace: Path, observer: VisionObserver | None = None) -> None:
        self.workspace = workspace
        self.observer = observer or WindowsScreenObserver(workspace)

    def run(self, request: ToolRequest) -> ToolResult:
        query = str(request.arguments.get("query") or "").strip()
        target = self._target_from_request(request)
        try:
            observation = self.observer.observe(target=target, query=query)
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "target": target, "error": type(exc).__name__, "detail": str(exc)[:500]},
                display_card=DisplayCard("画面观察", "当前画面没有截取成功。", str(exc)[:1800], status="failed"),
                voice_line=safe_voice_line("我没能截到当前画面，细节在卡片里。", sprite="4"),
            )

        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "observation": observation.to_agent_state(),
                "artifacts": [observation.screenshot_rel],
            },
            display_card=DisplayCard(
                "画面观察",
                self._summary(observation),
                observation.detail_text(),
                status="success",
                artifacts=[observation.screenshot_rel],
            ),
            voice_line=safe_voice_line("我截到当前画面了。", sprite="5"),
        )

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
