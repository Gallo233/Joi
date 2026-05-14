from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_companion.core.schemas import RiskLevel, ToolRequest


LOW_RISK = {"companion.chat", "observe.screen", "browser.search", "browser.observe", "files.read", "mcp.list_tools"}
MEDIUM_RISK = {"codex.run", "browser.click", "browser.type", "game.ok_ww.run", "files.write_workspace"}
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
        return {
            "tool": request.name,
            "reason": request.reason,
            "arguments_preview": {key: str(value)[:160] for key, value in request.arguments.items()},
        }
