from __future__ import annotations

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


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
                voice_line=safe_voice_line("这个能力还没有接好。", "这个能力还没有接好。", "concerned", "4"),
            )
        return tool.run(request)

    def schemas(self) -> list[dict[str, str]]:
        return [{"name": name, "adapter": type(tool).__name__} for name, tool in sorted(self._tools.items())]

