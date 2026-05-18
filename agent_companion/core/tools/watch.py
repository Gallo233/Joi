from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch import WatchAnswerer, WatchFrame


class WatchRecallTool(ToolAdapter):
    name = "watch.recall"

    def __init__(self, workspace: Path, recent_frames: Callable[[int], list[WatchFrame]], answerer: WatchAnswerer | None = None) -> None:
        self.workspace = workspace.resolve()
        self._recent_frames = recent_frames
        self._answerer = answerer or WatchAnswerer(self.workspace)

    def run(self, request: ToolRequest) -> ToolResult:
        question = str(request.arguments.get("query") or "").strip()
        frames = self._recent_frames(3)
        answer, status = self._answerer.answer(question, frames)
        artifacts = [frame.artifact for frame in frames[:1] if frame.artifact]
        body_lines = [f"问题：{question or '追问'}", f"回答：{answer}"]
        if frames:
            body_lines.append("最近视觉上下文：")
            for index, frame in enumerate(frames, start=1):
                title = frame.title or "未知窗口"
                body_lines.append(f"{index}. {title} - {frame.summary}")
        else:
            body_lines.append("最近视觉上下文：暂无")
        has_context = bool(frames)
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "watch_answer": answer,
                "watch_context": [frame.to_agent_state() for frame in frames],
                "model_status": status or "no_context",
                "answer_source": "model" if self._answerer.last_used_model else "template",
                "artifacts": artifacts,
            },
            display_card=DisplayCard(
                "陪看追问",
                answer,
                "\n".join(body_lines),
                status="success" if has_context else "info",
                artifacts=artifacts,
            ),
            voice_line=(
                safe_voice_line(_voice_answer(answer), sprite="5")
                if has_context
                else safe_voice_line("我还没有最近的画面上下文。", sprite="4")
            ),
        )


def _voice_answer(answer: str) -> str:
    first = (answer or "").replace("\n", " ").strip()
    for separator in ("。", "！", "？", ".", "!", "?"):
        if separator in first:
            first = first.split(separator, 1)[0] + separator
            break
    return first[:120] or "我根据刚才看到的内容回答你。"
