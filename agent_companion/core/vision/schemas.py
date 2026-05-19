from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any


@dataclass(frozen=True)
class CaptureRect:
    screen_x: int
    screen_y: int
    width: int
    height: int
    capture_scale: float = 1.0
    scale_x: float = 1.0
    scale_y: float = 1.0

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "screen_x": self.screen_x,
            "screen_y": self.screen_y,
            "width": self.width,
            "height": self.height,
            "capture_scale": round(float(self.capture_scale), 4),
            "scale_x": round(float(self.scale_x), 4),
            "scale_y": round(float(self.scale_y), 4),
        }


@dataclass(frozen=True)
class VisionObservation:
    target: str
    screenshot_path: Path
    screenshot_rel: str
    width: int
    height: int
    title: str = ""
    window_handle: int | None = None
    capture_rect: CaptureRect | None = None
    source: str = "windows"
    query: str = ""
    created_at: float = field(default_factory=time.time)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "screenshot_path": str(self.screenshot_path),
            "screenshot_rel": self.screenshot_rel,
            "width": self.width,
            "height": self.height,
            "title": self.title,
            "window_handle": self.window_handle,
            "capture_rect": self.capture_rect.to_agent_state() if self.capture_rect else None,
            "source": self.source,
            "query": self.query,
            "created_at": self.created_at,
        }

    def detail_text(self) -> str:
        lines = [
            f"target: {self.target}",
            f"title: {self.title or 'unknown'}",
            f"size: {self.width}x{self.height}",
            f"screenshot: {self.screenshot_rel}",
        ]
        if self.query:
            lines.insert(1, f"query: {self.query}")
        return "\n".join(lines)
