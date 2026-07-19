from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_companion.core.runtime_config_writer import update_runtime_config
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class RuntimeConfigUpdateTool(ToolAdapter):
    name = "runtime.update_config"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, request: ToolRequest) -> ToolResult:
        updates = request.arguments.get("updates")
        dry_run = bool(request.arguments.get("dry_run", False))
        result = update_runtime_config(self.workspace, updates if isinstance(updates, dict) else {}, dry_run=dry_run)
        body = _body_from_result(result.to_agent_state())
        return ToolResult(
            ok=result.ok,
            agent_state={"tool": self.name, "runtime_config_update": result.to_agent_state()},
            display_card=DisplayCard(
                "运行设置",
                result.summary,
                body,
                status="success" if result.ok else "failed",
            ),
            voice_line=safe_voice_line("运行设置预览好了。" if dry_run and result.ok else "运行设置已更新。" if result.ok else "运行设置没有更新，可以展开执行过程查看细节。", sprite="3" if result.ok else "4"),
            risk=RiskLevel.MEDIUM,
        )


def _body_from_result(payload: dict[str, Any]) -> str:
    lines = [str(payload.get("summary") or "运行设置状态已更新。")]
    changes = payload.get("changes") if isinstance(payload.get("changes"), list) else []
    for row in changes[:12]:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "运行设置")
        action = "会更新" if row.get("action") == "changed" and payload.get("dry_run") else "已更新" if row.get("action") == "changed" else "无变化"
        lines.append(f"- {label}: {action}")
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    for row in errors[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(f"- 错误：{row.get('code') or 'invalid_update'}")
    return "\n".join(lines)
