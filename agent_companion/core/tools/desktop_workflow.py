from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import quote_plus

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


class DesktopWorkflowTool(ToolAdapter):
    name = "computer.workflow"

    def __init__(
        self,
        workspace: Path,
        backend: ComputerUseBackend | None = None,
        ocr: OcrExtractor | None = None,
        post_action_settle_ms: int = 600,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.backend = backend or get_computer_backend(workspace)
        self.ocr = ocr
        self.post_action_settle_ms = max(0, int(post_action_settle_ms or 0))
        self._sleep = sleep_fn or time.sleep

    def run(self, request: ToolRequest) -> ToolResult:
        workflow = str(request.arguments.get("workflow") or "").strip()
        actions = self._actions_from_request(request)
        if not workflow or not actions:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "invalid_workflow"},
                display_card=DisplayCard("电脑操作", "这类桌面操作还没有被识别出来。", "支持打开应用、用浏览器打开站点并搜索。", status="failed"),
                voice_line=safe_voice_line("这类桌面操作我还没识别出来。", sprite="4"),
                risk=RiskLevel.MEDIUM,
            )

        with companion_hidden_for_target_observation():
            before_observation = self._observe_for_verification("before desktop workflow")
            result = self._perform_workflow(actions)
            verification = None
            if result.ok:
                self._settle_after_action()
                result, verification = self._attach_after_observation(result, before_observation)
            return self._to_tool_result(request, workflow, actions, result, verification, before_observation)

    def _actions_from_request(self, request: ToolRequest) -> list[ComputerAction]:
        args = request.arguments
        workflow = str(args.get("workflow") or "").strip()
        mac_backend = _is_macos_backend(self.backend)
        if workflow == "open_app":
            app = str(args.get("app") or "").strip()
            if not app:
                return []
            return _open_app_actions(app, mac_backend=mac_backend)
        if workflow == "open_web_search":
            browser = _browser_search_name(str(args.get("browser") or "edge"), mac_backend=mac_backend)
            site = str(args.get("site") or "").strip()
            query = str(args.get("query") or "").strip()
            url = _site_url(site, query)
            return _open_url_in_browser_actions(browser, url, mac_backend=mac_backend)
        if workflow == "open_url":
            browser = _browser_search_name(str(args.get("browser") or "edge"), mac_backend=mac_backend)
            url = str(args.get("url") or "").strip()
            if not url:
                return []
            return _open_url_in_browser_actions(browser, _normalize_url(url), mac_backend=mac_backend)
        return []

    def _perform_workflow(self, actions: list[ComputerAction]) -> ComputerUseResult:
        performer = getattr(self.backend, "perform_sequence", None)
        if callable(performer):
            return performer(actions, settle_ms=260)
        last_result: ComputerUseResult | None = None
        for action in actions:
            if action.action_type == "wait":
                self._sleep(max(0, int(action.delta or 0)) / 1000.0)
                continue
            last_result = self.backend.perform(action)
            if not last_result.ok:
                return ComputerUseResult(False, action=ComputerAction("workflow"), error=last_result.error or "workflow step failed")
            self._sleep(0.26)
        return ComputerUseResult(True, action=ComputerAction("workflow"), summary="完成了多步电脑操作。")

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
            observation = self.backend.observe(target="active_window", query="after desktop workflow")
        except Exception as exc:
            verification = verify_post_action(before_observation, None)
            return replace(result, detail=f"after observation failed: {type(exc).__name__}"), verification
        observation = self._attach_ocr(observation)
        verification = verify_post_action(before_observation, observation)
        return replace(result, observation=observation), verification

    def _to_tool_result(
        self,
        request: ToolRequest,
        workflow: str,
        actions: list[ComputerAction],
        result: ComputerUseResult,
        verification: PostActionVerification | None,
        before_observation: ComputerObservation | None,
    ) -> ToolResult:
        status = "failed" if not result.ok else "success" if verification is None or verification.status == "changed" else "info"
        if workflow == "open_app" and result.ok and result.summary:
            summary = result.summary
        else:
            summary = verification.summary if verification else ("桌面操作已执行。" if result.ok else "桌面操作没有完成。")
        body_lines = [
            f"流程：{_workflow_label(workflow)}",
            f"步骤：{len([action for action in actions if action.action_type != 'wait'])} 个键鼠动作",
            "输入内容：已记录为长度和类型，不在语音中播报。",
        ]
        if result.error:
            body_lines.append("结果：动作执行失败。")
        if verification:
            body_lines.append(f"验证：{verification.summary}")
        artifacts = verification.artifacts if verification else [result.observation.screenshot_rel] if result.observation else []
        computer_use = result.to_agent_state()
        action_state = computer_use.get("action") if isinstance(computer_use.get("action"), dict) else {}
        action_state.update({"type": "workflow", "workflow": workflow, "step_count": len([action for action in actions if action.action_type != "wait"])})
        computer_use["action"] = action_state
        if before_observation and before_observation.screenshot_rel:
            computer_use["before_artifact"] = before_observation.screenshot_rel
        if before_observation and before_observation.title:
            computer_use["before_title"] = before_observation.title
        if result.observation and result.observation.screenshot_rel:
            computer_use["after_artifact"] = result.observation.screenshot_rel
        agent_state: dict[str, Any] = {"tool": self.name, "computer_use": computer_use}
        if verification:
            agent_state["post_action_verification"] = verification.to_agent_state()
            computer_use["verification"] = verification.to_agent_state()
        return ToolResult(
            ok=result.ok,
            agent_state=agent_state,
            display_card=DisplayCard("电脑操作", summary, "\n".join(body_lines), status=status, artifacts=artifacts),
            voice_line=safe_voice_line(_workflow_voice(workflow, result, verification), sprite="5" if result.ok else "4"),
            risk=RiskLevel.MEDIUM,
        )


