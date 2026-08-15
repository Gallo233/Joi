from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_companion.core.coercion import optional_int
from agent_companion.core.rpc import JsonRpcRouter, RpcMethodNotFound


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "joi_watch_start",
        "description": "Start Joi watch-together context for the current screen or media.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "transcript_source": {"type": "string", "enum": ["system_audio", "ocr_subtitle", "auto"]},
            },
        },
    },
    {
        "name": "joi_watch_status",
        "description": "Read current Joi watch-together status and recent visual/transcript context.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "joi_watch_recall",
        "description": "Ask Joi to answer using recent watch-together visual/transcript context.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "joi_screen_observe",
        "description": "Observe the current Mac screen or active window before deciding UI actions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "target": {"type": "string", "enum": ["active_window", "fullscreen"]},
                "sample_count": {"type": "number"},
                "sample_interval_ms": {"type": "number"},
                "skip_summary": {"type": "boolean"},
            },
        },
    },
    {
        "name": "joi_goal_verify",
        "description": "Observe the current screen and verify whether the user's UI goal is actually satisfied before reporting completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "target": {"type": "string", "enum": ["active_window", "fullscreen"]},
                "required_terms": {"type": "array", "items": {"type": "string"}},
                "any_terms": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "joi_browser_current_state",
        "description": "Read running macOS browser window titles and document URLs, useful for page verification even when Joi is frontmost. This is read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "joi_target_resolve",
        "description": "Find likely clickable screen targets from the current observation and a natural-language target. Use joi_computer_click_target when the user wants the target clicked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "joi_computer_click_target",
        "description": "Find and click a visible UI target by natural-language label or intent through Joi's approved computer-use harness. Use this for requests like clicking a named button, tab, link, or menu item in the current app/page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "target": {"type": "string"},
                "goal": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "joi_computer_click",
        "description": "Click through Joi's approved computer-use harness. Coordinates default to pixels in the most recent joi_screen_observe screenshot; pass coordinate_space='screen' for macOS screen points.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string"},
                "coordinate_space": {"type": "string", "enum": ["screenshot", "screen"]},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "joi_computer_double_click",
        "description": "Double-click through Joi's approved computer-use harness. Coordinates default to pixels in the most recent joi_screen_observe screenshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string"},
                "coordinate_space": {"type": "string", "enum": ["screenshot", "screen"]},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "joi_computer_drag",
        "description": "Drag through Joi's approved computer-use harness. Coordinates default to pixels in the most recent joi_screen_observe screenshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "end_x": {"type": "number"},
                "end_y": {"type": "number"},
                "button": {"type": "string"},
                "coordinate_space": {"type": "string", "enum": ["screenshot", "screen"]},
            },
            "required": ["x", "y", "end_x", "end_y"],
        },
    },
    {
        "name": "joi_computer_type_text",
        "description": "Type text into the current foreground UI through Joi's approved computer-use harness.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "joi_computer_scroll",
        "description": "Scroll the foreground UI through Joi's approved computer-use harness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "number"},
                "delta": {"type": "number"},
            },
        },
    },
    {
        "name": "joi_computer_hotkey",
        "description": "Press a keyboard shortcut through Joi's approved computer-use harness.",
        "inputSchema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
        },
    },
    {
        "name": "joi_computer_wait",
        "description": "Wait for the UI or webpage to settle, then optionally observe the screen. This is read-only and does not need approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number"},
                "observe_after": {"type": "boolean"},
                "query": {"type": "string"},
                "target": {"type": "string", "enum": ["active_window", "fullscreen"]},
            },
        },
    },
    {
        "name": "joi_computer_open_app",
        "description": "Open a macOS application through Joi's approved computer-use harness and verify the result.",
        "inputSchema": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
    },
    {
        "name": "joi_browser_open_url",
        "description": "Open a URL in the user's desktop browser through Joi's approved computer-use harness.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "browser": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "joi_browser_open_site",
        "description": "Open a known site or section, such as Bilibili popular videos, in the user's desktop browser.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "section": {"type": "string"},
                "query": {"type": "string"},
                "browser": {"type": "string"},
            },
            "required": ["site"],
        },
    },
    {
        "name": "joi_browser_observe",
        "description": "Use Joi's controlled browser observer for page text/elements. This is not the user's Safari window.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "url": {"type": "string"}}},
    },
    {
        "name": "joi_browser_search",
        "description": "Use Joi's controlled browser observer to search and inspect web content.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "url": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "joi_memory_recall",
        "description": "Recall approved local Joi memories relevant to a query.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "number"}}},
    },
    {
        "name": "joi_skills_list",
        "description": "List Joi native skills available through the shell.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "joi_skill_run",
        "description": "Run an exposed Joi native skill by tool name. Prefer the dedicated computer tools for UI actions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["tool"],
        },
    },
    {
        "name": "joi_voice_status",
        "description": "Read Joi voice input/output runtime status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "joi_files_read",
        "description": "Read a file inside Joi's workspace for local context.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "joi_mcp_list",
        "description": "List external MCP tools discovered by Joi.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "joi_minecraft_status",
        "description": "Read Joi's Minecraft adapter and autonomy status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "joi_minecraft_snapshot",
        "description": "Read the sanitized live state of an active Joi Minecraft session (no coordinates).",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "joi_minecraft_plan",
        "description": "Compile a natural-language Minecraft goal into an approval-gated step plan preview.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}, "goal_text": {"type": "string"}},
            "required": ["session_id", "goal_text"],
        },
    },
]


