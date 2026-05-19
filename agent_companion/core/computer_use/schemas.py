from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from agent_companion.core.vision.schemas import VisionObservation


@dataclass(frozen=True)
class ComputerObservation:
    target: str
    screenshot_path: Path
    screenshot_rel: str
    width: int
    height: int
    title: str = ""
    window_handle: int | None = None
    source: str = "computer"
    query: str = ""
    ocr: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_vision(cls, observation: VisionObservation) -> "ComputerObservation":
        return cls(
            target=observation.target,
            screenshot_path=observation.screenshot_path,
            screenshot_rel=observation.screenshot_rel,
            width=observation.width,
            height=observation.height,
            title=observation.title,
            window_handle=observation.window_handle,
            source=observation.source,
            query=observation.query,
            ocr={},
            created_at=observation.created_at,
        )

    def to_vision(self) -> VisionObservation:
        return VisionObservation(
            target=self.target,
            screenshot_path=self.screenshot_path,
            screenshot_rel=self.screenshot_rel,
            width=self.width,
            height=self.height,
            title=self.title,
            window_handle=self.window_handle,
            source=self.source,
            query=self.query,
            created_at=self.created_at,
        )

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "screenshot_path": str(self.screenshot_path),
            "screenshot_rel": self.screenshot_rel,
            "width": self.width,
            "height": self.height,
            "title": self.title,
            "window_handle": self.window_handle,
            "source": self.source,
            "query": self.query,
            "created_at": self.created_at,
        }
        if self.ocr:
            payload["ocr"] = self.ocr
        return payload


@dataclass(frozen=True)
class ComputerAction:
    action_type: str
    x: int | None = None
    y: int | None = None
    text: str = ""
    delta: int = 0
    keys: tuple[str, ...] = ()
    button: str = "left"

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.action_type,
            "button": self.button,
        }
        if self.x is not None and self.y is not None:
            payload["x"] = self.x
            payload["y"] = self.y
        if self.text:
            payload["text_length"] = len(self.text)
        if self.delta:
            payload["delta"] = self.delta
        if self.keys:
            payload["keys"] = list(self.keys)
        return payload


@dataclass(frozen=True)
class ComputerUseResult:
    ok: bool
    action: ComputerAction | None = None
    observation: ComputerObservation | None = None
    summary: str = ""
    detail: str = ""
    error: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.action:
            payload["action"] = self.action.to_agent_state()
        if self.observation:
            payload["observation"] = self.observation.to_agent_state()
        if self.summary:
            payload["summary"] = self.summary
        if self.error:
            payload["error"] = self.error
        return payload
