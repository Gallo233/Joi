from __future__ import annotations

from typing import Protocol

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult


class ComputerUseBackend(Protocol):
    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        ...

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        ...