class JoiMcpServer:
    def __init__(self, core_url: str, workspace: Path) -> None:
        self.core_url = core_url
        self.workspace = workspace.resolve()
        self._next_id = 1
        self._last_observation: dict[str, Any] | None = None
        self._tool_router = self._build_tool_router()

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = str(request.get("method") or "")
        request_id = request.get("id")
        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "joi", "version": "0.1.0"},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                result = await self._call_tool(name, arguments)
                return self._result(request_id, {"content": self._content_for_result(result)})
            except Exception as exc:
                return self._error(request_id, -32000, str(exc)[:500])
        return self._error(request_id, -32601, f"unknown method: {method}")

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return (await self._tool_router.dispatch(name, arguments)).result
        except RpcMethodNotFound as exc:
            raise ValueError(f"unknown Joi MCP tool: {exc.method}") from exc

    def _build_tool_router(self) -> JsonRpcRouter:
        router = JsonRpcRouter()
        router.register("joi_watch_start", self._tool_watch_start)
        router.register("joi_watch_status", lambda _: self._core("watch.loop.status", {}))
        router.register("joi_watch_recall", lambda args: self._core_skill("watch.recall", {"query": str(args.get("query") or "")}))
        router.register("joi_screen_observe", self._tool_screen_observe)
        router.register("joi_goal_verify", self._goal_verify)
        router.register("joi_browser_current_state", lambda _: _browser_current_state())
        router.register("joi_target_resolve", self._tool_target_resolve)
        router.register("joi_computer_click_target", self._click_target)
        router.register("joi_computer_click", lambda args: self._computer_coordinates("computer.click", args, ("x", "y", "button")))
        router.register("joi_computer_double_click", lambda args: self._computer_coordinates("computer.double_click", args, ("x", "y", "button")))
        router.register("joi_computer_drag", lambda args: self._computer_coordinates("computer.drag", args, ("x", "y", "end_x", "end_y", "button")))
        router.register("joi_computer_type_text", lambda args: self._run_computer_skill("computer.type_text", {"text": str(args.get("text") or "")}, requested_arguments=args))
        router.register("joi_computer_scroll", lambda args: self._run_computer_skill("computer.scroll", _scroll_args(args), requested_arguments=args))
        router.register("joi_computer_hotkey", lambda args: self._run_computer_skill("computer.hotkey", {"keys": _keys(args.get("keys"))}, requested_arguments=args))
        router.register("joi_computer_wait", self._wait_and_observe)
        router.register("joi_computer_open_app", lambda args: self._run_computer_skill("computer.open_app", {"app_name": str(args.get("app_name") or "")}, requested_arguments=args))
        router.register("joi_browser_open_url", self._tool_browser_open_url)
        router.register("joi_browser_open_site", self._tool_browser_open_site)
        router.register("joi_browser_observe", lambda args: self._core_skill("browser.observe", _browser_skill_args(args)))
        router.register("joi_browser_search", lambda args: self._core_skill("browser.search", _browser_skill_args(args)))
        router.register("joi_memory_recall", lambda args: self._core("memory.recall", {"query": str(args.get("query") or ""), "limit": optional_int(args.get("limit")) or 8}))
        router.register("joi_skills_list", lambda _: self._core("skills.list", {}))
        router.register("joi_skill_run", self._tool_skill_run)
        router.register("joi_voice_status", lambda _: self._core("runtime.status", {}))
        router.register("joi_files_read", lambda args: self._core_skill("files.read", {"path": str(args.get("path") or "")}))
        router.register("joi_mcp_list", lambda args: self._core_skill("mcp.list_tools", {"query": str(args.get("query") or "")}))
        router.register("joi_minecraft_status", lambda _: self._core("game.adapter.minecraft.autonomy.status", {}))
        router.register(
            "joi_minecraft_snapshot",
            lambda args: self._core("game.adapter.minecraft.snapshot", {"session_id": str(args.get("session_id") or "")}),
        )
        router.register(
            "joi_minecraft_plan",
            lambda args: self._core(
                "game.adapter.minecraft.plan.preview",
                {"session_id": str(args.get("session_id") or ""), "goal_text": str(args.get("goal_text") or "")},
            ),
        )
        return router

    async def _tool_watch_start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._core(
            "watch.loop.start",
            {
                "query": str(arguments.get("query") or "陪我看当前画面"),
                "interval_seconds": 6,
                "sample_count": 3,
                "sample_interval_ms": 700,
                "transcript_source": str(arguments.get("transcript_source") or "auto"),
                "transcribe": True,
            },
        )

    async def _tool_screen_observe(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._remember_from_result(await self._core_skill("observe.screen", _screen_observe_args(arguments)))

    async def _tool_target_resolve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._core_skill_wait(
            "vision.resolve_target",
            {
                "query": str(arguments.get("query") or arguments.get("target") or ""),
                "target": str(arguments.get("target") or ""),
            },
            timeout_seconds=180.0,
        )
        return self._remember_from_result(result)

    async def _computer_coordinates(self, tool: str, arguments: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return await self._run_computer_skill(
            tool,
            self._coordinate_args(arguments, keys),
            requested_arguments=arguments,
        )

    async def _tool_browser_open_url(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._run_computer_skill(
            "computer.workflow",
            {
                "workflow": "open_url",
                "url": str(arguments.get("url") or ""),
                "browser": _browser_argument(arguments),
            },
            requested_arguments=arguments,
        )

    async def _tool_browser_open_site(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._run_computer_skill(
            "computer.workflow",
            {
                "workflow": "open_url",
                "url": _site_url(
                    str(arguments.get("site") or ""),
                    str(arguments.get("section") or ""),
                    str(arguments.get("query") or ""),
                ),
                "browser": _browser_argument(arguments),
            },
            requested_arguments=arguments,
        )

    async def _tool_skill_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = str(arguments.get("tool") or "")
        skill_args = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
        if _waits_for_approval(tool):
            result = await self._core_skill_wait(tool, skill_args, timeout_seconds=180.0)
        else:
            result = await self._core_skill(tool, skill_args)
        return self._remember_from_result(result)

    async def _core_skill(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._core("skill.run", {"tool": tool, "arguments": arguments})

    async def _run_computer_skill(self, tool: str, arguments: dict[str, Any], *, requested_arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        before_observation = self._last_observation
        before_guide = _coordinate_guide(before_observation)
        result = self._remember_from_result(await self._core_skill_wait(tool, arguments, timeout_seconds=180.0))
        result["browser_state"] = _browser_current_state()
        result["computer_call"] = _computer_call_summary(
            tool,
            requested_arguments if isinstance(requested_arguments, dict) else arguments,
            arguments,
            result,
            before_guide,
            self._last_observation,
            result["browser_state"],
        )
        return result

    async def _click_target(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or arguments.get("target") or "").strip()
        target = str(arguments.get("target") or query).strip()
        result = self._remember_from_result(
            await self._core_skill_wait(
                "vision.resolve_target",
                {"query": query, "target": target},
                timeout_seconds=180.0,
            )
        )
        result["browser_state"] = _browser_current_state()
        result["semantic_click_call"] = _semantic_click_summary(
            query=query,
            target=target,
            goal=str(arguments.get("goal") or "").strip(),
            result=result,
            latest_observation=self._last_observation,
            browser_state=result["browser_state"],
        )
        return result

    async def _goal_verify(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = str(arguments.get("goal") or "").strip()
        observe_args = _screen_observe_args(
            {
                "query": f"verify UI goal: {goal}",
                "target": str(arguments.get("target") or "active_window"),
                "sample_count": 1,
                "skip_summary": False,
            }
        )
        result = self._remember_from_result(await self._core("skill.run", {"tool": "observe.screen", "arguments": observe_args}))
        result["browser_state"] = _browser_current_state()
        result["goal_verification"] = _goal_verification(
            goal,
            result,
            required_terms=_string_list(arguments.get("required_terms")),
            any_terms=_string_list(arguments.get("any_terms")),
        )
        return result

    async def _wait_and_observe(self, arguments: dict[str, Any]) -> dict[str, Any]:
        seconds = _bounded_float(arguments.get("seconds"), 1.5, 0.1, 15.0)
        await asyncio.sleep(seconds)
        result: dict[str, Any] = {
            "ok": True,
            "wait_call": {
                "seconds": round(seconds, 2),
                "observe_after": bool(arguments.get("observe_after", True)),
            },
        }
        if not bool(arguments.get("observe_after", True)):
            result["browser_state"] = _browser_current_state()
            return result
        observe_args = _screen_observe_args(
            {
                "query": str(arguments.get("query") or "observe after wait"),
                "target": str(arguments.get("target") or "active_window"),
                "sample_count": 1,
                "skip_summary": bool(arguments.get("skip_summary", True)),
            }
        )
        observed = self._remember_from_result(await self._core("skill.run", {"tool": "observe.screen", "arguments": observe_args}))
        observed["wait_call"] = result["wait_call"]
        observed["browser_state"] = _browser_current_state()
        return observed

    async def _core_skill_wait(self, tool: str, arguments: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets dependency is required for Joi MCP") from exc
        request_id = f"mcp-{self._next_id}"
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "skill.run",
            "params": {"tool": tool, "arguments": arguments},
        }
        deadline = time.monotonic() + max(5.0, timeout_seconds)
        async with websockets.connect(self.core_url) as socket:
            await socket.send(json.dumps(payload, ensure_ascii=False))
            initial = await self._wait_for_response(socket, request_id, deadline)
            events = _events(initial)
            task_id = _task_id_from_events(events)
            if not _has_approval(events) or not task_id:
                return initial
            collected = list(events)
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if message.get("method") != "agent.event":
                    continue
                event = message.get("params")
                if not isinstance(event, dict) or str(event.get("task_id") or "") != task_id:
                    continue
                collected.append(event)
                if str(event.get("type") or "") in {"task_completed", "task_failed"}:
                    result = dict(initial)
                    result["events"] = collected
                    result["ok"] = str(event.get("type") or "") == "task_completed"
                    result["completed_after_approval"] = True
                    return result
            result = dict(initial)
            result["ok"] = False
            result["events"] = collected
            result["timeout"] = True
            result["message"] = "Joi waited for the user approval/result, but the action did not finish before timeout."
            return result

    async def _wait_for_response(self, socket: Any, request_id: str, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
            payload = json.loads(raw)
            if payload.get("id") != request_id:
                continue
            if payload.get("error"):
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                raise RuntimeError(str(error.get("message") or "Joi core RPC failed"))
            result = payload.get("result")
            return result if isinstance(result, dict) else {"ok": True, "result": result}
        raise RuntimeError("Joi core did not reply before timeout")

    async def _core(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets dependency is required for Joi MCP") from exc
        request_id = f"mcp-{self._next_id}"
        self._next_id += 1
        async with websockets.connect(self.core_url) as socket:
            await socket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, ensure_ascii=False))
            async for raw in socket:
                payload = json.loads(raw)
                if payload.get("id") != request_id:
                    continue
                if payload.get("error"):
                    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                    raise RuntimeError(str(error.get("message") or "Joi core RPC failed"))
                result = payload.get("result")
                return result if isinstance(result, dict) else {"ok": True, "result": result}
        raise RuntimeError("Joi core closed before replying")

    def _content_for_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        compact = _compact_result(result)
        guide = _coordinate_guide(self._last_observation)
        if guide:
            compact["coordinate_system"] = guide
        content = [{"type": "text", "text": _json_text(compact)}]
        content.extend(_image_content_from_artifacts(self.workspace, result, limit=2))
        return content

    def _remember_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        observation = _latest_observation(result)
        if observation is not None:
            self._last_observation = observation
        return result

    def _coordinate_args(self, arguments: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        result = _coordinate_args(arguments, keys)
        if "x" in result and "y" in result:
            result["x"], result["y"] = self._screen_point(result["x"], result["y"], arguments)
        if "end_x" in result and "end_y" in result:
            result["end_x"], result["end_y"] = self._screen_point(result["end_x"], result["end_y"], arguments)
        return result

    def _screen_point(self, x: int, y: int, arguments: dict[str, Any]) -> tuple[int, int]:
        space = str(arguments.get("coordinate_space") or arguments.get("space") or "screenshot").strip().casefold()
        if space in {"screen", "display", "macos"}:
            return int(x), int(y)
        converted = _screenshot_to_screen_point(self._last_observation, x, y)
        return converted if converted is not None else (int(x), int(y))

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Run Joi MCP server over stdio.")
    parser.add_argument("--core-url", default="ws://127.0.0.1:8765")
    parser.add_argument("--workspace", default=".", help="Accepted for Codex MCP config compatibility.")
    args = parser.parse_args()
    server = JoiMcpServer(args.core_url, Path(args.workspace))
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            response = await server.handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": str(exc)[:500]}}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:20000]


def _screen_observe_args(arguments: dict[str, Any]) -> dict[str, Any]:
    target = str(arguments.get("target") or "active_window")
    return {
        "query": str(arguments.get("query") or "observe current UI"),
        "target": "fullscreen" if target in {"fullscreen", "full_screen", "screen", "desktop"} else "active_window",
        "sample_count": _bounded_int(arguments.get("sample_count"), 1, 1, 4),
        "sample_interval_ms": _bounded_int(arguments.get("sample_interval_ms"), 700, 100, 2500),
        "skip_summary": bool(arguments.get("skip_summary", False)),
    }


def _coordinate_args(arguments: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key == "button":
            result[key] = str(arguments.get(key) or "left")
        elif key in arguments:
            result[key] = _number(arguments.get(key))
    return result


def _scroll_args(arguments: dict[str, Any]) -> dict[str, Any]:
    if "delta" in arguments:
        return {"delta": _number(arguments.get("delta"))}
    return {
        "direction": str(arguments.get("direction") or "down"),
        "amount": _bounded_int(arguments.get("amount"), 3, 1, 12),
    }


def _keys(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    for separator in ("+", ",", "，", " "):
        if separator in text:
            return [part.strip() for part in text.split(separator) if part.strip()]
    return [text]


def _site_url(site: str, section: str = "", query: str = "") -> str:
    normalized_site = site.strip().casefold()
    normalized_section = section.strip().casefold()
    normalized_query = query.strip().casefold()
    query_text = query.strip()
    if normalized_site in {"bilibili", "bili", "b站", "哔哩哔哩", "bilbil"}:
        if any(token in normalized_section or token in normalized_query for token in ("popular", "hot", "rank", "热门", "排行")):
            return "https://www.bilibili.com/v/popular/all"
        if query_text:
            from urllib.parse import quote_plus

            return "https://search.bilibili.com/all?keyword=" + quote_plus(query_text)
        return "https://www.bilibili.com"
    if query_text:
        from urllib.parse import quote_plus

        return "https://www.baidu.com/s?wd=" + quote_plus(query_text)
    return normalized_site if "://" in normalized_site else "https://" + normalized_site


def _browser_argument(arguments: dict[str, Any]) -> str:
    requested = str(arguments.get("browser") or "").strip()
    if requested and requested.casefold() not in {"default", "默认", "auto", "current", "当前"}:
        return requested
    return _preferred_browser_from_state(_browser_current_state()) or "default"


def _browser_skill_args(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": str(arguments.get("query") or ""),
        "url": str(arguments.get("url") or ""),
    }


def _preferred_browser_from_state(state: dict[str, Any]) -> str:
    rows = state.get("browser_tabs")
    if not isinstance(rows, list) or not rows:
        rows = state.get("tabs")
    if not isinstance(rows, list):
        return ""
    browsers = [row for row in rows if isinstance(row, dict) and str(row.get("browser") or "").strip()]
    if not browsers:
        return ""
    for row in browsers:
        if bool(row.get("frontmost")):
            return str(row.get("browser") or "").strip()
    for row in browsers:
        if str(row.get("url") or "").strip():
            return str(row.get("browser") or "").strip()
    return str(browsers[0].get("browser") or "").strip()


def _events(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("events")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _has_approval(events: list[dict[str, Any]]) -> bool:
    return any(str(event.get("type") or "") == "approval_required" for event in events)


def _waits_for_approval(tool: str) -> bool:
    name = str(tool or "").strip()
    return name.startswith("computer.") or name in {"vision.resolve_target", "vision.select_target"}


def _task_id_from_events(events: list[dict[str, Any]]) -> str:
    for event in events:
        task_id = str(event.get("task_id") or "").strip()
        if task_id:
            return task_id
    return ""


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in result.items() if key != "events"}
    events = _events(result)
    if events:
        compact["events"] = [_compact_event(event) for event in events[-6:]]
        compact["event_count"] = len(events)
    return compact


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    card = event.get("display_card") if isinstance(event.get("display_card"), dict) else {}
    state = event.get("agent_state") if isinstance(event.get("agent_state"), dict) else {}
    return {
        "type": event.get("type"),
        "task_id": event.get("task_id"),
        "title": card.get("title"),
        "summary": card.get("summary"),
        "body": str(card.get("body") or "")[:900],
        "status": card.get("status"),
        "artifacts": card.get("artifacts") if isinstance(card.get("artifacts"), list) else [],
        "agent_state": _compact_agent_state(state),
    }


def _compact_agent_state(state: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for key in (
        "tool",
        "runtime_event",
        "approval",
        "computer_use",
        "post_action_verification",
        "observation",
        "vision_summary",
        "model_status",
        "artifacts",
    ):
        if key in state:
            keep[key] = state[key]
    return keep


def _computer_call_summary(
    tool: str,
    requested_arguments: dict[str, Any],
    executed_arguments: dict[str, Any],
    result: dict[str, Any],
    before_guide: dict[str, Any] | None,
    latest_observation: dict[str, Any] | None,
    browser_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = _events(result)
    final_event = _final_event(events)
    final_card = final_event.get("display_card") if isinstance(final_event.get("display_card"), dict) else {}
    return {
        "tool": tool,
        "status": _computer_call_status(result, final_event),
        "approval": _approval_summary(events),
        "requested_arguments": _safe_action_arguments(requested_arguments),
        "executed_arguments": _safe_action_arguments(executed_arguments),
        "coordinate_mapping": _coordinate_mapping(requested_arguments, executed_arguments, before_guide),
        "final_event": {
            "type": final_event.get("type") or "",
            "status": final_card.get("status") or "",
            "summary": final_card.get("summary") or "",
        },
        "post_action_verification": _latest_value(events, "post_action_verification"),
        "latest_observation": _observation_brief(latest_observation),
        "continuation_context": _continuation_context(
            result,
            latest_observation,
            browser_state,
            goal=str(requested_arguments.get("goal") or requested_arguments.get("user_goal") or ""),
        ),
        "completion_guard": "Action success only means the UI action ran; it does not prove the user's goal is complete.",
        "next_step": "Check browser_state and call joi_goal_verify for the user's goal; do not report completion from approval or screen-change alone.",
    }


def _semantic_click_summary(
    *,
    query: str,
    target: str,
    goal: str,
    result: dict[str, Any],
    latest_observation: dict[str, Any] | None,
    browser_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = _events(result)
    final_event = _final_event(events)
    final_card = final_event.get("display_card") if isinstance(final_event.get("display_card"), dict) else {}
    target_candidate = _latest_value(events, "target_candidate")
    target_candidates = _latest_value(events, "target_candidates")
    return {
        "tool": "vision.resolve_target",
        "query": query,
        "target": target,
        "goal": goal,
        "status": _computer_call_status(result, final_event),
        "approval": _approval_summary(events),
        "target_candidate": target_candidate if isinstance(target_candidate, dict) else None,
        "target_candidates": target_candidates if isinstance(target_candidates, list) else [],
        "final_event": {
            "type": final_event.get("type") or "",
            "status": final_card.get("status") or "",
            "summary": final_card.get("summary") or "",
        },
        "post_action_verification": _latest_value(events, "post_action_verification"),
        "latest_observation": _observation_brief(latest_observation),
        "continuation_context": _continuation_context(
            result,
            latest_observation,
            browser_state,
            goal=goal,
        ),
        "completion_guard": "A semantic click only means the requested target was acted on or attempted; it does not prove the user's larger goal is complete.",
        "next_step": "Call joi_goal_verify with the user's full goal before saying it is done. If status is not met, inspect browser_state/screen and continue.",
    }


def _continuation_context(
    result: dict[str, Any],
    latest_observation: dict[str, Any] | None,
    browser_state: dict[str, Any] | None,
    *,
    goal: str = "",
) -> dict[str, Any]:
    events = _events(result)
    final_event = _final_event(events)
    status = _computer_call_status(result, final_event)
    verification = _latest_value(events, "post_action_verification")
    verification_state = verification if isinstance(verification, dict) else {}
    goal_text = str(goal or "").strip()
    observation = _observation_brief(latest_observation)
    if status in {"failed", "timeout"}:
        next_tool = "joi_screen_observe"
        suggested_arguments: dict[str, Any] = {
            "query": "inspect current UI after failed action",
            "target": "active_window",
            "sample_count": 1,
        }
        instruction = "The action did not complete cleanly. Observe the current UI before retrying or reporting failure."
    elif status == "completed" and goal_text:
        next_tool = "joi_goal_verify"
        suggested_arguments = {"goal": goal_text, "target": "active_window"}
        instruction = "Verify the full user goal before reporting completion."
    elif status == "completed":
        next_tool = "joi_screen_observe"
        suggested_arguments = {
            "query": "observe current UI after action",
            "target": "active_window",
            "sample_count": 1,
        }
        instruction = "Inspect the current UI and infer the next step; do not report completion from action success alone."
    elif status == "waiting_for_or_after_approval":
        next_tool = "wait_for_user_approval"
        suggested_arguments = {}
        instruction = "Wait for Joi to return the approved action result before continuing."
    else:
        next_tool = "joi_screen_observe"
        suggested_arguments = {
            "query": "observe current UI before next action",
            "target": "active_window",
            "sample_count": 1,
        }
        instruction = "Observe the UI before choosing the next action."
    return {
        "status": status,
        "verification_status": str(verification_state.get("status") or ""),
        "has_after_observation": observation is not None,
        "latest_observation": observation,
        "browser_focus": _browser_focus_brief(browser_state),
        "next_tool": next_tool,
        "suggested_arguments": suggested_arguments,
        "requires_goal_verification": status == "completed" and bool(goal_text),
        "instruction": instruction,
    }


def _browser_focus_brief(browser_state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(browser_state, dict):
        return {}
    rows = browser_state.get("browser_tabs")
    if not isinstance(rows, list) or not rows:
        rows = browser_state.get("tabs")
    tabs = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    front_tab = next((row for row in tabs if bool(row.get("frontmost"))), None)
    if front_tab is None and tabs:
        front_tab = tabs[0]
    return {
        "frontmost_app": str(browser_state.get("frontmost_app") or ""),
        "browser_count": _number(browser_state.get("browser_count")) if "browser_count" in browser_state else len(tabs),
        "frontmost_browser": str((front_tab or {}).get("browser") or ""),
        "frontmost_title": str((front_tab or {}).get("title") or ""),
        "frontmost_url": str((front_tab or {}).get("url") or ""),
    }


def _goal_verification(goal: str, result: dict[str, Any], *, required_terms: list[str], any_terms: list[str]) -> dict[str, Any]:
    requirements = _goal_terms(goal, required_terms, any_terms)
    evidence_text = _visible_text(result)
    normalized = _normalize_text(evidence_text)
    required_hits = [term for term in requirements["required"] if _term_matches(term, normalized)]
    any_hits = [term for term in requirements["any"] if _term_matches(term, normalized)]
    if not requirements["required"] and not requirements["any"]:
        return {
            "goal": goal,
            "status": "uncertain",
            "confidence": "low",
            "required_terms": [],
            "required_hits": [],
            "required_missing": [],
            "any_terms": [],
            "any_hits": [],
            "evidence_excerpt": " ".join(evidence_text.split())[:1200],
            "latest_observation": _observation_brief(_latest_observation(result)),
            "instruction": "The goal did not produce stable verification terms. Ask for or infer the missing context, then observe again before reporting completion.",
        }
    required_ok = len(required_hits) == len(requirements["required"])
    any_ok = not requirements["any"] or bool(any_hits)
    if required_ok and any_ok:
        status = "met"
        confidence = "medium"
    elif evidence_text.strip():
        status = "not_met"
        confidence = "medium" if requirements["required"] or requirements["any"] else "low"
    else:
        status = "uncertain"
        confidence = "low"
    return {
        "goal": goal,
        "status": status,
        "confidence": confidence,
        "required_terms": requirements["required"],
        "required_hits": required_hits,
        "required_missing": [term for term in requirements["required"] if term not in required_hits],
        "any_terms": requirements["any"],
        "any_hits": any_hits,
        "evidence_excerpt": " ".join(evidence_text.split())[:1200],
        "latest_observation": _observation_brief(_latest_observation(result)),
        "instruction": "Only report the user goal as complete when status is 'met'. Otherwise keep observing or take another approved action.",
    }


def _browser_current_state() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"ok": False, "status": "unsupported", "platform": sys.platform, "tabs": []}
    script = r'''
set colDelim to "	"
set browserRows to {}
set frontApp to ""
set frontTitle to ""
set frontUrl to ""
set browserNames to {"Safari", "Google Chrome", "Microsoft Edge", "Brave Browser", "Arc", "Firefox", "Chromium", "Opera", "Vivaldi"}

tell application "System Events"
    set runningApps to name of every application process whose background only is false
    try
        set frontProc to first application process whose frontmost is true
        set frontApp to name of frontProc
        if (count of windows of frontProc) > 0 then
            set frontWin to window 1 of frontProc
            try
                set attrValue to value of attribute "AXTitle" of frontWin
                if attrValue is not missing value then set frontTitle to attrValue as text
            end try
            try
                set attrValue to value of attribute "AXDocument" of frontWin
                if attrValue is not missing value then set frontUrl to attrValue as text
            end try
        end if
    end try

    repeat with browserName in browserNames
        set appName to browserName as text
        set winTitle to ""
        set docUrl to ""
        try
            if runningApps contains appName then
                set browserProc to application process appName
                if (count of windows of browserProc) > 0 then
                    set browserWin to window 1 of browserProc
                    try
                        set attrValue to value of attribute "AXTitle" of browserWin
                        if attrValue is not missing value then set winTitle to attrValue as text
                    end try
                    try
                        set attrValue to value of attribute "AXDocument" of browserWin
                        if attrValue is not missing value then set docUrl to attrValue as text
                    end try
                end if
                if winTitle is not "" or docUrl is not "" then
                    set frontFlag to "false"
                    if appName is frontApp then set frontFlag to "true"
                    set end of browserRows to appName & colDelim & winTitle & colDelim & docUrl & colDelim & frontFlag & colDelim & "browser_accessibility"
                end if
            end if
        end try
    end repeat

    set hasFrontmostRow to false
    repeat with rowText in browserRows
        if (rowText as text) starts with (frontApp & colDelim) then set hasFrontmostRow to true
    end repeat
    if frontApp is not "" and hasFrontmostRow is false then
        set end of browserRows to frontApp & colDelim & frontTitle & colDelim & frontUrl & colDelim & "true" & colDelim & "frontmost_accessibility"
    end if
end tell

return frontApp & linefeed & my joinRows(browserRows, linefeed)

on joinRows(rowList, separator)
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to separator
    set joinedText to rowList as text
    set AppleScript's text item delimiters to oldDelimiters
    return joinedText
end joinRows
'''
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5.0)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": type(exc).__name__, "tabs": []}
    if proc.returncode != 0:
        return {
            "ok": False,
            "status": "failed",
            "error": (proc.stderr or proc.stdout or "osascript_failed")[:800],
            "tabs": [],
        }
    lines = (proc.stdout or "").splitlines()
    frontmost = lines[0].strip() if lines else ""
    tabs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for line in lines[1:]:
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        app, title, url, front_flag, source = (part.strip() for part in parts)
        if app or title or url:
            key = (app, title, url)
            if key in seen:
                continue
            seen.add(key)
            tabs.append(
                {
                    "browser": app,
                    "title": title,
                    "url": url,
                    "frontmost": front_flag.casefold() == "true" or app == frontmost,
                    "source": source,
                }
            )
    browser_tabs = [tab for tab in tabs if str(tab.get("source") or "").startswith("browser_")]
    return {
        "ok": True,
        "status": "ok",
        "frontmost_app": frontmost,
        "tabs": tabs,
        "browser_tabs": browser_tabs,
        "browser_count": len(browser_tabs),
    }


def _goal_terms(goal: str, required_terms: list[str], any_terms: list[str]) -> dict[str, list[str]]:
    required = [term for term in required_terms if term.strip()]
    any_group = [term for term in any_terms if term.strip()]
    lowered = goal.casefold()
    if not required:
        required.extend(_known_app_goal_terms(lowered))
    if not required and any(token in lowered for token in ("b站", "bilibili", "哔哩哔哩")):
        required.append("bilibili")
    if not any_group and any(token in lowered for token in ("热门", "popular", "排行")):
        any_group.extend(["热门", "popular", "排行榜", "综合热门", "全站热门"])
    if not required and not any_group:
        for token in _simple_goal_tokens(goal):
            if token not in required:
                required.append(token)
            if len(required) >= 3:
                break
    return {"required": required[:8], "any": any_group[:8]}


def _simple_goal_tokens(goal: str) -> list[str]:
    lowered = goal.casefold()
    for phrase in (
        "帮我", "帮忙", "请", "给我", "打开", "开启", "启动", "运行", "点击", "点一下", "点",
        "搜索", "查找", "找", "视频", "网页", "页面", "当前", "一下", "是否", "已经", "了吗", "吗",
    ):
        lowered = lowered.replace(phrase, " ")
    cleaned = "".join(char if char.isalnum() or "\u4e00" <= char <= "\u9fff" else " " for char in lowered)
    stop = {"the", "and", "for", "with", "please", "open", "launch", "start", "click", "search", "page", "video", "current"}
    tokens = [part for part in cleaned.split() if len(part) >= 3 and part not in stop]
    if tokens:
        return tokens
    compact = "".join(cleaned.split())
    return [compact] if compact and compact not in stop else []


def _known_app_goal_terms(lowered_goal: str) -> list[str]:
    rows = [
        ("safari", ("safari", "safari浏览器")),
        ("microsoft edge", ("microsoft edge", "edge", "edge浏览器")),
        ("google chrome", ("google chrome", "chrome", "谷歌浏览器", "chrome浏览器")),
        ("firefox", ("firefox", "火狐")),
        ("finder", ("finder", "访达")),
    ]
    terms: list[str] = []
    normalized_goal = _normalize_text(lowered_goal)
    for canonical, aliases in rows:
        if any(_normalize_text(alias) in normalized_goal for alias in aliases):
            terms.append(canonical)
    if not terms and any(token in normalized_goal for token in ("浏览器", "browser")):
        terms.append("browser")
    return terms


def _visible_text(value: object) -> str:
    texts: list[str] = []
    _collect_visible_text(value, texts)
    return "\n".join(texts)


def _collect_visible_text(value: object, texts: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "summary",
                "body",
                "title",
                "text",
                "label",
                "label_name",
                "vision_summary",
                "query",
                "url",
                "browser",
                "frontmost_app",
                "app",
                "app_name",
                "process",
                "process_name",
                "window_title",
            } and isinstance(item, str):
                texts.append(item)
            elif key_text in {"text_blocks", "text_snippets"} and isinstance(item, list):
                for row in item:
                    if isinstance(row, str):
                        texts.append(row)
                    elif isinstance(row, dict) and isinstance(row.get("text"), str):
                        texts.append(str(row.get("text")))
            elif isinstance(item, (dict, list)):
                _collect_visible_text(item, texts)
    elif isinstance(value, list):
        for item in value:
            _collect_visible_text(item, texts)


def _normalize_text(text: str) -> str:
    return "".join(str(text or "").casefold().split())


def _term_matches(term: str, normalized_evidence: str) -> bool:
    return any(_normalize_text(candidate) in normalized_evidence for candidate in _term_alternatives(term))


def _term_alternatives(term: str) -> list[str]:
    normalized = _normalize_text(term)
    groups = [
        {"bilibili", "bili", "b站", "哔哩哔哩", "bilibili.com", "www.bilibili.com"},
        {"热门", "popular", "排行榜", "排行", "综合热门", "全站热门", "/v/popular", "v/popular"},
        {"safari", "safari.app", "safari浏览器"},
        {"microsoft edge", "edge", "edge浏览器"},
        {"google chrome", "chrome", "谷歌浏览器", "chrome浏览器"},
        {"firefox", "火狐"},
        {"finder", "访达"},
        {"browser", "浏览器", "safari", "microsoft edge", "edge", "google chrome", "chrome", "firefox", "arc", "brave browser", "chromium", "opera", "vivaldi"},
    ]
    for group in groups:
        if normalized in {_normalize_text(item) for item in group}:
            return sorted(group)
    return [term]


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _computer_call_status(result: dict[str, Any], final_event: dict[str, Any]) -> str:
    if result.get("timeout"):
        return "timeout"
    event_type = str(final_event.get("type") or "")
    if event_type == "task_completed" and bool(result.get("ok")):
        return "completed"
    if event_type == "task_failed" or result.get("ok") is False:
        return "failed"
    if _has_approval(_events(result)):
        return "waiting_for_or_after_approval"
    return "submitted"


def _approval_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    approval_event = next((event for event in events if str(event.get("type") or "") == "approval_required"), None)
    final_event = _final_event(events)
    return {
        "required": approval_event is not None,
        "resolved": str(final_event.get("type") or "") in {"task_completed", "task_failed"},
        "final_event": str(final_event.get("type") or ""),
    }


def _final_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if str(event.get("type") or "") in {"task_completed", "task_failed", "tool_completed", "tool_failed"}:
            return event
    return events[-1] if events else {}


def _safe_action_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "text":
            safe["text_length"] = len(str(value or ""))
        elif key in {"url", "browser", "workflow", "site", "section", "query", "app_name", "direction", "amount", "delta", "coordinate_space", "space", "button", "x", "y", "end_x", "end_y"}:
            safe[key] = value
        elif key == "keys":
            safe[key] = [str(item) for item in value] if isinstance(value, list) else str(value)
    return safe


def _coordinate_mapping(requested: dict[str, Any], executed: dict[str, Any], before_guide: dict[str, Any] | None) -> dict[str, Any] | None:
    if "x" not in requested or "y" not in requested or "x" not in executed or "y" not in executed:
        return None
    space = str(requested.get("coordinate_space") or requested.get("space") or "screenshot")
    mapping: dict[str, Any] = {
        "input_space": space,
        "input_point": {"x": _number(requested.get("x")), "y": _number(requested.get("y"))},
        "screen_point": {"x": _number(executed.get("x")), "y": _number(executed.get("y"))},
    }
    if "end_x" in requested and "end_y" in requested and "end_x" in executed and "end_y" in executed:
        mapping["input_end_point"] = {"x": _number(requested.get("end_x")), "y": _number(requested.get("end_y"))}
        mapping["screen_end_point"] = {"x": _number(executed.get("end_x")), "y": _number(executed.get("end_y"))}
    if before_guide:
        mapping["used_coordinate_system"] = before_guide
    return mapping


def _latest_value(events: list[dict[str, Any]], key: str) -> object:
    for event in reversed(events):
        state = event.get("agent_state") if isinstance(event.get("agent_state"), dict) else {}
        if key in state:
            return state[key]
        computer_use = state.get("computer_use") if isinstance(state.get("computer_use"), dict) else {}
        if key in computer_use:
            return computer_use[key]
    return None


def _observation_brief(observation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not _is_observation_state(observation):
        return None
    assert observation is not None
    return {
        "title": observation.get("title") or "",
        "target": observation.get("target") or "",
        "screenshot_rel": observation.get("screenshot_rel") or "",
        "width": _number(observation.get("width")),
        "height": _number(observation.get("height")),
        "capture_rect": observation.get("capture_rect"),
    }


def _latest_observation(value: object) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    if isinstance(value, dict):
        for key in ("observation", "computer_observation", "first_computer_observation"):
            observation = value.get(key)
            if _is_observation_state(observation):
                latest = observation
        computer_use = value.get("computer_use")
        if isinstance(computer_use, dict):
            nested = _latest_observation(computer_use)
            if nested is not None:
                latest = nested
        agent_state = value.get("agent_state")
        if isinstance(agent_state, dict):
            nested = _latest_observation(agent_state)
            if nested is not None:
                latest = nested
        events = value.get("events")
        if isinstance(events, list):
            for event in events:
                nested = _latest_observation(event)
                if nested is not None:
                    latest = nested
        watch_frames = value.get("watch_frames")
        if isinstance(watch_frames, list):
            for frame in watch_frames:
                nested = _latest_observation(frame)
                if nested is not None:
                    latest = nested
        return latest
    if isinstance(value, list):
        for item in value:
            nested = _latest_observation(item)
            if nested is not None:
                latest = nested
    return latest


def _is_observation_state(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("capture_rect"), dict) and _number(value.get("width")) > 0 and _number(value.get("height")) > 0


def _coordinate_guide(observation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not _is_observation_state(observation):
        return None
    assert observation is not None
    rect = observation.get("capture_rect") if isinstance(observation.get("capture_rect"), dict) else {}
    return {
        "default_for_joi_computer_click": "screenshot",
        "screenshot_pixels": {"width": _number(observation.get("width")), "height": _number(observation.get("height"))},
        "screen_rect_points": {
            "x": _number(rect.get("screen_x")),
            "y": _number(rect.get("screen_y")),
            "width": _number(rect.get("width")),
            "height": _number(rect.get("height")),
        },
        "conversion": "For x,y from the attached screenshot, call joi_computer_click with those screenshot pixel coordinates. Joi converts them to macOS screen points.",
    }


def _screenshot_to_screen_point(observation: dict[str, Any] | None, x: int, y: int) -> tuple[int, int] | None:
    if not _is_observation_state(observation):
        return None
    assert observation is not None
    rect = observation.get("capture_rect") if isinstance(observation.get("capture_rect"), dict) else {}
    scale_x = _positive_float(rect.get("scale_x"))
    scale_y = _positive_float(rect.get("scale_y"))
    if scale_x is None or scale_y is None:
        width = _positive_float(observation.get("width"))
        rect_width = _positive_float(rect.get("width"))
        height = _positive_float(observation.get("height"))
        rect_height = _positive_float(rect.get("height"))
        if width and rect_width:
            scale_x = width / rect_width
        if height and rect_height:
            scale_y = height / rect_height
    if not scale_x or not scale_y:
        return None
    screen_x = _number(rect.get("screen_x")) + round(int(x) / scale_x)
    screen_y = _number(rect.get("screen_y")) + round(int(y) / scale_y)
    return int(screen_x), int(screen_y)


def _image_content_from_artifacts(workspace: Path, result: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for artifact in _artifact_paths(result):
        path = _resolve_artifact(workspace, artifact)
        if path is None or path in seen or not path.is_file() or path.stat().st_size > 6_000_000:
            continue
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        if not mime.startswith("image/"):
            continue
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        content.append({"type": "image", "data": encoded, "mimeType": mime})
        seen.add(path)
        if len(content) >= limit:
            break
    return content


def _artifact_paths(value: object) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        artifacts = value.get("artifacts")
        if isinstance(artifacts, list):
            paths.extend(str(item) for item in artifacts if str(item).strip())
        card = value.get("display_card")
        if isinstance(card, dict):
            paths.extend(_artifact_paths(card))
        for item in value.values():
            if isinstance(item, (dict, list)):
                paths.extend(_artifact_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(_artifact_paths(item))
    return paths


def _resolve_artifact(workspace: Path, artifact: str) -> Path | None:
    text = artifact.strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = workspace / path
    try:
        return path.resolve()
    except OSError:
        return None


def _number(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _positive_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
