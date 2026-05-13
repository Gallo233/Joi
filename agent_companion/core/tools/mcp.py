from __future__ import annotations

from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class McpListTool(ToolAdapter):
    name = "mcp.list_tools"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, request: ToolRequest) -> ToolResult:
        candidates = [
            self.workspace / "data" / "config" / "mcp_servers.yaml",
            Path.home() / ".codex" / "config.toml",
        ]
        found = [str(path) for path in candidates if path.is_file()]
        return ToolResult(
            ok=True,
            agent_state={"tool": self.name, "config_paths": found},
            display_card=DisplayCard("MCP", f"找到 {len(found)} 个可能的 MCP 配置入口。", "\n".join(found) if found else "还没有发现 MCP 配置。"),
            voice_line=safe_voice_line("MCP 配置我看过了。", sprite="3"),
        )

