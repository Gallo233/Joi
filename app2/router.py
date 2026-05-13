from __future__ import annotations

import re

from app2.events import RouteDecision, RouteKind


ENGINEERING_MARKERS = (
    "代码",
    "项目",
    "仓库",
    "文件",
    "目录",
    "测试",
    "编译",
    "构建",
    "功能",
    "bug",
    "BUG",
    "报错",
    "异常",
    "脚本",
    "入口",
    "重构",
    "实现",
    "修复",
    "检查",
    "跑一下",
    "跑测试",
)

ACTION_MARKERS = (
    "修复",
    "改",
    "修改",
    "实现",
    "新增",
    "加",
    "删除",
    "精简",
    "重构",
    "排查",
    "检查",
    "跑",
    "测试",
    "启动",
    "生成",
    "更新",
)

CHAT_ONLY_PATTERNS = (
    r"^\s*(你好|您好|hello|hi|嗨|在吗|早|晚上好|下午好)[。！!？? ]*$",
    r"^\s*(谢谢|辛苦了|可以|好的|ok|OK)[。！! ]*$",
)


def route_user_text(text: str) -> RouteDecision:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return RouteDecision(RouteKind.CHAT, "", "empty")

    lowered = cleaned.casefold()
    if lowered.startswith(("/chat ", "闲聊 ")):
        return RouteDecision(RouteKind.CHAT, cleaned.split(maxsplit=1)[1], "forced_chat")
    if lowered.startswith(("/codex ", "codex ")):
        return RouteDecision(RouteKind.CODEX, cleaned.split(maxsplit=1)[1], "forced_codex")

    if any(re.match(pattern, cleaned, re.I) for pattern in CHAT_ONLY_PATTERNS):
        return RouteDecision(RouteKind.CHAT, cleaned, "greeting")

    has_file_ref = bool(re.search(r"[\\./\w\u4e00-\u9fff-]+\.(?:py|md|yaml|yml|json|txt|toml|bat|ps1)", cleaned))
    has_engineering = any(marker in cleaned for marker in ENGINEERING_MARKERS)
    has_action = any(marker in cleaned for marker in ACTION_MARKERS)
    mentions_codex = "codex" in lowered or "交给你" in cleaned or "你来做" in cleaned

    if mentions_codex or has_file_ref or (has_engineering and has_action):
        return RouteDecision(RouteKind.CODEX, cleaned, "engineering_task")

    return RouteDecision(RouteKind.CHAT, cleaned, "default_chat")

