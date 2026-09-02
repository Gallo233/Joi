from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import time
from typing import Any

from agent_companion.core.computer_use import (
    ComputerAction,
    ComputerObservation,
    ComputerUseBackend,
    ComputerUseResult,
    PostActionVerification,
    verify_post_action,
)
from agent_companion.core.platform_factory import get_computer_backend
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.tools.foreground_guard import companion_hidden_for_target_observation
from agent_companion.core.vision import OcrExtractor
from agent_companion.core.vision.ocr import run_ocr_safely
from agent_companion.core.voice import safe_voice_line


class ComputerActionTool(ToolAdapter):
    def __init__(
        self,
        workspace: Path,
        name: str,
        action_type: str,
        backend: ComputerUseBackend | None = None,
        ocr: OcrExtractor | None = None,
        post_action_settle_ms: int = 200,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.name = name
        self.action_type = action_type
        self.backend = backend or get_computer_backend(workspace)
        self.ocr = ocr
        self.post_action_settle_ms = max(0, int(post_action_settle_ms or 0))
        self._sleep = sleep_fn or time.sleep

    def run(self, request: ToolRequest) -> ToolResult:
        action = self._action_from_request(request)
        if action is None:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "invalid_arguments"},
                display_card=DisplayCard("电脑操作", "动作参数不完整。", self._friendly_detail("参数不足"), status="failed"),
                voice_line=safe_voice_line("这一步缺少必要参数，可以展开执行过程查看细节。", sprite="4"),
                risk=RiskLevel.MEDIUM,
            )

        with companion_hidden_for_target_observation():
            before_observation = self._observe_for_verification("before computer action")
            result = self.backend.perform(action)
            verification = None
            if result.ok:
                self._settle_after_action()
                result, verification = self._attach_after_observation(result, before_observation)
            return self._to_tool_result(result, verification, before_observation)

    def _action_from_request(self, request: ToolRequest) -> ComputerAction | None:
        args = request.arguments
        if self.action_type == "click":
            x = _maybe_int(args.get("x"))
            y = _maybe_int(args.get("y"))
            if x is None or y is None:
                return None
            return ComputerAction("click", x=x, y=y, button=str(args.get("button") or "left"))
        if self.action_type == "double_click":
            x = _maybe_int(args.get("x"))
            y = _maybe_int(args.get("y"))
            if x is None or y is None:
                return None
            return ComputerAction("double_click", x=x, y=y, button=str(args.get("button") or "left"))
        if self.action_type == "drag":
            x = _maybe_int(args.get("x"))
            y = _maybe_int(args.get("y"))
            end_x = _maybe_int(args.get("end_x"))
            end_y = _maybe_int(args.get("end_y"))
            if x is None or y is None or end_x is None or end_y is None:
                return None
            return ComputerAction("drag", x=x, y=y, end_x=end_x, end_y=end_y, button=str(args.get("button") or "left"))
        if self.action_type == "open_app":
            app_name = str(args.get("app_name") or "").strip()
            if not app_name:
                return None
            return ComputerAction("open_app", app_name=app_name)
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

    def _to_tool_result(
        self,
        result: ComputerUseResult,
        verification: PostActionVerification | None = None,
        before_observation: ComputerObservation | None = None,
    ) -> ToolResult:
        action_label = _action_label(result.action.action_type if result.action else self.action_type)
        status = _card_status(result.ok, verification)
        if self.action_type == "open_app" and result.ok and result.summary:
            summary = result.summary
        else:
            summary = verification.summary if verification else result.summary if result.ok and result.summary else f"{action_label}没有完成。"
        body = self._friendly_detail(result.error if result.error else "")
        artifacts = verification.artifacts if verification else [result.observation.screenshot_rel] if result.observation else []
        if verification:
            body = f"{body}\n结果：{verification.summary}"
        elif result.observation:
            body = f"{body}\n结果：已自动观察执行后的画面。"
        agent_state = {"tool": self.name, "computer_use": result.to_agent_state()}
        if before_observation and before_observation.screenshot_rel:
            agent_state["computer_use"]["before_artifact"] = before_observation.screenshot_rel
        if before_observation and before_observation.title:
            agent_state["computer_use"]["before_title"] = before_observation.title
        if result.observation and result.observation.screenshot_rel:
            agent_state["computer_use"]["after_artifact"] = result.observation.screenshot_rel
        if verification:
            agent_state["post_action_verification"] = verification.to_agent_state()
            agent_state["computer_use"]["verification"] = verification.to_agent_state()
        return ToolResult(
            ok=result.ok,
            agent_state=agent_state,
            display_card=DisplayCard("电脑操作", summary, body, status=status, artifacts=artifacts),
            voice_line=safe_voice_line(_voice_for_verification(result.ok, verification, result.summary if self.action_type == "open_app" else ""), sprite="5" if result.ok else "4"),
            risk=RiskLevel.MEDIUM,
        )

    def _observe_for_verification(self, query: str) -> ComputerObservation | None:
        try:
            observation = self.backend.observe(target="active_window", query=query)
        except Exception:
            return None
        return self._attach_ocr(observation)

    def _settle_after_action(self) -> None:
        if self.post_action_settle_ms <= 0:
            return
        self._sleep(self.post_action_settle_ms / 1000.0)

    def _attach_ocr(self, observation: ComputerObservation) -> ComputerObservation:
        if self.ocr is None:
            return observation
        ocr_result = run_ocr_safely(self.ocr, observation.screenshot_path)
        return replace(observation, ocr=ocr_result.to_agent_state())

    def _attach_after_observation(
        self,
        result: ComputerUseResult,
        before_observation: ComputerObservation | None,
    ) -> tuple[ComputerUseResult, PostActionVerification]:
        try:
            observation = self.backend.observe(target="active_window", query="after computer action")
        except Exception as exc:
            verification = verify_post_action(before_observation, None)
            return replace(result, detail=f"after observation failed: {type(exc).__name__}"), verification
        observation = self._attach_ocr(observation)
        verification = verify_post_action(before_observation, observation)
        return replace(result, observation=observation), verification

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
        "double_click": "双击指定位置",
        "drag": "拖拽操作",
        "type_text": "输入一段文字",
        "scroll": "滚动画面",
        "hotkey": "按下快捷键",
        "open_app": "打开应用",
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


def _voice_for_verification(ok: bool, verification: PostActionVerification | None, success_summary: str = "") -> str:
    if not ok:
        return "电脑操作没有完成，可以展开执行过程查看细节。"
    if success_summary:
        return success_summary
    if verification is None:
        return "电脑操作已经执行。"
    if verification.status == "changed":
        return "操作后画面有变化。"
    if verification.status == "likely_noop":
        return "操作执行了，但画面变化不明显。"
    return "操作执行了，暂时判断不了画面变化。"


def _card_status(ok: bool, verification: PostActionVerification | None) -> str:
    if not ok:
        return "failed"
    if verification is None or verification.status == "changed":
        return "success"
    return "info"
