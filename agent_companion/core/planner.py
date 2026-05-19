from __future__ import annotations

import re
import uuid

from agent_companion.core.schemas import AgentPlan, ToolRequest


def build_plan(user_text: str) -> AgentPlan:
    text = " ".join((user_text or "").strip().split())
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    lowered = text.casefold()

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
    if _is_watch_followup(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_followup",
            steps=[ToolRequest("watch.recall", {"query": text}, "优先使用最近的陪看视觉上下文回答，不重复截图。")],
        )
    if _is_watch_task(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_together",
            steps=[ToolRequest("observe.screen", {"query": text}, "观察当前窗口或屏幕内容并生成陪看摘要。")],
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
    if "搜索" in text or "搜一下" in text or "查找" in text:
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="browser",
            steps=[ToolRequest("browser.search", {"query": text}, "使用本地浏览器搜索并观察结果。")],
        )
    if "网页" in text or "浏览器" in text or re.search(r"https?://", text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="browser",
            steps=[ToolRequest("browser.observe", {"query": text}, "观察浏览器页面。")],
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
    return any(token in text for token in ("陪我看", "看电影", "看视频", "一起看", "当前画面", "当前窗口", "这段剧情"))


def _is_watch_followup(text: str) -> bool:
    return any(token in text for token in ("刚刚发生了什么", "你看到了什么", "你刚才看到了什么", "这个页面讲什么", "刚才的画面", "刚才看到的"))


def _is_code_task(text: str, lowered: str) -> bool:
    code_markers = ("代码", "项目", "仓库", "bug", "BUG", "测试", "编译", "构建", "文件", "README", "报错")
    action_markers = ("修复", "实现", "新增", "修改", "重构", "检查", "跑", "生成", "更新")
    return "codex" in lowered or (any(x in text for x in code_markers) and any(x in text for x in action_markers))


def _build_computer_action(text: str, lowered: str) -> ToolRequest | None:
    if _looks_like_hotkey(text, lowered):
        keys = _parse_hotkey(text)
        return ToolRequest("computer.hotkey", {"keys": keys}, "按下系统快捷键会影响当前前台应用，需要确认。")
    if _looks_like_type_text(text, lowered):
        return ToolRequest("computer.type_text", {"text": _parse_text_payload(text)}, "向当前前台应用输入文字，需要确认。")
    if _looks_like_scroll(text, lowered):
        direction = "up" if any(token in text for token in ("向上", "往上", "上滚", "上滑")) else "down"
        return ToolRequest("computer.scroll", {"direction": direction, "amount": 3}, "滚动当前前台应用，需要确认。")
    if _looks_like_click(text, lowered):
        x, y = _parse_coordinates(text)
        args: dict[str, int | str] = {}
        if x is not None and y is not None:
            args.update({"x": x, "y": y})
            return ToolRequest("computer.click", args, "点击当前屏幕会影响前台应用，需要确认。")
        return ToolRequest("vision.resolve_target", {"query": text, "action": "click"}, "先从当前画面中寻找候选区域。")
    return None


def _looks_like_click(text: str, lowered: str) -> bool:
    return any(token in text for token in ("点击", "点一下", "鼠标点", "单击")) or ("点" in text and "按钮" in text) or "click" in lowered


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
