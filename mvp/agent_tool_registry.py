from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source_text: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    summary: str
    detail: str = ""
    requires_approval: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.requires_approval:
            return "approval_required"
        return "success" if self.ok else "failed"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    executor: Callable[[dict[str, Any]], ToolResult]
    aliases: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    approved_executor: Callable[[dict[str, Any]], ToolResult] | None = None

    def public_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "parameters": self.parameters_schema,
            "examples": list(self.examples),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._aliases: dict[str, str] = {}

    def register(self, definition: ToolDefinition) -> None:
        key = self._normalize(definition.name)
        self._definitions[key] = definition
        self._aliases[key] = key
        for alias in definition.aliases:
            self._aliases[self._normalize(alias)] = key

    def get(self, name: str) -> ToolDefinition | None:
        key = self._aliases.get(self._normalize(name))
        if key is None:
            return None
        return self._definitions.get(key)

    def execute(self, call: ToolCall, approved: bool = False) -> ToolResult:
        definition = self.get(call.tool_name)
        if definition is None:
            return ToolResult(False, f"未知工具：{call.tool_name}")
        if approved and definition.approved_executor is not None:
            return definition.approved_executor(call.arguments)
        return definition.executor(call.arguments)

    def list_definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def public_schemas(self) -> list[dict[str, Any]]:
        return [definition.public_schema() for definition in self.list_definitions()]

    @staticmethod
    def _normalize(value: str) -> str:
        return (value or "").strip().casefold()
