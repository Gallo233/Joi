from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import quote_plus

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class BrowserTool(ToolAdapter):
    def __init__(self, workspace: Path, name: str) -> None:
        self.workspace = workspace
        self.name = name
        self.queue_path = workspace / "data" / "agent_companion" / "browser_requests.jsonl"

    def run(self, request: ToolRequest) -> ToolResult:
        if os.environ.get("AGENT_COMPANION_BROWSER_STUB") != "1":
            try:
                return self._run_bridge(request)
            except Exception as exc:
                return ToolResult(
                    ok=False,
                    agent_state={"tool": request.name, "error": "browser_bridge_failed", "detail": str(exc)[:500]},
                    display_card=DisplayCard("浏览器", "本地浏览器执行器没有跑通。", str(exc)[:1800], status="failed"),
                    voice_line=safe_voice_line("浏览器这一步没有跑通，细节在卡片里。", sprite="4"),
                )
        return self._queue_only(request)

    def _queue_only(self, request: ToolRequest) -> ToolResult:
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

    def _run_bridge(self, request: ToolRequest) -> ToolResult:
        from mvp.browser_bridge import BrowserBridge

        bridge = BrowserBridge(self.workspace)
        action = "observe"
        arguments = dict(request.arguments)
        query = str(arguments.get("query") or arguments.get("url") or "当前页面").strip()
        if request.name == "browser.search":
            url = str(arguments.get("url") or "").strip()
            if not url:
                url = "https://www.baidu.com/s?wd=" + quote_plus(query)
            opened = bridge.execute("open_url", {"url": url}, timeout=40)
            if not opened.get("ok"):
                return self._from_response(request.name, query, opened)
            response = bridge.execute("observe", {"query": query}, timeout=45)
            response.setdefault("opened", opened)
            return self._from_response(request.name, query, response)
        if request.name == "browser.observe":
            url = str(arguments.get("url") or "").strip()
            if url:
                opened = bridge.execute("open_url", {"url": url}, timeout=40)
                if not opened.get("ok"):
                    return self._from_response(request.name, query, opened)
        response = bridge.execute(action, arguments, timeout=45)
        return self._from_response(request.name, query, response)

    def _from_response(self, tool_name: str, query: str, response: dict) -> ToolResult:
        ok = bool(response.get("ok"))
        artifacts = [str(item) for item in response.get("artifacts") or []]
        detail = str(response.get("detail") or "")[:5000]
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        if ok and self._is_blank_observation(data):
            ok = False
            detail = detail or "浏览器执行器当前是空白页，没有可用于陪看的内容。"
            summary = "当前没有可观察的网页内容。"
        else:
            summary = str(response.get("summary") or ("浏览器观察完成。" if ok else "浏览器观察失败。"))
        title = "浏览器观察" if tool_name != "browser.search" else "浏览器搜索"
        return ToolResult(
            ok=ok,
            agent_state={
                "tool": tool_name,
                "query": query,
                "summary": summary,
                "data": data,
                "artifacts": artifacts,
            },
            display_card=DisplayCard(
                title,
                summary,
                detail,
                status="success" if ok else "failed",
                artifacts=artifacts,
            ),
            voice_line=safe_voice_line("我看到页面内容了。" if ok else "我没能看清这个页面。", sprite="5" if ok else "4"),
        )

    @staticmethod
    def _is_blank_observation(data: dict) -> bool:
        url = str(data.get("url") or "").strip().lower()
        title = str(data.get("title") or "").strip()
        elements = data.get("elements") if isinstance(data.get("elements"), list) else []
        return url in {"", "about:blank"} and not title and not elements

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
