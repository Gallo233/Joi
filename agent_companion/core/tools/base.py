from __future__ import annotations

from abc import ABC, abstractmethod

from agent_companion.core.schemas import ToolRequest, ToolResult


class ToolAdapter(ABC):
    name: str

    @abstractmethod
    def run(self, request: ToolRequest) -> ToolResult:
        raise NotImplementedError

