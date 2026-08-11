from __future__ import annotations

import re
import sys
import uuid

from agent_companion.core.character_motion import character_motion_from_text
from agent_companion.core.language_policy import display_language_policy
from agent_companion.core.schemas import AgentPlan, ToolRequest

SEARCH_VERB_RE = r"(?:搜索|搜一下|查找|搜(?!集))"


def build_plan(user_text: str) -> AgentPlan:
    text = " ".join((user_text or "").strip().split())
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    lowered = text.casefold()

    character_motion = character_motion_from_text(text)
    if character_motion:
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="character_motion",
            steps=[
                ToolRequest(
                    "character.perform",
                    {
                        "motion": character_motion,
                        # What the user wrote in, so the line she says on screen
                        # follows the message rather than the voice she is
                        # configured to speak. A Chinese "跳个舞" answered in
                        # Japanese is the same violation the chat replies
                        # already avoid.
                        "reply_language": display_language_policy(text).code,
                    },
                    "播放本地角色动作，不操作外部应用。",
                )
            ],
        )
    if _is_game_task(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="game_assist",
            steps=[
                ToolRequest("game.ok_ww.run", {"intent": text, "dry_run": True}, "先检查 OK-WW 技能是否可启动。"),
                ToolRequest("game.ok_ww.run", {"intent": text, "dry_run": False}, "游戏自动化会启动外部技能，需要确认。"),
            ],
        )
    if _is_video_transcription_task(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_together",
            steps=[
                ToolRequest(
                    "observe.screen",
                    _video_transcription_arguments(text),
                    "连续采样当前视频字幕或系统音频，生成实时转写上下文。",
                )
            ],
        )
    if _is_current_video_question(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_together",
            steps=[
                ToolRequest(
                    "observe.screen",
                    {"query": text, "sample_count": 4},
                    "重新连续采样当前视频画面，更新陪看上下文。",
                )
            ],
        )
    if _is_watch_followup(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_followup",
            steps=[ToolRequest("watch.recall", {"query": text}, "优先使用最近的陪看视觉上下文回答，不重复截图。")],
        )
    if _is_execution_status_question(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="companion_chat",
            steps=[ToolRequest("companion.chat", {"text": text}, "状态追问只走对话，不启动新的桌面操作。")],
        )
    desktop_workflow = _build_desktop_workflow(text, lowered)
    if desktop_workflow is not None:
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="desktop_workflow",
            steps=[desktop_workflow],
        )
    if _is_watch_task(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_together",
            steps=[ToolRequest("observe.screen", {"query": text}, "观察当前窗口或屏幕内容并生成陪看摘要。")],
        )
    if _looks_like_open_app(text, lowered):
        app_name = _parse_app_name(text)
        if app_name:
            # Use open_app action which handles Spotlight + open -a fallback internally
            return AgentPlan(
                task_id=task_id,
                user_text=text,
                intent="computer_use",
                steps=[
                    ToolRequest("computer.open_app", {"app_name": app_name}, f"打开应用 {app_name}。"),
                ],
            )
    computer_action = _build_computer_action(text, lowered)
    if computer_action is not None:
        intent = "semantic_target" if computer_action.name == "vision.resolve_target" else "computer_use"
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent=intent,
            steps=[computer_action],
        )
    if _is_code_task(text, lowered):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="coding",
            steps=[ToolRequest("codex.run", {"goal": text}, "交给 Codex 执行工程任务。")],
        )
    if _looks_like_search_command(text):
        search_query = _parse_browser_search_query(text)
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="browser",
            steps=[ToolRequest("browser.search", {"query": search_query or text}, "使用本地浏览器搜索并观察结果。")],
        )
    if "网页" in text or "浏览器" in text or re.search(r"https?://", text):
        url = _parse_url(text)
        arguments = {"query": text}
        if url:
            arguments["url"] = url
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="browser",
            steps=[ToolRequest("browser.observe", arguments, "观察浏览器页面。")],
        )
    return AgentPlan(
        task_id=task_id,
        user_text=text,
        intent="companion_chat",
        steps=[ToolRequest("companion.chat", {"text": text}, "普通对话只调用角色表达，不启动外部执行器。")],
    )


