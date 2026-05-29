"""Example plugin: System Info tool.

Demonstrates the ToolPlugin protocol. Provides a tool that reports
basic system information (OS, hostname, uptime).
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from agent_companion.core.plugin_protocol import ToolCapability, ToolPlugin
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class SystemInfoTool(ToolAdapter):
    name = "system.info"

    def run(self, request: ToolRequest) -> ToolResult:
        try:
            info = {
                "os": platform.system(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "hostname": platform.node(),
                "python": platform.python_version(),
            }
            # Get uptime on macOS
            try:
                proc = subprocess.run(
                    ["sysctl", "-n", "kern.boottime"],
                    capture_output=True, text=True, timeout=2.0,
                )
                if proc.returncode == 0:
                    info["boot_time"] = proc.stdout.strip()
            except Exception:
                pass

            body = "\n".join(f"{k}: {v}" for k, v in info.items())
            return ToolResult(
                ok=True,
                agent_state={"tool": self.name, "system_info": info},
                display_card=DisplayCard("系统信息", f"{info['os']} {info['machine']}", body, status="success"),
                voice_line=safe_voice_line("系统信息已经放在卡片里了。", sprite="5"),
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": str(exc)},
                display_card=DisplayCard("系统信息", "获取失败", str(exc), status="failed"),
                voice_line=safe_voice_line("获取系统信息失败了。", sprite="4"),
            )


plugin = ToolPlugin(
    name="system-info",
    version="1.0.0",
    description="报告基本系统信息",
    tools=[SystemInfoTool],
    capabilities=[
        ToolCapability(
            name="system.info",
            description="获取当前系统信息 (OS, 机器类型, 主机名)",
            risk_level="low",
        )
    ],
    author="Joi",
)
