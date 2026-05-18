from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_companion.core.computer_use import ComputerAction, ComputerUseBackend, ComputerUseResult, WindowsComputerUseBackend
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class ComputerActionTool(ToolAdapter):
    def __init__(self, workspace: Path, name: str, action_type: str, backend: ComputerUseBackend | None = None) -> None:
        self.workspace = workspace.resolve()
        self.name = name
        self.action_type = action_type
        self.backend = backend or WindowsComputerUseBackend(workspace)

    def run(self, request: ToolRequest) -> ToolResult:
        action = self._action_from_request(request)
        if action is None:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "invalid_arguments"},
                display_card=DisplayCard("电脑操作", "动作参数不完整。", self._friendly_detail("参数不足"), status="failed"),
                voice_line=safe_voice_line("这一步缺少必要参数，细节在卡片里。", sprite="4"),
                risk=RiskLevel.MEDIUM,
            )

        result = self.backend.perform(action)
        return self._to_tool_result(result)

    def _action_from_request(self, request: ToolRequest) -> ComputerAction | None:
        args = request.arguments
        if self.action_type == "click":
            x = _maybe_int(args.get("x"))
            y = _maybe_int(args.get("y"))
            if x is None or y is None:
                return None
            return ComputerAction("click", x=x, y=y, button=str(args.get("button") or "left"))
        if self.action_type == "type_text":
            text = str(args.get("text") or "").strip()
            if not text:
                return None
            return ComputerAction("type_text", text=text)
        if self.action_type == "scroll":
            delta = _maybe_int(args.get("delta"))
            if delta is None:
                direction = str(args.get("direction") or "down").casefold()
                amount = abs(_maybe_int(args.get("amount")) or 3)
                delta = amount if direction in {"up", "上", "向上"} else -amount
            return ComputerAction("scroll", delta=delta)
        if self.action_type == "hotkey":
            keys = _parse_keys(args.get("keys"))
            if not keys:
                return None
            return ComputerAction("hotkey", keys=keys)
        return None

    def _to_tool_result(self, result: ComputerUseResult) -> ToolResult:
        action_label = _action_label(result.action.action_type if result.action else self.action_type)
        status = "success" if result.ok else "failed"
        summary = result.summary if result.ok and result.summary else f"{action_label}没有完成。"
        body = self._friendly_detail(result.error if result.error else "")
        return ToolResult(
            ok=result.ok,
            agent_state={"tool": self.name, "computer_use": result.to_agent_state()},
            display_card=DisplayCard("电脑操作", summary, body, status=status),
            voice_line=safe_voice_line("电脑操作已经执行。" if result.ok else "电脑操作没有完成，细节在卡片里。", sprite="5" if result.ok else "4"),
            risk=RiskLevel.MEDIUM,
        )

    def _friendly_detail(self, error: str = "") -> str:
        label = _action_label(self.action_type)
        lines = [f"动作：{label}", "参数：已记录在审计数据中，不在语音中播报。"]
        if error:
            lines.append(f"结果：{_friendly_error(error)}")
        return "\n".join(lines)


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    for sep in ("+", "，", ",", " "):
        if sep in text:
            return tuple(part.strip() for part in text.split(sep) if part.strip())
    return (text,)


def _action_label(action_type: str) -> str:
    labels = {
        "click": "点击指定位置",
        "type_text": "输入一段文字",
        "scroll": "滚动画面",
        "hotkey": "按下快捷键",
    }
    return labels.get(action_type, "电脑操作")


def _friendly_error(error: str) -> str:
    if not error:
        return ""
    if "requires x and y" in error:
        return "缺少点击位置。"
    if "requires text" in error:
        return "缺少要输入的文字。"
    if "requires keys" in error:
        return "缺少快捷键。"
    if "unsupported" in error:
        return "当前动作暂不支持。"
    if "Windows only" in error:
        return "当前动作只支持 Windows。"
    return "动作执行失败。"
