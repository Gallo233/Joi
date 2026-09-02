"""MCP (Model Context Protocol) Tool Adapter.

Wraps external MCP server tools as Joi ToolAdapters, allowing Joi
to use any MCP-compatible tool without modification.

Usage:
    adapter = McpToolAdapter(
        server_url="stdio:///path/to/mcp-server",
        tool_name="read_file",
        mcp_tool_name="read_file",
    )
    registry.register(adapter)
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line

logger = logging.getLogger(__name__)


class McpToolAdapter(ToolAdapter):
    """Adapts a single MCP tool as a Joi ToolAdapter.

    Communication with the MCP server happens via stdio (subprocess)
    or HTTP (for remote servers).
    """

    def __init__(
        self,
        name: str,
        server_command: list[str] | None = None,
        server_url: str = "",
        mcp_tool_name: str = "",
        description: str = "",
        workspace: Path | None = None,
    ) -> None:
        self.name = name
        self._server_command = server_command or []
        self._server_url = server_url
        self._mcp_tool_name = mcp_tool_name or name
        self._description = description
        self.workspace = workspace
        self._session_id: str | None = None

    def run(self, request: ToolRequest) -> ToolResult:
        if self._server_command:
            return self._run_stdio(request)
        elif self._server_url:
            return self._run_http(request)
        else:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "no_server_configured"},
                display_card=DisplayCard("MCP 工具", "未配置 MCP 服务器。", status="failed"),
                voice_line=safe_voice_line("这个 MCP 工具还没配置好。", sprite="4"),
            )

    def _run_stdio(self, request: ToolRequest) -> ToolResult:
        """Run MCP tool via stdio subprocess."""
        try:
            # Build MCP JSON-RPC request
            mcp_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": self._mcp_tool_name,
                    "arguments": request.arguments,
                },
            }

            proc = subprocess.run(
                self._server_command,
                input=json.dumps(mcp_request) + "\n",
                capture_output=True,
                text=True,
                timeout=30.0,
                cwd=str(self.workspace) if self.workspace else None,
            )

            if proc.returncode != 0:
                error_msg = proc.stderr.strip()[:500] if proc.stderr else "exit code " + str(proc.returncode)
                return ToolResult(
                    ok=False,
                    agent_state={"tool": self.name, "error": "mcp_process_error", "detail": error_msg},
                    display_card=DisplayCard("MCP 工具", "MCP 服务器执行失败。", error_msg, status="failed"),
                    voice_line=safe_voice_line("MCP 工具执行失败了。", sprite="4"),
                )

            # Parse response
            response = self._parse_mcp_response(proc.stdout)
            return self._to_tool_result(response, request)

        except subprocess.TimeoutExpired:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "timeout"},
                display_card=DisplayCard("MCP 工具", "MCP 服务器响应超时。", status="failed"),
                voice_line=safe_voice_line("MCP 工具超时了。", sprite="4"),
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": str(exc)},
                display_card=DisplayCard("MCP 工具", "MCP 工具执行异常。", str(exc)[:500], status="failed"),
                voice_line=safe_voice_line("MCP 工具出了问题。", sprite="4"),
            )

    def _run_http(self, request: ToolRequest) -> ToolResult:
        """Run MCP tool via HTTP (SSE/Streamable HTTP transport)."""
        import urllib.request
        try:
            mcp_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": self._mcp_tool_name,
                    "arguments": request.arguments,
                },
            }
            data = json.dumps(mcp_request).encode("utf-8")
            req = urllib.request.Request(
                self._server_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                response = json.loads(resp.read().decode("utf-8"))
            return self._to_tool_result(response, request)
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": str(exc)},
                display_card=DisplayCard("MCP 工具", "MCP HTTP 请求失败。", str(exc)[:500], status="failed"),
                voice_line=safe_voice_line("MCP 工具连接失败了。", sprite="4"),
            )

    def _to_tool_result(self, mcp_response: dict[str, Any], request: ToolRequest) -> ToolResult:
        """Convert MCP response to Joi ToolResult."""
        error = mcp_response.get("error")
        if error:
            error_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "mcp_error": error_msg},
                display_card=DisplayCard("MCP 工具", f"MCP 返回错误: {error_msg[:200]}", status="failed"),
                voice_line=safe_voice_line("MCP 工具返回了错误。", sprite="4"),
            )

        result = mcp_response.get("result", {})
        content = result.get("content", [])

        # Extract text content from MCP response
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    text_parts.append("[图片]")
            elif isinstance(item, str):
                text_parts.append(item)

        output_text = "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)[:2000]

        return ToolResult(
            ok=True,
            agent_state={"tool": self.name, "mcp_result": result},
            display_card=DisplayCard(
                "MCP 工具",
                f"MCP {self._mcp_tool_name} 执行完成",
                output_text[:2000],
                status="success",
            ),
            voice_line=safe_voice_line("MCP 工具执行完成了。", sprite="5"),
        )

    @staticmethod
    def _parse_mcp_response(stdout: str) -> dict[str, Any]:
        """Parse MCP JSON-RPC response from stdout."""
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return {"error": {"message": "No valid JSON-RPC response"}}


def discover_mcp_tools(config: dict[str, Any]) -> list[McpToolAdapter]:
    """Discover MCP tools from a configuration dict.

    Config format:
        {
            "mcpServers": {
                "server-name": {
                    "command": ["python", "server.py"],
                    "args": [],
                    "tools": ["tool1", "tool2"]
                }
            }
        }
    """
    adapters = []
    servers = config.get("mcpServers", {})
    for server_name, server_config in servers.items():
        command = server_config.get("command", [])
        args = server_config.get("args", [])
        full_command = command + args
        tools = server_config.get("tools", [])

        for tool_name in tools:
            mcp_name = f"{server_name}.{tool_name}"
            adapters.append(McpToolAdapter(
                name=f"mcp.{mcp_name}",
                server_command=full_command,
                mcp_tool_name=tool_name,
                description=f"MCP tool {tool_name} from {server_name}",
            ))

    return adapters
