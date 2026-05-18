from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any


@dataclass(frozen=True)
class WatchFrame:
    user_question: str
    summary: str
    title: str = ""
    artifact: str = ""
    model_status: str = "unknown"
    created_at: float = field(default_factory=time.time)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "user_question": self.user_question,
            "summary": self.summary,
            "title": self.title,
            "artifact": self.artifact,
            "model_status": self.model_status,
            "created_at": self.created_at,
        }


class WatchSession:
    def __init__(self, limit: int = 5) -> None:
        self.limit = max(1, limit)
        self._frames: list[WatchFrame] = []

    def add(self, frame: WatchFrame) -> None:
        if not frame.summary and not frame.artifact:
            return
        self._frames.append(frame)
        self._frames = self._frames[-self.limit :]

    def recent(self, limit: int = 3) -> list[WatchFrame]:
        return list(reversed(self._frames[-max(1, limit) :]))

    def has_context(self) -> bool:
        return bool(self._frames)


def answer_from_recent_frames(question: str, frames: list[WatchFrame]) -> tuple[str, str]:
    if not frames:
        return "我还没有最近的画面上下文。先让我看一下当前窗口吧。", ""
    latest = frames[0]
    title = f"《{latest.title}》" if latest.title else "刚才的画面"
    if latest.model_status == "unconfigured":
        return f"{title}的截图已经保存了，但还没有配置视觉模型，所以我现在只能确认画面已记录。", "vision_unconfigured"
    if latest.model_status == "error":
        return f"{title}的截图已经保存了，不过视觉摘要暂时没生成出来。", "vision_error"
    if len(frames) == 1:
        return f"刚才我看到的是：{latest.summary}", "vision_context"
    prior = "；".join(frame.summary for frame in frames[:3] if frame.summary)
    return f"结合最近几次画面，我看到的重点是：{prior}", "vision_context"
