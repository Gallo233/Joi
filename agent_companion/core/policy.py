from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_companion.core.schemas import RiskLevel, ToolRequest


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
    "codex.run",
    "browser.click",
    "browser.type",
    "computer.click",
    "computer.type_text",
    "computer.scroll",
    "computer.hotkey",
    "game.ok_ww.run",
    "files.write_workspace",
}
HIGH_RISK = {"shell.run", "files.delete", "package.install", "git.push", "external.launch_admin"}


@dataclass(frozen=True)
class PolicyDecision:
    risk: RiskLevel
    allowed: bool
    requires_approval: bool
    reason: str


class PolicyGate:
    def classify(self, request: ToolRequest, approved: bool = False) -> PolicyDecision:
        name = request.name
        risk = RiskLevel.LOW
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
        elif request.name == "codex.run":
            preview = _codex_preview(request.arguments)
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
