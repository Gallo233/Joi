from __future__ import annotations

from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class FileReadTool(ToolAdapter):
    name = "files.read"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def run(self, request: ToolRequest) -> ToolResult:
        rel = str(request.arguments.get("path") or "README.md")
        path = (self.workspace / rel).resolve()
        if self.workspace not in path.parents and path != self.workspace:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "outside_workspace"},
                display_card=DisplayCard("文件读取", "拒绝读取工作区外文件。", status="failed"),
                voice_line=safe_voice_line("这个文件不在工作区里，我先不读。", sprite="4"),
            )
        if not path.is_file():
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "not_found", "path": rel},
                display_card=DisplayCard("文件读取", f"文件不存在：{rel}", status="failed"),
                voice_line=safe_voice_line("没有找到这个文件。", sprite="4"),
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        return ToolResult(
            ok=True,
            agent_state={"tool": self.name, "path": rel, "chars": len(text)},
            display_card=DisplayCard("文件读取", f"已读取：{rel}", text[:2400]),
            voice_line=safe_voice_line("文件内容我看到了。", sprite="3"),
        )

