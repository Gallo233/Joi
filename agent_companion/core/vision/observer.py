from __future__ import annotations

from typing import Protocol

from agent_companion.core.vision.schemas import VisionObservation


class VisionObserver(Protocol):
    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        raise NotImplementedError