def _is_macos_backend(backend: ComputerUseBackend) -> bool:
    # The CUA driver may be wrapped for safe native fallback, so look through
    # the wrapper rather than at the outermost class name.
    underlying = getattr(backend, "primary", backend)
    return sys.platform == "darwin" and underlying.__class__.__name__ in {"MacComputerUseBackend", "CuaDriverBackend"}


def _open_app_actions(app: str, *, mac_backend: bool = False) -> list[ComputerAction]:
    if mac_backend:
        return [
            ComputerAction("open_app", app_name=app),
            ComputerAction("wait", delta=1200),
        ]
    return [
        ComputerAction("hotkey", keys=("win",)),
        ComputerAction("wait", delta=260),
        ComputerAction("type_text", text=app),
        ComputerAction("wait", delta=120),
        ComputerAction("hotkey", keys=("enter",)),
        ComputerAction("wait", delta=1200),
    ]


def _open_url_in_browser_actions(browser: str, url: str, *, mac_backend: bool = False) -> list[ComputerAction]:
    if mac_backend:
        return [
            ComputerAction("open_url", text=url, app_name=browser),
            ComputerAction("wait", delta=2200),
        ]
    return [
        *_open_app_actions(browser, mac_backend=mac_backend),
        ComputerAction("wait", delta=1500),
        ComputerAction("hotkey", keys=("cmd" if mac_backend else "ctrl", "l")),
        ComputerAction("wait", delta=100),
        ComputerAction("type_text", text=url),
        ComputerAction("wait", delta=100),
        ComputerAction("hotkey", keys=("enter",)),
        ComputerAction("wait", delta=1800),
    ]


def _browser_search_name(value: str, *, mac_backend: bool = False) -> str:
    lowered = value.strip().casefold()
    if lowered in {"chrome", "谷歌", "google chrome"}:
        return "Google Chrome"
    if mac_backend and lowered in {"", "edge", "microsoft edge", "默认", "default"}:
        return "Safari"
    return "Microsoft Edge"


def _site_url(site: str, query: str = "") -> str:
    normalized = site.strip().casefold()
    if normalized in {"bilibili", "bilbil", "b站", "bili", "哔哩哔哩"}:
        if any(token in query.casefold() for token in ("热门", "popular", "hot", "排行")):
            return "https://www.bilibili.com/v/popular/all"
        if query:
            return "https://search.bilibili.com/all?keyword=" + quote_plus(query)
        return "https://www.bilibili.com"
    if query:
        return "https://www.baidu.com/s?wd=" + quote_plus(query)
    return _normalize_url(site)


def _normalize_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    if "://" in value:
        return value
    return "https://" + value


def _workflow_label(workflow: str) -> str:
    return {
        "open_app": "打开应用",
        "open_web_search": "打开站点并搜索",
        "open_url": "打开网页",
    }.get(workflow, "桌面流程")


def _workflow_voice(workflow: str, result: ComputerUseResult, verification: PostActionVerification | None) -> str:
    if not result.ok:
        return "桌面操作没有完成，可以展开执行过程查看细节。"
    if workflow == "open_app" and result.summary:
        return result.summary
    if verification is None:
        return "桌面操作已经执行。"
    if verification.status == "changed":
        return "操作后画面有变化。"
    if verification.status == "likely_noop":
        return "操作执行了，但画面变化不明显。"
    return "操作执行了，暂时判断不了画面变化。"
