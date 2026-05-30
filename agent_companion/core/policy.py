from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_companion.core.schemas import RiskLevel, ToolRequest
from agent_companion.core.skill_manifest import skill_id_for_tool


LOW_RISK = {
    "companion.chat",
    "observe.screen",
    "watch.recall",
    "browser.search",
    "browser.observe",
    "files.read",
    "mcp.list_tools",
    "vision.resolve_target",
    "vision.select_target",
}
MEDIUM_RISK = {
    "agent_cli.run",
    "codex.run",
    "browser.click",
    "browser.type",
    "computer.click",
    "computer.double_click",
    "computer.drag",
    "computer.type_text",
    "computer.scroll",
    "computer.hotkey",
    "computer.open_app",
    "computer.workflow",
    "game.ok_ww.run",
    "files.write_workspace",
    "runtime.update_config",
}
HIGH_RISK = {"shell.run", "files.delete", "package.install", "git.push", "external.launch_admin"}


@dataclass(frozen=True)
class PolicyDecision:
    risk: RiskLevel
    allowed: bool
    requires_approval: bool
    reason: str


class PolicyGate:
    def __init__(self, disabled_skills: set[str] | None = None) -> None:
        self.disabled_skills = set(disabled_skills or set())

    def classify(self, request: ToolRequest, approved: bool = False) -> PolicyDecision:
        name = request.name
        risk = RiskLevel.LOW
        skill_id = skill_id_for_tool(name)
        if skill_id in self.disabled_skills:
            return PolicyDecision(RiskLevel.MEDIUM, False, False, "skill_disabled")
        if name == "game.ok_ww.run" and bool(request.arguments.get("dry_run", True)):
            return PolicyDecision(risk, True, False, "游戏技能 dry-run 只做检查，可直接执行。")
        if name in MEDIUM_RISK:
            risk = RiskLevel.MEDIUM
        elif name in HIGH_RISK:
            risk = RiskLevel.HIGH
        elif name not in LOW_RISK:
            risk = RiskLevel.MEDIUM

        if risk == RiskLevel.LOW:
            return PolicyDecision(risk, True, False, "低风险动作可直接执行。")
        if approved:
            return PolicyDecision(risk, True, False, "用户已确认。")
        return PolicyDecision(risk, False, True, f"{risk.value} 风险动作需要确认。")

    @staticmethod
    def public_payload(request: ToolRequest) -> dict[str, Any]:
        preview = {key: str(value)[:160] for key, value in request.arguments.items()}
        if request.name.startswith("computer."):
            preview = _computer_preview(request.arguments)
        elif request.name == "agent_cli.run":
            preview = _agent_cli_preview(request.arguments)
        elif request.name == "codex.run":
            preview = _codex_preview(request.arguments)
        elif request.name == "runtime.update_config":
            preview = _runtime_config_preview(request.arguments)
        return {
            "tool": request.name,
            "reason": request.reason,
            "arguments_preview": preview,
        }


def _computer_preview(arguments: dict[str, Any]) -> dict[str, str]:
    preview: dict[str, str] = {}
    if "x" in arguments and "y" in arguments:
        preview["target"] = "指定屏幕位置"
    if "text" in arguments:
        preview["text"] = f"{len(str(arguments.get('text') or ''))} characters"
    if "direction" in arguments:
        preview["direction"] = str(arguments.get("direction"))
    if "keys" in arguments:
        preview["keys"] = " + ".join(str(key) for key in arguments.get("keys") or [])
    if "workflow" in arguments:
        preview["workflow"] = str(arguments.get("workflow") or "desktop_sequence")
    if "app_name" in arguments:
        preview["app"] = "app_name_hidden"
    return preview


def _codex_preview(arguments: dict[str, Any]) -> dict[str, str]:
    preview: dict[str, str] = {}
    if arguments.get("goal"):
        preview["goal"] = "coding_request"
    if arguments.get("codex_permission_hash"):
        preview["permission"] = "one_time_codex_permission"
    if arguments.get("codex_permission_decision"):
        preview["decision"] = "approval_required_to_continue"
    return preview or {"request": "coding_task"}


def _agent_cli_preview(arguments: dict[str, Any]) -> dict[str, str]:
    preview: dict[str, str] = {"mode": "agent_cli_takeover"}
    cli_id = str(arguments.get("cli_id") or "").strip()
    if cli_id:
        preview["cli"] = cli_id[:40]
    if arguments.get("goal"):
        preview["goal"] = "takeover_request"
    if arguments.get("codex_permission_hash"):
        preview["permission"] = "one_time_agent_cli_permission"
    if arguments.get("codex_permission_decision"):
        preview["decision"] = "approval_required_to_continue"
    return preview


def _runtime_config_preview(arguments: dict[str, Any]) -> dict[str, str]:
    updates = arguments.get("updates")
    field_count = _count_update_fields(updates) if isinstance(updates, dict) else 0
    return {
        "settings": f"{field_count} requested",
        "mode": "preview" if arguments.get("dry_run") else "write",
    }


def _count_update_fields(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_update_fields(item) for item in value.values())
    return 1
