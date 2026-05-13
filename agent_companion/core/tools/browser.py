from __future__ import annotations

import json
import time
from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class BrowserTool(ToolAdapter):
    def __init__(self, workspace: Path, name: str) -> None:
        self.workspace = workspace
        self.name = name
        self.queue_path = workspace / "data" / "agent_companion" / "browser_requests.jsonl"

    def run(self, request: ToolRequest) -> ToolResult:
        payload = {
            "id": f"browser-{int(time.time() * 1000)}",
            "tool": request.name,
            "arguments": request.arguments,
            "created_at": time.time(),
        }
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        query = str(request.arguments.get("query") or request.arguments.get("url") or "当前页面")
        return ToolResult(
            ok=True,
            agent_state={"tool": request.name, "queued": True, "query": query, "queue": self._rel(self.queue_path)},
            display_card=DisplayCard("浏览器/陪看", f"已记录观察请求：{query}", f"队列：{self._rel(self.queue_path)}", artifacts=[self._rel(self.queue_path)]),
            voice_line=safe_voice_line("我先看一下当前画面。", sprite="3"),
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)

