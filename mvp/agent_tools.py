from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

import yaml

from mvp.agent_tool_registry import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from mvp.browser_bridge import BrowserBridge
from mvp.codex_adapter import CodexAdapter


class AgentToolbox:
    """Registered local tools. Approval routing happens before risky execution."""

    _SKIPPED_DIRS = {".git", ".venv", "__pycache__", "data/cache", "imports"}
    _BINARY_SUFFIXES = {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".wav",
        ".mp3",
        ".pth",
        ".ckpt",
        ".zip",
        ".char",
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.registry = ToolRegistry()
        self._browser = BrowserBridge(self.workspace)
        self._codex = CodexAdapter(self.workspace)
        self._browser_context_active = False
        self._register_builtin_tools()

    def tool_schemas(self) -> list[dict[str, Any]]:
        return self.registry.public_schemas()

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        return self.registry.get(tool_name)

    def build_call(self, tool_name: str, argument: str | dict[str, Any] = "") -> ToolCall:
        args = argument if isinstance(argument, dict) else self._parse_legacy_args(tool_name, argument)
        definition = self.registry.get(tool_name)
        canonical_name = definition.name if definition is not None else tool_name.strip()
        return ToolCall(canonical_name, dict(args), str(argument or ""))

    def execute_call(self, call: ToolCall, approved: bool = False) -> ToolResult:
        return self.registry.execute(call, approved=approved)

    def plan_natural_language(self, text: str) -> ToolCall | None:
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            return None
        lowered = cleaned.casefold()

        if any(token in cleaned for token in ("有哪些工具", "工具列表", "列出工具", "能用什么工具")):
            return self.build_call("tools.list", {})
        if "mcp" in lowered and any(token in cleaned for token in ("查看", "列出", "有哪些", "配置")):
            return self.build_call("mcp.list_servers", {})
        if "插件" in cleaned and any(token in cleaned for token in ("查看", "列出", "有哪些", "配置")):
            return self.build_call("plugins.list", {})
        if "事件源" in cleaned or "event source" in lowered:
            return self.build_call("events.list_sources", {})
        codex_call = self._plan_codex_request(cleaned)
        if codex_call is not None:
            return codex_call
        browser_call = self._plan_browser_request(cleaned)
        if browser_call is not None:
            return browser_call
        if "git status" in lowered or "git状态" in lowered or "git 状态" in lowered:
            return self.build_call("workspace.run_command", {"command": "git status --short"})
        if any(token in cleaned for token in ("列出目录", "列一下目录", "看看目录", "当前目录文件")):
            return self.build_call("workspace.run_command", {"command": "dir"})

        command = self._extract_after_keywords(cleaned, ("执行命令", "运行命令", "跑命令"))
        if command:
            return self.build_call("workspace.run_command", {"command": command})

        path = self._extract_file_request(cleaned)
        if path:
            return self.build_call("workspace.read_file", {"path": path})

        query = self._extract_after_keywords(cleaned, ("搜索", "查找", "找一下", "找下"))
        if query:
            return self.build_call("workspace.search_text", {"pattern": self._strip_object_marker(query)})

        return None

    def search_text(self, pattern: str, glob: str = "*") -> ToolResult:
        return self._tool_search({"pattern": pattern, "glob": glob})

    def read_file(self, relative_path: str, max_chars: int = 2400) -> ToolResult:
        return self._tool_read({"path": relative_path, "max_chars": max_chars})

    def run_command(self, command: str) -> ToolResult:
        return self._tool_run_command({"command": command})

    def run_approved_command(self, command: str) -> ToolResult:
        return self._tool_run_approved_command({"command": command})

    def _register_builtin_tools(self) -> None:
        self.registry.register(
            ToolDefinition(
                name="tools.list",
                aliases=("tools", "list_tools"),
                description="列出当前可用工具及参数 schema。",
                parameters_schema={"type": "object", "properties": {}, "required": []},
                executor=self._tool_list_tools,
                examples=("有哪些工具", "/tool tools"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="workspace.search_text",
                aliases=("search", "grep"),
                description="在当前工作区搜索文本，跳过缓存、虚拟环境和二进制资源。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "要搜索的文本"},
                        "glob": {"type": "string", "description": "文件名匹配，默认 *"},
                    },
                    "required": ["pattern"],
                },
                executor=self._tool_search,
                examples=("搜索 AgentRuntime", "/tool search AgentRuntime"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="workspace.read_file",
                aliases=("read", "open_file"),
                description="读取当前工作区内的文本文件。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "工作区内文件路径"},
                        "max_chars": {"type": "integer", "description": "最多返回字符数"},
                    },
                    "required": ["path"],
                },
                executor=self._tool_read,
                examples=("看一下 README.md", "/tool read README.md"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="workspace.run_command",
                aliases=("cmd", "shell"),
                description="执行白名单只读命令；高风险或非白名单命令会先请求授权。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "PowerShell/cmd 命令"}
                    },
                    "required": ["command"],
                },
                executor=self._tool_run_command,
                approved_executor=self._tool_run_approved_command,
                examples=("运行命令 dir", "/tool cmd git status --short"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="mcp.list_servers",
                aliases=("mcp", "mcp_servers"),
                description="读取 MVP 的 MCP 配置占位，列出已配置 MCP server。",
                parameters_schema={"type": "object", "properties": {}, "required": []},
                executor=lambda args: self._read_yaml_listing("data/config/mcp.yaml", "MCP server"),
                examples=("列出 MCP 配置",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="plugins.list",
                aliases=("plugins", "plugin_list"),
                description="读取 MVP 的插件配置占位，列出已配置插件。",
                parameters_schema={"type": "object", "properties": {}, "required": []},
                executor=lambda args: self._read_yaml_listing("data/config/plugins.yaml", "插件"),
                examples=("列出插件配置",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="events.list_sources",
                aliases=("event_sources", "sources"),
                description="列出 Codex/MCP/插件/通用事件 inbox 文件状态。",
                parameters_schema={"type": "object", "properties": {}, "required": []},
                executor=self._tool_list_event_sources,
                examples=("查看事件源",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="codex.submit_task",
                aliases=("codex", "codex_submit", "delegate_codex"),
                description="把工程任务提交给 Codex Executor 队列，可选自动启动 codex exec。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "要交给 Codex 的任务目标"},
                        "cwd": {"type": "string", "description": "任务工作目录，默认当前项目"},
                        "auto_start": {"type": "boolean", "description": "是否立即启动本机 codex exec"},
                        "approval_policy": {"type": "string", "description": "Codex approval policy，默认 never"},
                        "sandbox": {"type": "string", "description": "Codex sandbox，默认 workspace-write"},
                        "model": {"type": "string", "description": "可选 Codex 模型"},
                    },
                    "required": ["goal"],
                },
                executor=self._tool_codex_submit_task,
                examples=("让 Codex 修复浏览器观察失败的问题", "/tool codex.submit_task {\"goal\":\"跑测试并修复失败\",\"auto_start\":true}"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="codex.status",
                aliases=("codex_status",),
                description="查看 Codex Executor 队列和最近回写事件。",
                parameters_schema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string", "description": "可选任务 ID"}},
                    "required": [],
                },
                executor=self._tool_codex_status,
                examples=("查看 Codex 任务状态",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="codex.start_task",
                aliases=("codex_start", "codex_run", "codex_start_latest"),
                description="启动已提交的 Codex 任务；可指定 task_id，或 latest=true 启动最近一条。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "要启动的 Codex 任务 ID"},
                        "latest": {"type": "boolean", "description": "是否启动最近一条任务"},
                        "allow_restart": {"type": "boolean", "description": "是否允许重复启动已完成或运行中的任务"},
                    },
                    "required": [],
                },
                executor=self._tool_codex_start,
                examples=("启动最近的 Codex 任务", "/tool codex.start_task {\"latest\":true}"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="codex.cancel",
                aliases=("codex_cancel",),
                description="写入 Codex 任务取消请求；执行器需要自行监听取消队列。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "要取消的任务 ID"},
                        "reason": {"type": "string", "description": "取消原因"},
                    },
                    "required": ["task_id"],
                },
                executor=self._tool_codex_cancel,
                examples=("/tool codex.cancel codex-xxxx",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.open_url",
                aliases=("browser_open", "open_url"),
                description="使用本地 Browser Executor 打开 URL，并等待打开结果。",
                parameters_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "要打开的 URL"}},
                    "required": ["url"],
                },
                executor=lambda args: self._tool_run_browser_action("open_url", args),
                examples=("打开 https://example.com", "/tool browser.open_url https://example.com"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.search",
                aliases=("browser_search", "web_search"),
                description="使用本地 Browser Executor 打开网页搜索结果页。",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "网页搜索关键词"}},
                    "required": ["query"],
                },
                executor=self._tool_browser_search,
                examples=("打开浏览器并搜索 Shinsekai", "/tool browser.search Shinsekai"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.search_extract",
                aliases=("browser_search_extract", "web_search_extract"),
                description="使用本地 Browser Executor 搜索网页、视觉观察并提取页面文本，供角色观察和反应。",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "网页搜索关键词"}},
                    "required": ["query"],
                },
                executor=self._tool_browser_search_extract,
                examples=("自搜一下自己有什么评论", "/tool browser.search_extract 七海千秋 评论"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.observe",
                aliases=("browser_observe", "browser_vision"),
                description="观察当前浏览器画面：截图、视口信息、可见元素位置和文字。",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "可选的观察目的"}},
                    "required": [],
                },
                executor=lambda args: self._tool_run_browser_action("observe", args),
                examples=("观察当前页面", "看看浏览器画面里有什么"),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.screenshot",
                aliases=("browser_screenshot",),
                description="使用本地 Browser Executor 给当前网页截图。",
                parameters_schema={
                    "type": "object",
                    "properties": {"label": {"type": "string", "description": "截图标签"}},
                    "required": [],
                },
                executor=lambda args: self._tool_run_browser_action("screenshot", args),
                examples=("给当前网页截图",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.click",
                aliases=("browser_click",),
                description="使用本地 Browser Executor 点击页面元素。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "按钮文字、选择器或元素描述"}
                    },
                    "required": ["target"],
                },
                executor=lambda args: self._tool_run_browser_action("click", args),
                examples=("在网页上点击登录按钮",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.type_text",
                aliases=("browser_type",),
                description="使用本地 Browser Executor 在页面输入文本。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输入的文本"},
                        "target": {"type": "string", "description": "输入框描述或选择器"},
                    },
                    "required": ["text"],
                },
                executor=lambda args: self._tool_run_browser_action("type_text", args),
                examples=("在搜索框输入 Shinsekai",),
            )
        )
        self.registry.register(
            ToolDefinition(
                name="browser.extract_text",
                aliases=("browser_extract",),
                description="使用本地 Browser Executor 提取当前页面文本。",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "可选的提取重点"}},
                    "required": [],
                },
                executor=lambda args: self._tool_run_browser_action("extract_text", args),
                examples=("提取当前网页文本",),
            )
        )

    def _tool_list_tools(self, args: dict[str, Any]) -> ToolResult:
        schemas = self.tool_schemas()
        lines = [
            f"- {row['name']}: {row['description']}"
            for row in schemas
        ]
        return ToolResult(True, f"当前注册了 {len(schemas)} 个工具。", "\n".join(lines), data={"tools": schemas})

    def _tool_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("pattern") or "").strip()
        glob = str(args.get("glob") or "*").strip() or "*"
        if not query:
            return ToolResult(False, "搜索词不能为空。")
        matched: list[str] = []
        for path in self._iter_text_candidates(glob):
            if path.suffix.lower() in self._BINARY_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if query.casefold() in line.casefold():
                    rel = path.relative_to(self.workspace).as_posix()
                    matched.append(f"{rel}:{line_no}: {line.strip()[:120]}")
                    if len(matched) >= 20:
                        break
            if len(matched) >= 20:
                break
        if not matched:
            return ToolResult(True, f"没有找到包含“{query}”的文本。", data={"matches": []})
        return ToolResult(
            True,
            f"找到 {len(matched)} 条匹配。",
            "\n".join(matched),
            data={"matches": matched},
        )

    def _tool_read(self, args: dict[str, Any]) -> ToolResult:
        relative_path = str(args.get("path") or "").strip()
        max_chars = int(args.get("max_chars") or 2400)
        try:
            path = self._resolve_workspace_path(relative_path)
        except ValueError as exc:
            return ToolResult(False, str(exc))
        if not path.is_file():
            return ToolResult(False, f"文件不存在：{relative_path}")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(False, f"读取失败：{exc}")
        trimmed = content[:max_chars]
        suffix = "\n..." if len(content) > max_chars else ""
        rel = path.relative_to(self.workspace).as_posix()
        return ToolResult(
            True,
            f"已读取 {rel}。",
            trimmed + suffix,
            data={"path": rel, "truncated": len(content) > max_chars},
        )

    def _tool_run_command(self, args: dict[str, Any]) -> ToolResult:
        cleaned = str(args.get("command") or "").strip()
        if not cleaned:
            return ToolResult(False, "命令不能为空。")
        if self._requires_approval(cleaned):
            return ToolResult(
                False,
                "该命令属于受限操作，需要你确认授权。",
                detail=cleaned,
                requires_approval=True,
                data={"command": cleaned},
            )
        if not self._is_allowed_command(cleaned):
            return ToolResult(
                False,
                "当前工具层只开放只读或低风险命令。",
                detail=cleaned,
                requires_approval=True,
                data={"command": cleaned},
            )
        return self._execute_command(cleaned)

    def _tool_run_approved_command(self, args: dict[str, Any]) -> ToolResult:
        cleaned = str(args.get("command") or "").strip()
        if not cleaned:
            return ToolResult(False, "命令不能为空。")
        return self._execute_command(cleaned)

    def _tool_list_event_sources(self, args: dict[str, Any]) -> ToolResult:
        sources = []
        for name in (
            "inbox",
            "codex",
            "codex_requests",
            "codex_cancellations",
            "mcp",
            "plugins",
            "browser",
            "browser_requests",
            "browser_responses",
        ):
            path = self.workspace / "data" / "agent_events" / f"{name}.jsonl"
            sources.append(
                {
                    "name": name,
                    "path": path.relative_to(self.workspace).as_posix(),
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                }
            )
        lines = [
            f"- {row['name']}: {row['path']} ({'存在' if row['exists'] else '未创建'}, {row['size']} bytes)"
            for row in sources
        ]
        return ToolResult(True, f"当前有 {len(sources)} 个事件源入口。", "\n".join(lines), data={"sources": sources})

    def _tool_codex_submit_task(self, args: dict[str, Any]) -> ToolResult:
        goal = str(args.get("goal") or "").strip()
        if not goal:
            return ToolResult(False, "Codex 任务目标不能为空。")
        try:
            task = self._codex.submit_task(
                goal=goal,
                cwd=str(args.get("cwd") or "").strip() or None,
                mode=str(args.get("mode") or "execute"),
                approval_policy=str(args.get("approval_policy") or "never"),
                sandbox=str(args.get("sandbox") or "workspace-write"),
                model=str(args.get("model") or ""),
                auto_start=bool(args.get("auto_start", False)),
            )
        except Exception as exc:
            return ToolResult(False, f"Codex 任务提交失败：{exc}")
        rel = self._codex.request_path.relative_to(self.workspace).as_posix()
        detail = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        summary = "Codex 任务已提交并启动。" if task.auto_start else "Codex 任务已提交，等待执行器处理。"
        return ToolResult(
            True,
            summary,
            detail,
            data={"task": task.to_dict(), "request_path": rel},
            artifacts=[rel],
        )

    def _tool_codex_status(self, args: dict[str, Any]) -> ToolResult:
        task_id = str(args.get("task_id") or "").strip()
        status = self._codex.status(task_id=task_id)
        requests = status.get("requests") or []
        latest = status.get("latest_events") or {}
        lines = []
        for row in requests:
            task_id_value = str(row.get("id") or "")
            event = latest.get(task_id_value) if isinstance(latest, dict) else None
            event_type = event.get("type") if isinstance(event, dict) else row.get("status", "queued")
            message = event.get("message") if isinstance(event, dict) else row.get("goal", "")
            lines.append(f"- {task_id_value}: {event_type} - {message}")
        if not lines:
            lines.append("没有找到 Codex 任务。")
        return ToolResult(
            True,
            f"找到 {len(requests)} 个 Codex 任务记录。",
            "\n".join(lines) + "\n\n" + json.dumps(status.get("paths", {}), ensure_ascii=False, indent=2),
            data=status,
        )

    def _tool_codex_cancel(self, args: dict[str, Any]) -> ToolResult:
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(False, "task_id 不能为空。")
        try:
            payload = self._codex.cancel_task(task_id, str(args.get("reason") or ""))
        except Exception as exc:
            return ToolResult(False, f"Codex 取消请求写入失败：{exc}")
        rel = self._codex.cancel_path.relative_to(self.workspace).as_posix()
        return ToolResult(
            True,
            "Codex 取消请求已写入。",
            json.dumps(payload, ensure_ascii=False, indent=2),
            data={"cancel": payload, "cancel_path": rel},
            artifacts=[rel],
        )

    def _tool_codex_start(self, args: dict[str, Any]) -> ToolResult:
        task_id = str(args.get("task_id") or "").strip()
        latest = bool(args.get("latest", False)) or not task_id
        allow_restart = bool(args.get("allow_restart", False))
        try:
            task = self._codex.start_task_by_id(task_id=task_id, latest=latest, allow_restart=allow_restart)
        except Exception as exc:
            return ToolResult(False, f"Codex 任务启动失败：{exc}")
        detail = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        return ToolResult(
            True,
            f"Codex 任务已启动：{task.id}",
            detail,
            data={"task": task.to_dict()},
        )

    def _tool_run_browser_action(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "open_url" and not str(args.get("url") or "").strip():
            return ToolResult(False, "URL 不能为空。")
        if action == "click" and not str(args.get("target") or "").strip():
            return ToolResult(False, "点击目标不能为空。")
        if action == "type_text" and not str(args.get("text") or "").strip():
            return ToolResult(False, "输入文本不能为空。")
        try:
            response = self._browser.execute(action, args, timeout=60.0)
        except TimeoutError as exc:
            return ToolResult(
                False,
                "浏览器执行超时。",
                str(exc),
                data={"action": action, "arguments": args},
            )
        except Exception as exc:
            return ToolResult(
                False,
                f"浏览器执行器不可用：{exc}",
                data={"action": action, "arguments": args},
            )
        ok = bool(response.get("ok"))
        if ok:
            self._browser_context_active = True
        summary = str(response.get("summary") or ("浏览器动作已完成。" if ok else "浏览器动作失败。"))
        detail = str(response.get("detail") or "")
        artifacts = [str(item) for item in response.get("artifacts") or []]
        return ToolResult(
            ok,
            summary,
            detail,
            data={"browser": response},
            artifacts=artifacts,
        )

    def _tool_browser_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(False, "搜索词不能为空。")
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}"
        result = self._tool_run_browser_action("open_url", {"url": url})
        if result.ok:
            return ToolResult(
                True,
                f"已打开网页搜索：{query}",
                result.detail,
                data={**result.data, "query": query, "url": url},
                artifacts=result.artifacts,
            )
        return result

    def _tool_browser_search_extract(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(False, "搜索词不能为空。")
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}"
        opened = self._tool_run_browser_action("open_url", {"url": url})
        if not opened.ok:
            return opened
        time.sleep(1.2)
        observed = self._tool_run_browser_action("observe", {"query": query})
        extracted = self._tool_run_browser_action("extract_text", {"query": query})
        if not observed.ok and not extracted.ok:
            return extracted
        detail = (
            f"搜索关键词: {query}\n"
            f"搜索 URL: {url}\n\n"
            f"--- 打开结果 ---\n{opened.detail}\n\n"
            f"--- 视觉观察 ---\n{observed.detail}\n\n"
            f"--- 页面文本 ---\n{extracted.detail if extracted.ok else '页面文本提取失败，已使用视觉观察结果。'}"
        )
        artifacts = [*opened.artifacts, *observed.artifacts, *extracted.artifacts]
        return ToolResult(
            True,
            f"已搜索并观察页面：{query}",
            detail,
            data={
                "query": query,
                "url": url,
                "opened": opened.data,
                "observed": observed.data,
                "extracted": extracted.data,
            },
            artifacts=artifacts,
        )

    def _read_yaml_listing(self, relative_path: str, label: str) -> ToolResult:
        path = self.workspace / relative_path
        if not path.is_file():
            return ToolResult(True, f"还没有配置 {label}。", data={"items": []})
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return ToolResult(False, f"{label} 配置读取失败：{exc}")
        detail = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
        empty = payload in ({}, [], {"servers": {}}, {"plugins": []})
        summary = f"还没有配置 {label}。" if empty else f"已读取 {label} 配置。"
        return ToolResult(True, summary, detail, data={"config": payload})

    def _execute_command(self, command: str) -> ToolResult:
        try:
            result = subprocess.run(
                command,
                cwd=self.workspace,
                shell=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except Exception as exc:
            return ToolResult(False, f"命令执行失败：{exc}", detail=command, data={"command": command})
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.returncode != 0:
            return ToolResult(
                False,
                f"命令退出码 {result.returncode}。",
                output or command,
                data={"command": command, "returncode": result.returncode},
            )
        return ToolResult(
            True,
            "命令执行完成。",
            output[:2400],
            data={"command": command, "returncode": result.returncode},
        )

    def _parse_legacy_args(self, tool_name: str, argument: str) -> dict[str, Any]:
        raw = (argument or "").strip()
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
        definition = self.registry.get(tool_name)
        name = definition.name if definition is not None else tool_name
        if name == "workspace.search_text":
            return {"pattern": raw}
        if name == "workspace.read_file":
            return {"path": raw}
        if name == "workspace.run_command":
            return {"command": raw}
        if name == "codex.submit_task":
            return {"goal": raw}
        if name == "codex.status":
            return {"task_id": raw}
        if name == "codex.start_task":
            if raw.startswith("{"):
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    pass
            return {"task_id": raw, "latest": not bool(raw)}
        if name == "codex.cancel":
            return {"task_id": raw}
        if name == "browser.open_url":
            return {"url": raw}
        if name == "browser.search":
            return {"query": raw}
        if name == "browser.search_extract":
            return {"query": raw}
        if name == "browser.observe":
            return {"query": raw}
        if name == "browser.click":
            return {"target": raw}
        if name == "browser.type_text":
            return {"text": raw}
        if name == "browser.screenshot":
            return {"label": raw}
        if name == "browser.extract_text":
            return {"query": raw}
        return {}

    def _iter_text_candidates(self, glob: str):
        for root, dirs, files in os.walk(self.workspace):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if not self._should_skip_dir(root_path / name)]
            for file_name in files:
                if fnmatch.fnmatch(file_name, glob):
                    yield root_path / file_name

    def _should_skip_dir(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.workspace).as_posix()
        except ValueError:
            return True
        return rel in self._SKIPPED_DIRS or any(rel.startswith(f"{item}/") for item in self._SKIPPED_DIRS)

    def _resolve_workspace_path(self, relative_path: str) -> Path:
        raw = Path(relative_path.strip())
        candidate = raw if raw.is_absolute() else self.workspace / raw
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("只允许读取当前工作区内的文件。") from exc
        return resolved

    @staticmethod
    def _requires_approval(command: str) -> bool:
        lowered = command.casefold()
        risky_tokens = (
            " install ",
            "pip install",
            "npm install",
            "pnpm install",
            "yarn install",
            "curl ",
            "invoke-webrequest",
            "start-process",
            "remove-item",
            "del ",
            "rm ",
            "move-item",
            "copy-item",
            "shutdown",
            "reboot",
        )
        return any(token in f" {lowered} " for token in risky_tokens)

    @staticmethod
    def _is_allowed_command(command: str) -> bool:
        lowered = command.strip().casefold()
        allowed_prefixes = (
            "git status",
            "git diff",
            "rg ",
            "dir",
            "get-childitem",
            "python --version",
            ".\\.venv\\scripts\\python.exe --version",
        )
        return lowered.startswith(allowed_prefixes)

    @staticmethod
    def _extract_after_keywords(text: str, keywords: tuple[str, ...]) -> str:
        for keyword in keywords:
            if keyword in text:
                return text.split(keyword, 1)[1].strip(" ：:，,")
        return ""

    @staticmethod
    def _extract_file_request(text: str) -> str:
        if not any(token in text for token in ("读取", "打开", "查看", "看一下", "看下")):
            return ""
        quoted = re.search(r"[`\"“']([^`\"”']+)[`\"”']", text)
        if quoted:
            return quoted.group(1).strip()
        path_like = re.search(r"([\\./\w\u4e00-\u9fff-]+\.(?:py|md|yaml|yml|json|txt|toml|bat))", text)
        if path_like:
            return path_like.group(1).strip()
        if "README" in text.upper():
            return "README.md"
        return ""

    @staticmethod
    def _strip_object_marker(text: str) -> str:
        text = text.strip()
        text = re.sub(r"^(一下|下|有关|关于|包含)", "", text).strip()
        return text.strip(" `\"“”'")

    def _plan_codex_request(self, text: str) -> ToolCall | None:
        lowered = text.casefold()
        if any(token in text for token in ("Codex状态", "Codex 状态", "查看 Codex 任务", "查看Codex任务")):
            return self.build_call("codex.status", {})
        if any(token in text for token in ("启动最近 Codex", "启动最近的 Codex", "运行最近 Codex", "执行最近 Codex")):
            return self.build_call("codex.start_task", {"latest": True})
        task_id_match = re.search(r"codex-[0-9a-fA-F]{8,}", text)
        if task_id_match and any(token in text for token in ("启动", "运行", "执行")):
            return self.build_call("codex.start_task", {"task_id": task_id_match.group(0)})
        explicit = "codex" in lowered or "交给codex" in lowered or "让codex" in lowered
        codeish = any(token in text for token in ("代码", "项目", "仓库", "测试", "编译", "文件", "功能", "bug", "BUG"))
        actionish = any(
            token in text
            for token in ("修复", "修一下", "修改", "实现", "加", "新增", "重构", "排查", "跑测试", "检查", "提交")
        )
        if not explicit and not (codeish and actionish):
            return None
        goal = text
        for prefix in ("让codex", "让 Codex", "交给codex", "交给 Codex", "请codex", "请 Codex"):
            if goal.casefold().startswith(prefix.casefold()):
                goal = goal[len(prefix) :].strip(" ：:,，")
                break
        auto_start = any(token in text for token in ("直接启动", "立即启动", "直接跑", "自动启动", "现在执行"))
        return self.build_call(
            "codex.submit_task",
            {
                "goal": goal,
                "auto_start": auto_start,
                "approval_policy": "never",
                "sandbox": "workspace-write",
            },
        )

    def _plan_browser_request(self, text: str) -> ToolCall | None:
        lowered = text.casefold()
        browserish = any(token in text for token in ("浏览器", "网页", "页面", "网站")) or "browser" in lowered
        url_match = re.search(r"(?:https?://|file://)[^\s，。；]+|(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/[^\s，。；]*)?", text)
        if url_match and any(token in text for token in ("打开", "访问", "进入", "浏览")):
            return self.build_call("browser.open_url", {"url": url_match.group(0)})
        browser_search = self._extract_browser_search_request(text)
        if browser_search:
            tool = "browser.search_extract" if self._wants_browser_observation(text) else "browser.search"
            return self.build_call(tool, {"query": browser_search})
        if any(
            token in text
            for token in (
                "观察当前页面",
                "观察页面",
                "看看当前页面",
                "看看浏览器画面",
                "你看到了什么",
                "你看到什么",
                "看到什么",
                "说说你看到",
            )
        ):
            return self.build_call("browser.observe", {"query": text})
        if "截图" in text:
            return self.build_call("browser.screenshot", {"label": "当前页面"})
        if any(token in text for token in ("提取", "读取", "总结", "看看")) and any(
            token in text for token in ("文本", "内容", "页面", "网页")
        ):
            return self.build_call("browser.extract_text", {"query": text})
        click_target = self._extract_after_keywords(text, ("点击", "点一下", "点开"))
        if click_target and (browserish or any(token in text for token in ("按钮", "链接", "页面", "网页", "当前"))):
            return self.build_call("browser.click", {"target": click_target})
        type_payload = self._extract_browser_type_request(text)
        if type_payload and (
            browserish or any(token in text for token in ("输入框", "搜索框", "地址栏", "页面", "网页", "当前"))
        ):
            return self.build_call("browser.type_text", type_payload)
        return None

    def _extract_browser_search_request(self, text: str) -> str:
        if self._is_workspace_search_request(text):
            return ""
        browserish = any(token in text for token in ("浏览器", "网页", "页面", "网站", "网上", "联网"))
        self_search = any(token in text for token in ("自己", "自搜", "百科"))
        if not browserish and not self._browser_context_active and not self_search:
            return ""
        if self_search:
            query = self._current_character_name()
            suffixes = []
            if "百科" in text:
                suffixes.append("百科")
            if any(token in text for token in ("评论", "评价", "感想", "大家")):
                suffixes.append("评论")
            return " ".join([query, *suffixes]).strip()
        query = self._extract_after_keywords(
            text,
            ("搜索一下", "搜索下", "搜索", "搜一下", "搜下", "搜", "再找一下", "再找", "找一下", "找下", "查一下", "查下"),
        )
        if not query:
            return ""
        query = re.sub(r"^(一下|下|有关|关于)", "", query).strip(" `\"“”'")
        return query

    @staticmethod
    def _wants_browser_observation(text: str) -> bool:
        return any(
            token in text
            for token in ("自己", "自搜", "百科", "评论", "评价", "感想", "怎么看", "看法", "读一下", "看看内容")
        )

    def _current_character_name(self) -> str:
        path = self.workspace / "config.yaml"
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            characters = payload.get("characters") or []
            if isinstance(characters, list) and characters:
                name = str(characters[0].get("name") or "").strip()
                if name:
                    return name
        except Exception:
            pass
        return "七海千秋"

    @staticmethod
    def _is_workspace_search_request(text: str) -> bool:
        lowered = text.casefold()
        workspace_markers = (
            "项目",
            "工作区",
            "代码",
            "源码",
            "文件",
            "目录",
            "repo",
            "repository",
            "workspace",
            "codebase",
        )
        if any(marker in lowered for marker in workspace_markers):
            return True
        return bool(re.search(r"[\\./\w\u4e00-\u9fff-]+\.(?:py|md|yaml|yml|json|txt|toml|bat)", text))

    @staticmethod
    def _extract_browser_type_request(text: str) -> dict[str, str] | None:
        match = re.search(r"(?:在|往)?(.{0,24}?(?:输入框|搜索框|文本框|地址栏|框|栏))? *(?:输入|填入|键入)[：: ]*(.+)", text)
        if not match:
            return None
        target = (match.group(1) or "").strip(" ，,。")
        value = (match.group(2) or "").strip(" `\"“”'")
        if not value:
            return None
        payload = {"text": value}
        if target:
            payload["target"] = target
        return payload