def _is_game_task(text: str) -> bool:
    return any(token in text for token in ("鸣潮", "ok-ww", "OK-WW", "刷副本", "清体力", "梦魇", "游戏日常", "Minecraft", "我的世界"))


def _is_watch_task(text: str) -> bool:
    return any(
        token in text
        for token in (
            "陪我看",
            "看电影",
            "看视频",
            "一起看",
            "当前画面",
            "当前窗口",
            "当前页面",
            "当前网页",
            "浏览器页面",
            "这个页面",
            "这个视频",
            "正在播放",
            "这段剧情",
            "B站",
            "b站",
        )
    )


def _is_watch_followup(text: str) -> bool:
    return any(token in text for token in ("刚刚发生了什么", "你看到了什么", "你刚才看到了什么", "这个页面讲什么", "刚才的画面", "刚才看到的"))


def _is_execution_status_question(text: str) -> bool:
    value = re.sub(r"\s+", "", text or "").strip("。！？!?，,")
    if not value:
        return False
    if value in {"打开了吗", "打开了没", "开了吗", "开了没", "启动了吗", "启动了没", "运行了吗", "运行了没", "执行了吗", "执行了没", "好了没", "好了吗", "成功了吗", "成功了没"}:
        return True
    return bool(re.search(r"(?:打开|启动|运行|执行|搜索|搜).{0,8}(?:了吗|了没|成功了吗|成功了没)$", value))


def _is_current_video_question(text: str) -> bool:
    video_tokens = ("视频", "播放", "弹幕", "字幕", "B站", "b站", "哔哩", "这一段", "这段")
    content_tokens = ("讲什么", "讲了什么", "在讲", "关于什么", "内容", "发生了什么", "讲到哪", "说了什么", "看懂", "总结", "解释")
    if any(token in text for token in video_tokens) and any(token in text for token in content_tokens):
        return True
    return any(token in text for token in ("现在讲到哪", "刚才说了什么", "刚刚说了什么", "这一段在讲什么", "这段在讲什么"))


def _is_video_transcription_task(text: str) -> bool:
    return any(token in text for token in ("实时转写", "转写当前视频", "转写这个视频", "视频字幕", "字幕源", "系统音频", "听一下这个视频", "听懂这个视频"))


def _video_transcription_arguments(text: str) -> dict:
    args = {"query": text, "sample_count": 8, "sample_interval_ms": 700, "transcribe": True}
    if any(token in text for token in ("系统音频", "电脑声音", "播放声音", "听一下", "听懂")):
        args["transcript_source"] = "system_audio"
    return args


def _is_code_task(text: str, lowered: str) -> bool:
    code_markers = ("代码", "项目", "仓库", "bug", "BUG", "测试", "编译", "构建", "文件", "README", "报错")
    action_markers = ("修复", "实现", "新增", "修改", "重构", "检查", "跑", "生成", "更新")
    return "codex" in lowered or (any(x in text for x in code_markers) and any(x in text for x in action_markers))


def _build_desktop_workflow(text: str, lowered: str) -> ToolRequest | None:
    has_open_verb = any(token in text for token in ("打开", "启动", "运行", "开启"))
    browser = _requested_browser(text, lowered)
    site = _requested_site(text, lowered)
    search_query = _parse_site_search_query(text)
    if not has_open_verb and not (site and search_query and _looks_like_explicit_site_search(text, lowered)):
        return None
    if browser and (site or search_query):
        arguments: dict[str, str] = {"workflow": "open_web_search", "browser": browser}
        if site:
            arguments["site"] = site
        if search_query:
            arguments["query"] = search_query
        return ToolRequest("computer.workflow", arguments, "打开浏览器和网页会操作当前电脑，需要确认。")
    if site:
        arguments = {"workflow": "open_web_search", "browser": browser or "edge", "site": site}
        if search_query:
            arguments["query"] = search_query
        return ToolRequest("computer.workflow", arguments, "打开网页会操作当前电脑，需要确认。")
    app = _requested_app(text, lowered)
    if app:
        return ToolRequest("computer.workflow", {"workflow": "open_app", "app": app}, "打开应用会操作当前电脑，需要确认。")
    return None


