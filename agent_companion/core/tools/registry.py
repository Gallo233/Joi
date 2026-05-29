"""Tool Registry with plugin discovery support.

Extends the base registry with:
- Plugin auto-discovery from configured directories
- Runtime tool listing with metadata
- MCP tool adapter support (Phase A2)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolAdapter] = {}
        self._plugin_sources: dict[str, str] = {}  # tool_name -> plugin_name

    def register(self, tool: ToolAdapter, plugin_name: str = "") -> None:
        self._tools[tool.name] = tool
        if plugin_name:
            self._plugin_sources[tool.name] = plugin_name

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
        """Return detailed tool info including plugin source."""
        result = []
        for name, tool in sorted(self._tools.items()):
            entry: dict[str, Any] = {
                "name": name,
                "adapter": type(tool).__name__,
            }
            source = self._plugin_sources.get(name)
            if source:
                entry["plugin"] = source
            result.append(entry)
        return result

    def load_plugins(self, workspace: Path | None = None) -> int:
        """Discover and register plugins from standard directories.

        Returns the number of tools registered from plugins.
        """
        from agent_companion.core.plugin_protocol import discover_plugins

        plugin_dirs = []
        if workspace:
            plugin_dirs.append(workspace / "agent_companion" / "plugins")
        plugin_dirs.append(Path(__file__).parent.parent / "plugins")

        plugins = discover_plugins(plugin_dirs=plugin_dirs, workspace=workspace)
        count = 0
        for plugin in plugins:
            if plugin.requires and not self._check_requirements(plugin.requires):
                logger.warning("Plugin %s missing requirements: %s", plugin.name, plugin.requires)
                continue
            kwargs = {}
            if workspace:
                kwargs["workspace"] = workspace
            for tool in plugin.instantiate_tools(**kwargs):
                self.register(tool, plugin_name=plugin.name)
                count += 1
                logger.info("Loaded plugin tool: %s (from %s)", tool.name, plugin.name)
        return count

    @staticmethod
    def _check_requirements(requires: list[str]) -> bool:
        """Check if all required packages are importable."""
        import importlib
        for pkg in requires:
            try:
                importlib.import_module(pkg)
            except ImportError:
                return False
        return True
