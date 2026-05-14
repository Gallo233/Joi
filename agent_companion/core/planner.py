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
    if _is_watch_task(text):
        return AgentPlan(
            task_id=task_id,
            user_text=text,
            intent="watch_together",
            steps=[ToolRequest("observe.screen", {"query": text}, "观察当前窗口或屏幕内容并生成陪看摘要。")],
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
    return any(token in text for token in ("陪我看", "看电影", "看视频", "一起看", "当前画面", "刚刚发生了什么", "这段剧情"))


def _is_code_task(text: str, lowered: str) -> bool:
    code_markers = ("代码", "项目", "仓库", "bug", "BUG", "测试", "编译", "构建", "文件", "README", "报错")
    action_markers = ("修复", "实现", "新增", "修改", "重构", "检查", "跑", "生成", "更新")
    return "codex" in lowered or (any(x in text for x in code_markers) and any(x in text for x in action_markers))
