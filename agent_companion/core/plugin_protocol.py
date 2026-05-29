"""Plugin Protocol for Joi Tool System.

Defines the ToolPlugin protocol that external tools must implement
to be auto-discovered and registered by Joi's agent core.

Inspired by AIRI's plugin-protocol but adapted for Joi's Python architecture.

Usage:
    1. Create a Python file in agent_companion/plugins/ (or any configured directory)
    2. Implement the ToolPlugin protocol
    3. Export a `plugin` variable of type ToolPlugin
    4. Joi auto-discovers and registers it on startup

Example:
    # agent_companion/plugins/my_tool.py
    from agent_companion.core.tools.base import ToolAdapter
    from agent_companion.core.plugin_protocol import ToolPlugin

    class MyTool(ToolAdapter):
        name = "my.custom_tool"
        def run(self, request):
            ...

    plugin = ToolPlugin(
        name="my-tool",
        version="1.0.0",
        description="My custom tool",
        tools=[MyTool],
    )
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_companion.core.tools.base import ToolAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCapability:
    """Describes a single tool's capabilities for discovery."""
    name: str
    description: str = ""
    risk_level: str = "low"  # low, medium, high
    requires_approval: bool = False
    arguments_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPlugin:
    """Plugin metadata and tool factory for dynamic registration.

    Attributes:
        name: Unique plugin identifier (e.g., "joi-minecraft")
        version: Semver string
        description: Human-readable description
        tools: List of ToolAdapter subclasses to register
        capabilities: Tool capability descriptions for discovery
        author: Plugin author name
        requires: List of required Python packages
    """
    name: str
    version: str = "0.0.0"
    description: str = ""
    tools: list[type[ToolAdapter]] = field(default_factory=list)
    capabilities: list[ToolCapability] = field(default_factory=list)
    author: str = ""
    requires: list[str] = field(default_factory=list)

    def instantiate_tools(self, **kwargs: Any) -> list[ToolAdapter]:
        """Instantiate all tool classes, passing kwargs to constructors."""
        instances = []
        for tool_cls in self.tools:
            try:
                instance = tool_cls(**kwargs)
                instances.append(instance)
            except TypeError:
                # Tool doesn't accept these kwargs, try bare constructor
                try:
                    instances.append(tool_cls())
                except Exception as exc:
                    logger.warning("Failed to instantiate %s: %s", tool_cls.__name__, exc)
            except Exception as exc:
                logger.warning("Failed to instantiate %s: %s", tool_cls.__name__, exc)
        return instances


def discover_plugins(
    plugin_dirs: list[Path] | None = None,
    workspace: Path | None = None,
) -> list[ToolPlugin]:
    """Scan plugin directories for Python modules implementing ToolPlugin.

    Each plugin module must export a `plugin` variable of type ToolPlugin,
    or implement a `create_plugin()` function returning one.

    Args:
        plugin_dirs: Directories to scan. Defaults to agent_companion/plugins/
        workspace: Project workspace path (passed to tool constructors)

    Returns:
        List of discovered ToolPlugin instances.
    """
    if plugin_dirs is None:
        default_dir = Path(__file__).parent.parent / "plugins"
        plugin_dirs = [default_dir]

    plugins: list[ToolPlugin] = []
    for plugin_dir in plugin_dirs:
        if not plugin_dir.is_dir():
            continue
        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            plugin = _load_plugin_module(py_file)
            if plugin is not None:
                plugins.append(plugin)

    return plugins


def _load_plugin_module(file_path: Path) -> ToolPlugin | None:
    """Load a single plugin module and extract its ToolPlugin."""
    module_name = f"agent_companion.plugins.{file_path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("Failed to load plugin %s: %s", file_path.name, exc)
        return None

    # Try `plugin` attribute first, then `create_plugin()` function
    plugin = getattr(module, "plugin", None)
    if plugin is None:
        factory = getattr(module, "create_plugin", None)
        if callable(factory):
            try:
                plugin = factory()
            except Exception as exc:
                logger.warning("Plugin factory %s failed: %s", file_path.name, exc)
                return None

    if not isinstance(plugin, ToolPlugin):
        return None

    # Validate
    if not plugin.name:
        logger.warning("Plugin %s has no name, skipping", file_path.name)
        return None

    return plugin
