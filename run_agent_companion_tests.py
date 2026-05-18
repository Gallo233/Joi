from __future__ import annotations

import os
import tempfile
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.config import LlmConfig, ModelEndpoint, ModelRouter, load_app_config
from agent_companion.core.planner import build_plan
from agent_companion.core.schemas import EventType, ToolRequest
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.vision.summarizer import MockSummarizer, OpenAIVisionSummarizer, VisionSummary
from agent_companion.core.voice import safe_voice_line


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


class FakeVisionObserver:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        rel = "data/agent_companion/vision/sample.png"
        return VisionObservation(
            target=target,
            screenshot_path=self.workspace / rel,
            screenshot_rel=rel,
            width=1280,
            height=720,
            title="Joi Test Window",
            window_handle=1234,
            query=query,
        )


def main() -> int:
    workspace = Path(__file__).resolve().parent
    os.environ["AGENT_COMPANION_DISABLE_LLM"] = "1"
    os.environ["AGENT_COMPANION_BROWSER_STUB"] = "1"
    assert_true(build_plan("修复这个项目 bug 并跑测试").intent == "coding", "coding route failed")
    watch_plan = build_plan("陪我看这个视频")
    assert_true(watch_plan.intent == "watch_together", "watch route failed")
    assert_true(watch_plan.steps[0].name == "observe.screen", "watch route should use screen observation")
    assert_true(build_plan("帮我刷鸣潮日常").intent == "game_assist", "game route failed")
    blocked = safe_voice_line('{"task_id":"codex2-abcdef1234","path":"data/app.log"}')
    assert_true("codex2-" not in blocked.text and "{" not in blocked.text, "voice sanitizer failed")
    blocked_tool_name = safe_voice_line("codex.run 需要确认")
    assert_true("codex.run" not in blocked_tool_name.text, "voice leaked raw tool name")

    screen_tool = ScreenObserveTool(workspace, FakeVisionObserver(workspace))
    screen_result = screen_tool.run(ToolRequest("observe.screen", {"query": "陪我看当前画面", "target": "fullscreen"}))
    assert_true(screen_result.ok, "screen observation should succeed with fake observer")
    assert_true(screen_result.agent_state["observation"]["target"] == "fullscreen", "screen target not preserved")
    assert_true(screen_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "screenshot artifact missing")
    assert_true("sample.png" not in screen_result.voice_line.text, "voice should not read screenshot path")

    # VisionSummarizer: MockSummarizer
    mock_summarizer = MockSummarizer()
    mock_vs = mock_summarizer.summarize(FakeVisionObserver(workspace).observe())
    assert_true("画面摘要" in mock_vs.text, "MockSummarizer should produce summary text")
    assert_true(mock_vs.model == "mock", "MockSummarizer model should be 'mock'")

    # ScreenObserveTool with summarizer
    tool_with_summarizer = ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=MockSummarizer())
    result_with_summary = tool_with_summarizer.run(ToolRequest("observe.screen", {"query": "看看画面", "target": "fullscreen"}))
    assert_true(result_with_summary.ok, "screen observation with summarizer should succeed")
    assert_true("vision_summary" in result_with_summary.agent_state, "agent_state should include vision_summary")
    assert_true("画面摘要" in result_with_summary.agent_state["vision_summary"], "vision_summary should contain summary text")
    assert_true("画面摘要" in result_with_summary.display_card.summary, "card summary should use vision summary")
    assert_true("视觉摘要" in result_with_summary.display_card.body, "card body should include visual summary")

    # ScreenObserveTool with failing summarizer (non-fatal)
    class FailingSummarizer:
        def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
            raise RuntimeError("vision model unavailable")

    tool_failing = ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=FailingSummarizer())
    result_failing = tool_failing.run(ToolRequest("observe.screen", {"query": "看看画面", "target": "fullscreen"}))
    assert_true(result_failing.ok, "screen observation should succeed even if summarizer fails")
    assert_true("vision_summary_error" in result_failing.agent_state, "agent_state should include vision_summary_error on failure")
    assert_true("vision_summary" not in result_failing.agent_state, "agent_state should not include vision_summary on failure")
    assert_true("摘要" in result_failing.voice_line.text, "failure voice should mention summary unavailable")
    assert_true("sample.png" not in result_failing.voice_line.text, "failure voice should not read screenshot path")

    # ModelRouter: text fallback
    base_llm = LlmConfig(
        provider="openai_compatible", use_mock=False,
        base_url="https://api.base.com/v1", model="gpt-4", api_key="sk-base",
    )
    router = ModelRouter(base_llm)
    text_ep = router.resolve("text")
    assert_true(text_ep.base_url == "https://api.base.com/v1", "text should use base_url")
    assert_true(text_ep.model == "gpt-4", "text should use base model")
    assert_true(text_ep.api_key == "sk-base", "text should use base api_key")

    # ModelRouter: vision falls back to text when not configured
    vision_ep = router.resolve("vision")
    assert_true(vision_ep.base_url == "https://api.base.com/v1", "unconfigured vision should fall back to base")

    # ModelRouter: vision uses dedicated config when configured
    vision_llm = LlmConfig(
        provider="openai_compatible", use_mock=False,
        base_url="https://api.base.com/v1", model="gpt-4", api_key="sk-base",
        vision_enabled=True, vision_base_url="https://api.vision.com/v1",
        vision_model="gpt-4o", vision_api_key="sk-vision",
    )
    vision_ep2 = ModelRouter(vision_llm).resolve("vision")
    assert_true(vision_ep2.base_url == "https://api.vision.com/v1", "vision should use vision_base_url")
    assert_true(vision_ep2.model == "gpt-4o", "vision should use vision_model")
    assert_true(vision_ep2.api_key == "sk-vision", "vision should use vision_api_key")

    # ModelRouter: expression uses dedicated config when configured
    expr_llm = LlmConfig(
        provider="openai_compatible", use_mock=False,
        base_url="https://api.base.com/v1", model="gpt-4", api_key="sk-base",
        expression_enabled=True, expression_model="gpt-4.1-mini",
    )
    expr_ep = ModelRouter(expr_llm).resolve("expression")
    assert_true(expr_ep.base_url == "https://api.base.com/v1", "expression should fall back base_url")
    assert_true(expr_ep.model == "gpt-4.1-mini", "expression should use expression_model")
    assert_true(expr_ep.api_key == "sk-base", "expression should fall back api_key")

    # ModelRouter: expression falls back when not enabled
    expr_ep2 = ModelRouter(base_llm).resolve("expression")
    assert_true(expr_ep2.model == "gpt-4", "unconfigured expression should fall back to base model")

    # Config path: _build_vision_summarizer reads from workspace / config.yaml
    tmpdir = tempfile.mkdtemp()
    try:
        tmp = Path(tmpdir)
        (tmp / "config.yaml").write_text(
            "llm:\n"
            "  provider: openai_compatible\n"
            "  use_mock: false\n"
            "  base_url: https://api.base.com/v1\n"
            "  model: gpt-4\n"
            "  api_key: sk-test\n"
            "  vision_enabled: true\n"
            "  vision_base_url: https://api.vision.com/v1\n"
            "  vision_model: gpt-4o\n"
            "  vision_api_key: sk-vision\n"
            "characters:\n"
            "  - name: Test\n"
            "    color: '#fff'\n"
            "    setting: test\n",
            encoding="utf-8",
        )
        char_dir = tmp / "agent_companion" / "config"
        char_dir.mkdir(parents=True)
        (char_dir / "default_character.yaml").write_text(
            "id: test\nname: Test\npersona: test\ntone: test\n",
            encoding="utf-8",
        )
        tmp_app = AgentCompanionApp(tmp)
        observe_tool = tmp_app.tools._tools.get("observe.screen")
        assert_true(observe_tool is not None, "observe.screen tool should be registered")
        assert_true(observe_tool.summarizer is not None, "summarizer should be created when root config.yaml has vision enabled")
        assert_true(isinstance(observe_tool.summarizer, OpenAIVisionSummarizer), "summarizer should be OpenAIVisionSummarizer")
        assert_true(observe_tool.summarizer.model == "gpt-4o", "summarizer should use vision_model")
        assert_true(observe_tool.summarizer.base_url == "https://api.vision.com/v1", "summarizer should use vision_base_url")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

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