def _requested_browser(text: str, lowered: str) -> str:
    if "chrome" in lowered or "谷歌" in text:
        return "chrome"
    if "edge" in lowered or "浏览器" in text:
        return "edge"
    return ""


def _requested_site(text: str, lowered: str) -> str:
    if any(token in lowered for token in ("bilibili", "bilbil", "bili")) or any(token in text for token in ("B站", "b站", "哔哩哔哩")):
        return "bilibili"
    url = _parse_url(text)
    if url:
        return url
    return ""


def _looks_like_explicit_site_search(text: str, lowered: str) -> bool:
    bilibili_site = r"(?:B站|b站|哔哩哔哩|bilibili|bilbil|bili)"
    return re.search(rf"(?:在|到|去)?\s*{bilibili_site}\s*(?:里|上|中)?\s*{SEARCH_VERB_RE}", text, re.IGNORECASE) is not None


def _requested_app(text: str, lowered: str) -> str:
    app_aliases = (
        ("codex", "Codex"),
        ("edge", "Microsoft Edge"),
        ("chrome", "Google Chrome"),
        ("vscode", "Visual Studio Code"),
        ("vs code", "Visual Studio Code"),
        ("visual studio code", "Visual Studio Code"),
    )
    for token, app in app_aliases:
        if token in lowered:
            return app
    match = re.search(r"(?:打开|启动|运行|开启)\s*([^\s，。,.!?！？]+)", text)
    if not match:
        return ""
    candidate = match.group(1).strip()
    if candidate in {"网页", "浏览器", "网站", "页面", "了吗", "了没", "吗", "没"}:
        return ""
    return candidate[:80]


def _parse_site_search_query(text: str) -> str:
    match = re.search(rf"(?:并|然后|再)?(?:在[^，。,.!?！？]{{0,20}})?{SEARCH_VERB_RE}\s*([^\n]+)$", text)
    if not match:
        return ""
    query = re.sub(r"[。！？!?]+$", "", match.group(1)).strip(" ：:，,")
    return query[:160]


def _parse_browser_search_query(text: str) -> str:
    query = _parse_site_search_query(text)
    if query:
        return query
    return re.sub(rf"^(?:帮我|请|麻烦)?{SEARCH_VERB_RE}\s*", "", text).strip()[:160]


def _looks_like_search_command(text: str) -> bool:
    return re.search(rf"{SEARCH_VERB_RE}\s*\S+", text) is not None


def _parse_url(text: str) -> str:
    match = re.search(r"https?://[^\s，。]+", text, re.I)
    if match:
        return match.group(0)
    return ""


def _build_computer_action(text: str, lowered: str) -> ToolRequest | None:
    if _looks_like_hotkey(text, lowered):
        keys = _parse_hotkey(text)
        return ToolRequest("computer.hotkey", {"keys": keys}, "按下系统快捷键会影响当前前台应用，需要确认。")
    if _looks_like_type_text(text, lowered):
        return ToolRequest("computer.type_text", {"text": _parse_text_payload(text)}, "向当前前台应用输入文字，需要确认。")
    if _looks_like_scroll(text, lowered):
        direction = "up" if any(token in text for token in ("向上", "往上", "上滚", "上滑")) else "down"
        return ToolRequest("computer.scroll", {"direction": direction, "amount": 3}, "滚动当前前台应用，需要确认。")
    if _looks_like_double_click(text, lowered):
        x, y = _parse_coordinates(text)
        args: dict[str, int | str] = {}
        if x is not None and y is not None:
            args.update({"x": x, "y": y})
            return ToolRequest("computer.double_click", args, "双击当前屏幕会影响前台应用，需要确认。")
        return ToolRequest("vision.resolve_target", {"query": text, "action": "double_click"}, "先从当前画面中寻找候选区域。")
    if _looks_like_drag(text, lowered):
        coords = _parse_drag_coordinates(text)
        if coords:
            x1, y1, x2, y2 = coords
            return ToolRequest("computer.drag", {"x": x1, "y": y1, "end_x": x2, "end_y": y2}, "拖拽操作会影响前台应用，需要确认。")
    if _looks_like_click(text, lowered):
        x, y = _parse_coordinates(text)
        args = {}
        if x is not None and y is not None:
            args.update({"x": x, "y": y})
            return ToolRequest("computer.click", args, "点击当前屏幕会影响前台应用，需要确认。")
        return ToolRequest("vision.resolve_target", {"query": text, "action": "click"}, "先从当前画面中寻找候选区域。")
    return None


