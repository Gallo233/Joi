from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid
from typing import Any

from agent_companion.core.config import AppConfig, ModelRouter, load_app_config
from agent_companion.core.schemas import AgentPlan, ToolRequest


MIN_CONFIDENCE = 0.55
SUPPORTED_INTENTS = {"companion_chat", "desktop_workflow", "browser", "watch_together", "computer_use", "semantic_target"}
SEARCH_ACTIONS = {"search", "browser_search", "web_search"}
WATCH_ACTIONS = {"observe", "watch", "watch_current", "observe_screen"}
CLICK_ACTIONS = {"click", "click_target", "target_click"}
TYPE_ACTIONS = {"type", "type_text"}
SCROLL_ACTIONS = {"scroll"}
HOTKEY_ACTIONS = {"hotkey", "shortcut"}


class LlmPlanParser:
    """Converts natural language into a constrained Joi AgentPlan.

    The model is only allowed to fill an intent and slots. Execution still goes
    through Joi tools, policy approval, and audit.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config = self._load_config()
        self._client: Any | None = None

    def should_try(self, user_text: str, rule_plan: AgentPlan) -> bool:
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return False
        if self._config is None or self._config.llm.use_mock or not self._config.llm.is_configured:
            return False
        text = user_text or ""
        if rule_plan.intent == "companion_chat":
            return _looks_actionable(text)
        if rule_plan.intent == "browser":
            return any(token in text.casefold() for token in ("edge", "chrome", "bilibili", "bili")) or any(token in text for token in ("浏览器", "网页", "B站", "b站", "哔哩"))
        if rule_plan.intent == "desktop_workflow":
            step = rule_plan.steps[0] if rule_plan.steps else None
            return bool(step and step.name == "computer.workflow" and step.arguments.get("workflow") == "open_web_search" and not step.arguments.get("query") and _has_search_word(text))
        return False

    def plan(self, user_text: str, rule_plan: AgentPlan) -> AgentPlan | None:
        if not self.should_try(user_text, rule_plan):
            return None
        payload = self._call_model(user_text, rule_plan)
        if payload is None:
            return None
        return plan_from_llm_payload(user_text, payload)

    def _call_model(self, user_text: str, rule_plan: AgentPlan) -> dict[str, Any] | None:
        config = self._config
        if config is None:
            return None
        try:
            from openai import OpenAI
        except Exception:
            return None
        try:
            endpoint = ModelRouter(config.llm).resolve("reasoning")
            if self._client is None or self._client.base_url != endpoint.base_url:
                self._client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url, timeout=8.0)
            response = self._client.chat.completions.create(
                model=endpoint.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 Joi 的安全计划槽位解析器。只输出 JSON object，不要解释。\n"
                            "你只能选择这些 intent: companion_chat, desktop_workflow, browser, watch_together, computer_use, semantic_target。\n"
                            "如果用户要打开真实桌面应用、Edge/Chrome、B站并站内搜索，输出 desktop_workflow。\n"
                            "如果用户只是泛搜索，没有指定真实桌面浏览器/站点，输出 browser。\n"
                            "如果用户要看当前画面/当前窗口/当前页面，或追问正在播放的视频/这一段在讲什么，输出 watch_together；转写/字幕/系统音频请求也输出 watch_together。\n"
                            "如果用户要点击一个可见目标，不要输出坐标，输出 semantic_target + action=click_target + query=原目标描述。\n"
                            "禁止输出 shell、文件删除、安装、git、坐标点击、密钥、路径或任何未列出的工具。\n"
                            "格式：{\"intent\":\"desktop_workflow\",\"confidence\":0.9,\"action\":\"open_web_search\",\"slots\":{\"browser\":\"edge\",\"site\":\"bilibili\",\"query\":\"猫猫视频\"}}。\n"
                            "desktop_workflow action 只能是 open_app, open_web_search, open_url。browser 只能是 edge 或 chrome。site 支持 bilibili 或 URL。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_text": user_text,
                                "rule_plan": {
                                    "intent": rule_plan.intent,
                                    "steps": [{"name": step.name, "arguments": step.arguments} for step in rule_plan.steps[:3]],
                                },
                                "examples": [
                                    {"text": "帮我用edge打开b站搜猫猫视频", "intent": "desktop_workflow", "action": "open_web_search", "slots": {"browser": "edge", "site": "bilibili", "query": "猫猫视频"}},
                                    {"text": "打开codex", "intent": "desktop_workflow", "action": "open_app", "slots": {"app": "Codex"}},
                                    {"text": "搜猫猫视频", "intent": "browser", "action": "search", "slots": {"query": "猫猫视频"}},
                                    {"text": "点右上角登录", "intent": "semantic_target", "action": "click_target", "slots": {"query": "点右上角登录"}},
                                    {"text": "这个视频在讲什么", "intent": "watch_together", "action": "observe_screen", "slots": {"query": "这个视频在讲什么"}},
                                    {"text": "实时转写当前视频", "intent": "watch_together", "action": "observe_screen", "slots": {"query": "实时转写当前视频", "transcribe": True}},
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.choices[0].message.content or "{}")
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _load_config(self) -> AppConfig | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            return load_app_config(config_path)
        except Exception:
            return None


def plan_from_llm_payload(user_text: str, payload: dict[str, Any], task_id: str | None = None) -> AgentPlan | None:
    text = " ".join((user_text or "").strip().split())
    if not text:
        return None
    intent = _safe_identifier(payload.get("intent")).casefold()
    if intent not in SUPPORTED_INTENTS:
        return None
    confidence = _safe_confidence(payload.get("confidence"))
    if confidence < MIN_CONFIDENCE:
        return None
    slots = payload.get("slots") if isinstance(payload.get("slots"), dict) else payload
    action = _safe_identifier(payload.get("action") or payload.get("workflow")).casefold()
    plan_id = task_id or f"task-{uuid.uuid4().hex[:10]}"

    if intent == "companion_chat":
        return AgentPlan(plan_id, text, "companion_chat", [ToolRequest("companion.chat", {"text": text}, "普通对话只调用角色表达，不启动外部执行器。")])

    if intent == "desktop_workflow":
        request = _desktop_request_from_slots(action, slots)
        if request is None:
            return None
        return AgentPlan(plan_id, text, "desktop_workflow", [request])

    if intent == "browser":
        request = _browser_request_from_slots(action, slots, text)
        if request is None:
            return None
        return AgentPlan(plan_id, text, "browser", [request])

    if intent == "watch_together":
        if action and action not in WATCH_ACTIONS:
            return None
        args: dict[str, Any] = {"query": text}
        if _looks_like_video_transcription_task(text):
            args["sample_count"] = 8
            args["sample_interval_ms"] = 700
            args["transcribe"] = True
            if any(token in text for token in ("系统音频", "电脑声音", "播放声音", "听一下", "听懂")):
                args["transcript_source"] = "system_audio"
        elif _looks_like_video_content_question(text):
            args["sample_count"] = 4
        return AgentPlan(plan_id, text, "watch_together", [ToolRequest("observe.screen", args, "观察当前窗口或屏幕内容并生成陪看摘要。")])

    if intent == "semantic_target" or action in CLICK_ACTIONS:
        query = _clean_text(slots.get("query") or slots.get("target") or text, 160)
        if not query:
            return None
        return AgentPlan(plan_id, text, "semantic_target", [ToolRequest("vision.resolve_target", {"query": query, "action": "click"}, "先从当前画面中寻找候选区域。")])

    if intent == "computer_use":
        request = _computer_request_from_slots(action, slots)
        if request is None:
            return None
        step_intent = "semantic_target" if request.name == "vision.resolve_target" else "computer_use"
        return AgentPlan(plan_id, text, step_intent, [request])

    return None


def _desktop_request_from_slots(action: str, slots: dict[str, Any]) -> ToolRequest | None:
    workflow = _safe_identifier(slots.get("workflow") or action).casefold()
    if workflow == "open_app":
        app = _clean_app(slots.get("app") or slots.get("application"))
        if not app:
            return None
        return ToolRequest("computer.workflow", {"workflow": "open_app", "app": app}, "打开应用会操作当前电脑，需要确认。")
    if workflow == "open_web_search":
        browser = _clean_browser(slots.get("browser"))
        site = _clean_site(slots.get("site") or slots.get("url"))
        query = _clean_text(slots.get("query") or slots.get("keyword"), 160)
        if not site and not query:
            return None
        args: dict[str, str] = {"workflow": "open_web_search", "browser": browser}
        if site:
            args["site"] = site
        if query:
            args["query"] = query
        return ToolRequest("computer.workflow", args, "打开浏览器和网页会操作当前电脑，需要确认。")
    if workflow == "open_url":
        browser = _clean_browser(slots.get("browser"))
        url = _clean_url(slots.get("url") or slots.get("site"))
        if not url:
            return None
        return ToolRequest("computer.workflow", {"workflow": "open_url", "browser": browser, "url": url}, "打开网页会操作当前电脑，需要确认。")
    return None


def _browser_request_from_slots(action: str, slots: dict[str, Any], user_text: str) -> ToolRequest | None:
    if action and action not in SEARCH_ACTIONS | WATCH_ACTIONS:
        return None
    url = _clean_url(slots.get("url"))
    if action in WATCH_ACTIONS or url:
        args = {"query": _clean_text(slots.get("query") or user_text, 180)}
        if url:
            args["url"] = url
        return ToolRequest("browser.observe", args, "观察浏览器页面。")
    query = _clean_text(slots.get("query") or slots.get("keyword") or user_text, 180)
    if not query:
        return None
    return ToolRequest("browser.search", {"query": query}, "使用本地浏览器搜索并观察结果。")


def _computer_request_from_slots(action: str, slots: dict[str, Any]) -> ToolRequest | None:
    if action in CLICK_ACTIONS:
        query = _clean_text(slots.get("query") or slots.get("target"), 160)
        if not query:
            return None
        return ToolRequest("vision.resolve_target", {"query": query, "action": "click"}, "先从当前画面中寻找候选区域。")
    if action in TYPE_ACTIONS:
        text = _clean_text(slots.get("text"), 300)
        if not text:
            return None
        return ToolRequest("computer.type_text", {"text": text}, "向当前前台应用输入文字，需要确认。")
    if action in SCROLL_ACTIONS:
        direction = _safe_identifier(slots.get("direction")).casefold()
        return ToolRequest("computer.scroll", {"direction": "up" if direction == "up" else "down", "amount": 3}, "滚动当前前台应用，需要确认。")
    if action in HOTKEY_ACTIONS:
        keys = slots.get("keys")
        if not isinstance(keys, list):
            return None
        clean_keys = [_safe_identifier(key) for key in keys[:4] if _safe_identifier(key)]
        if not clean_keys:
            return None
        return ToolRequest("computer.hotkey", {"keys": clean_keys}, "按下系统快捷键会影响当前前台应用，需要确认。")
    return None


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_+\- .:/\u4e00-\u9fff]", "", text)[:80]


def _clean_browser(value: Any) -> str:
    raw = _safe_identifier(value).casefold()
    if raw in {"chrome", "google chrome", "谷歌"}:
        return "chrome"
    return "edge"


def _clean_site(value: Any) -> str:
    raw = _clean_text(value, 160)
    lowered = raw.casefold()
    if lowered in {"bilibili", "bilbil", "bili"} or raw in {"B站", "b站", "哔哩哔哩", "哔哩"}:
        return "bilibili"
    return _clean_url(raw)


def _clean_url(value: Any) -> str:
    raw = _clean_text(value, 240)
    if not raw:
        return ""
    if _unsafe_text(raw):
        return ""
    if re.match(r"https?://[^\s，。]+$", raw, re.IGNORECASE):
        return raw
    if re.match(r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?$", raw):
        return "https://" + raw
    return ""


def _clean_app(value: Any) -> str:
    raw = _clean_text(value, 80)
    if _unsafe_text(raw):
        return ""
    aliases = {
        "codex": "Codex",
        "edge": "Microsoft Edge",
        "microsoft edge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "vscode": "Visual Studio Code",
        "vs code": "Visual Studio Code",
    }
    return aliases.get(raw.casefold(), raw)


def _clean_text(value: Any, max_len: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text or _unsafe_text(text):
        return ""
    return text[:max_len]


def _unsafe_text(value: str) -> bool:
    text = value or ""
    if re.search(r"(?i)\b(?:sk-[A-Za-z0-9_-]{4,}|api[_-]?key|token|secret)\b", text):
        return True
    if any(token in text for token in ("\n", "\r", ";", "|", "&&", "||", "`", "$(")):
        return True
    return False


def _looks_actionable(text: str) -> bool:
    lowered = (text or "").casefold()
    return (
        any(token in text for token in ("打开", "启动", "运行", "开启", "搜索", "搜", "查找", "点击", "点一下", "输入", "滚动", "当前页面", "当前窗口", "视频", "播放", "帮我用", "在B站", "在b站"))
        or any(token in lowered for token in ("edge", "chrome", "bilibili", "codex", "open ", "search ", "click ", "type "))
    )


def _has_search_word(text: str) -> bool:
    return any(token in text for token in ("搜索", "搜一下", "查找", "搜"))


def _looks_like_video_content_question(text: str) -> bool:
    value = text or ""
    video_tokens = ("视频", "播放", "弹幕", "字幕", "B站", "b站", "哔哩", "这一段", "这段")
    content_tokens = ("讲什么", "讲了什么", "在讲", "关于什么", "内容", "发生了什么", "讲到哪", "说了什么", "看懂", "总结", "解释")
    if any(token in value for token in video_tokens) and any(token in value for token in content_tokens):
        return True
    return any(token in value for token in ("现在讲到哪", "刚才说了什么", "刚刚说了什么", "这一段在讲什么", "这段在讲什么"))


def _looks_like_video_transcription_task(text: str) -> bool:
    value = text or ""
    return any(token in value for token in ("实时转写", "转写当前视频", "转写这个视频", "视频字幕", "字幕源", "系统音频", "听一下这个视频", "听懂这个视频"))
