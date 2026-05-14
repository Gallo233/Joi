from __future__ import annotations

import os
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.planner import build_plan
from agent_companion.core.schemas import EventType
from agent_companion.core.voice import safe_voice_line


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    workspace = Path(__file__).resolve().parent
    os.environ["AGENT_COMPANION_DISABLE_LLM"] = "1"
    os.environ["AGENT_COMPANION_BROWSER_STUB"] = "1"
    assert_true(build_plan("修复这个项目 bug 并跑测试").intent == "coding", "coding route failed")
    assert_true(build_plan("陪我看这个视频").intent == "watch_together", "watch route failed")
    assert_true(build_plan("帮我刷鸣潮日常").intent == "game_assist", "game route failed")
    blocked = safe_voice_line('{"task_id":"codex2-abcdef1234","path":"data/app.log"}')
    assert_true("codex2-" not in blocked.text and "{" not in blocked.text, "voice sanitizer failed")
    blocked_tool_name = safe_voice_line("codex.run 需要确认")
    assert_true("codex.run" not in blocked_tool_name.text, "voice leaked raw tool name")

    chat_app = AgentCompanionApp(workspace)
    chat_events = chat_app.handle_user_text("你好")
    assert_true(any(event.display_card.title == "对话" for event in chat_events), "chat should produce a dialogue card")
    assert_true(not any(event.type == EventType.PLAN_CREATED for event in chat_events), "chat should not show plan events")
    assert_true(not any(event.type == EventType.TASK_COMPLETED for event in chat_events), "chat should not show task completion")

    previous = os.environ.get("AGENT_COMPANION_CODEX_BIN")
    os.environ["AGENT_COMPANION_CODEX_BIN"] = str(workspace / "missing-codex.exe")
    try:
        app = AgentCompanionApp(workspace)
        events = app.handle_user_text("修复这个项目 bug 并跑测试", approved=True)
        assert_true(any(event.type == EventType.TOOL_FAILED for event in events), "missing Codex should fail")
        assert_true(all("codex2-" not in event.voice_line.text for event in events), "voice leaked task id")
    finally:
        if previous is None:
            os.environ.pop("AGENT_COMPANION_CODEX_BIN", None)
        else:
            os.environ["AGENT_COMPANION_CODEX_BIN"] = previous

    app = AgentCompanionApp(workspace)
    approval_events = app.handle_user_text("帮我刷鸣潮日常", approved=False)
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED for event in approval_events), "game task should require approval")
    pending = [event for event in approval_events if event.type == EventType.APPROVAL_REQUIRED]
    cancelled = app.resolve_approval(pending[-1].task_id, approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in cancelled), "approval refusal should cancel task")
    duplicate = app.resolve_approval(pending[-1].task_id, approved=False)
    assert_true(not duplicate, "duplicate approval responses should be ignored")
    print("agent_companion tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