def _looks_like_click(text: str, lowered: str) -> bool:
    contextual_click = "点" in text and any(token in text for token in ("按钮", "那个", "这个", "右上", "左上", "右下", "左下", "开始", "登录", "任务"))
    return any(token in text for token in ("点击", "点一下", "鼠标点", "单击")) or contextual_click or "click" in lowered


def _looks_like_double_click(text: str, lowered: str) -> bool:
    return any(token in text for token in ("双击", "双点", "连点", "连击")) or "double" in lowered or "dblclick" in lowered


def _looks_like_drag(text: str, lowered: str) -> bool:
    return any(token in text for token in ("拖拽", "拖动", "拉动", "滑动到", "拖到")) or "drag" in lowered


def _looks_like_type_text(text: str, lowered: str) -> bool:
    return any(token in text for token in ("输入文字", "键入", "打字", "输入：", "输入:")) or "type " in lowered


def _looks_like_scroll(text: str, lowered: str) -> bool:
    return any(token in text for token in ("滚动", "下滑", "上滑", "往下", "往上")) or "scroll" in lowered


def _looks_like_hotkey(text: str, lowered: str) -> bool:
    return any(token in text for token in ("快捷键", "按下", "组合键")) or "hotkey" in lowered or "ctrl+" in lowered


def _parse_coordinates(text: str) -> tuple[int | None, int | None]:
    match = re.search(r"(?:x\s*[=:]\s*)?(\d{1,5})\s*[,， ]+\s*(?:y\s*[=:]\s*)?(\d{1,5})", text, re.IGNORECASE)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_drag_coordinates(text: str) -> tuple[int, int, int, int] | None:
    """Parse drag coordinates like '100,200 到 300,400' or '100,200 -> 300,400'."""
    match = re.search(r"(\d{1,5})\s*[,，]\s*(\d{1,5})\s*(?:到|->|→|拖到|拖至)\s*(\d{1,5})\s*[,，]\s*(\d{1,5})", text)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
    return None


def _parse_text_payload(text: str) -> str:
    match = re.search(r"[\"“'「](.+?)[\"”'」]", text)
    if match:
        return match.group(1).strip()
    marker_match = re.search(r"输入(?:文字)?[:：]?\s*(.+)$", text)
    return marker_match.group(1).strip() if marker_match else text


def _parse_hotkey(text: str) -> list[str]:
    match = re.search(r"((?:ctrl|control|alt|shift|win|meta|cmd|command)[+\s,，-]+[a-zA-Z0-9]+(?:[+\s,，-]+[a-zA-Z0-9]+)*)", text, re.IGNORECASE)
    if match:
        raw = match.group(1)
        return [part for part in re.split(r"[+\s,，-]+", raw) if part]
    return []


def _looks_like_open_app(text: str, lowered: str) -> bool:
    if _is_execution_status_question(text):
        return False
    return any(token in text for token in ("打开", "启动", "运行")) or lowered.startswith("open ")


def _parse_app_name(text: str) -> str:
    cleaned = text
    for prefix in ("帮我", "请", "一键", "打开", "启动", "运行", "open"):
        cleaned = cleaned.replace(prefix, "")
    cleaned = re.sub(r"[。！!?？，\s]", "", cleaned)
    return cleaned.strip()
