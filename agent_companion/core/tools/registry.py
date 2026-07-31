"""Registry of the tool adapters Core will run.

Membership is decided in source, by ``build_tool_registry``. There is no
discovery step and nothing here loads code from disk: a third-party extension
reaches Joi as an Agent Skill, which runs in the out-of-host sandboxed runner
and never imports into this process (TDD §11.1, ADR-007).
"""
from __future__ import annotations

import logging
from typing import Any

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolAdapter] = {}

    def register(self, tool: ToolAdapter) -> None:
        self._tools[tool.name] = tool

    def run(self, request: ToolRequest) -> ToolResult:
        tool = self._tools.get(request.name)
        if tool is None:
            return ToolResult(
                ok=False,
                agent_state={"tool": request.name, "error": "unknown_tool"},
                display_card=DisplayCard("未知工具", f"没有注册工具：{request.name}", status="failed"),
                voice_line=safe_voice_line("这个能力还没有接好。", sprite="4"),
            )
        return tool.run(request)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolAdapter | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, str]]:
        return [{"name": name, "adapter": type(tool).__name__} for name, tool in sorted(self._tools.items())]

    def detailed_schemas(self) -> list[dict[str, Any]]:
        return [{"name": name, "adapter": type(tool).__name__} for name, tool in sorted(self._tools.items())]
