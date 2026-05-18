from __future__ import annotations

import os
import inspect
import tempfile
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.computer_use import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.config import LlmConfig, ModelEndpoint, ModelRouter, load_app_config
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import EventType, ToolRequest
from agent_companion.core.tools.computer import ComputerActionTool
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


class FakeComputerBackend:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.actions: list[ComputerAction] = []

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        return ComputerObservation.from_vision(FakeVisionObserver(self.workspace).observe(target=target, query=query))

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        self.actions.append(action)
        return ComputerUseResult(ok=True, action=action, summary=_fake_action_summary(action.action_type))


def _fake_action_summary(action_type: str) -> str:
    return {
        "click": "点击了指定位置。",
        "type_text": "输入了一段文字。",
        "scroll": "滚动了当前画面。",
        "hotkey": "按下了快捷键。",
    }.get(action_type, "完成了电脑操作。")


def _approval_payload(events) -> dict:
    for event in events:
        if event.type == EventType.APPROVAL_REQUIRED:
            approval = event.agent_state.get("approval")
            if isinstance(approval, dict):
                return approval
    return {}


def main() -> int:
    workspace = Path(__file__).resolve().parent
    os.environ["AGENT_COMPANION_DISABLE_LLM"] = "1"
    os.environ["AGENT_COMPANION_BROWSER_STUB"] = "1"
    assert_true("approved" not in inspect.signature(AgentCompanionApp.handle_user_text).parameters, "user message should not accept approval bypass")
    assert_true(build_plan("修复这个项目 bug 并跑测试").intent == "coding", "coding route failed")
    watch_plan = build_plan("陪我看这个视频")
    assert_true(watch_plan.intent == "watch_together", "watch route failed")
    assert_true(watch_plan.steps[0].name == "observe.screen", "watch route should use screen observation")
    watch_followup_plan = build_plan("你看到了什么")
    assert_true(watch_followup_plan.intent == "watch_followup", "watch follow-up route failed")
    assert_true(watch_followup_plan.steps[0].name == "watch.recall", "watch follow-up should reuse context")
    click_plan = build_plan("点击 100,200")
    assert_true(click_plan.intent == "computer_use", "computer click route failed")
    assert_true(click_plan.steps[0].name == "computer.click", "click should use computer.click")
    assert_true(click_plan.steps[0].arguments.get("x") == 100, "click x coordinate not parsed")
    type_plan = build_plan("输入文字：你好世界")
    assert_true(type_plan.steps[0].name == "computer.type_text", "type route failed")
    assert_true(type_plan.steps[0].arguments.get("text") == "你好世界", "Chinese type text should be preserved")
    assert_true(build_plan("向下滚动").steps[0].name == "computer.scroll", "scroll route failed")
    assert_true(build_plan("按下 Ctrl+L 快捷键").steps[0].name == "computer.hotkey", "hotkey route failed")
    assert_true(build_plan("帮我刷鸣潮日常").intent == "game_assist", "game route failed")
    blocked = safe_voice_line('{"task_id":"codex2-abcdef1234","path":"data/app.log"}')
    assert_true("codex2-" not in blocked.text and "{" not in blocked.text, "voice sanitizer failed")
    blocked_tool_name = safe_voice_line("codex.run 需要确认")
    assert_true("codex.run" not in blocked_tool_name.text, "voice leaked raw tool name")
    blocked_coordinate = safe_voice_line("我点击了 100,200")
    assert_true("100,200" not in blocked_coordinate.text, "voice leaked coordinates")

    screen_tool = ScreenObserveTool(workspace, FakeVisionObserver(workspace))
    screen_result = screen_tool.run(ToolRequest("observe.screen", {"query": "陪我看当前画面", "target": "fullscreen"}))
    assert_true(screen_result.ok, "screen observation should succeed with fake observer")
    assert_true("computer_observation" in screen_result.agent_state, "screen observation should use computer observation chain")
    assert_true(screen_result.agent_state["observation"]["target"] == "fullscreen", "screen target not preserved")
    assert_true(screen_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "screenshot artifact missing")
    assert_true("sample.png" not in screen_result.voice_line.text, "voice should not read screenshot path")

    policy = PolicyGate()
    click_decision = policy.classify(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true(click_decision.requires_approval, "computer.click should require approval")
    assert_true(policy.classify(ToolRequest("computer.click", {"x": 100, "y": 200}), approved=True).allowed, "approved computer.click should be allowed")
    public_payload = policy.public_payload(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true("100" not in str(public_payload), "policy preview should not expose raw click coordinates")

    fake_backend = FakeComputerBackend(workspace)
    click_tool = ComputerActionTool(workspace, "computer.click", "click", fake_backend)
    click_result = click_tool.run(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true(click_result.ok, "computer.click tool should succeed with fake backend")
    assert_true(click_result.agent_state["computer_use"]["action"]["x"] == 100, "computer action state should keep x for planner")
    assert_true("observation" in click_result.agent_state["computer_use"], "computer action should include after observation")
    assert_true(click_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "computer action should expose after screenshot artifact")
    assert_true("100" not in click_result.display_card.summary, "computer card summary should be friendly")
    assert_true("100" not in click_result.voice_line.text, "computer voice should not read coordinates")

    type_tool = ComputerActionTool(workspace, "computer.type_text", "type_text", fake_backend)
    type_result = type_tool.run(ToolRequest("computer.type_text", {"text": "hello world"}))
    assert_true(type_result.ok, "computer.type_text tool should succeed with fake backend")
    assert_true("hello" not in type_result.display_card.summary, "type summary should not echo raw text")

    hotkey_tool = ComputerActionTool(workspace, "computer.hotkey", "hotkey", fake_backend)
    hotkey_result = hotkey_tool.run(ToolRequest("computer.hotkey", {"keys": ["Ctrl", "L"]}))
    assert_true(hotkey_result.ok, "computer.hotkey tool should succeed with fake backend")
    assert_true("Ctrl" not in hotkey_result.voice_line.text, "hotkey voice should not read key names")

    # VisionSummarizer: MockSummarizer
    mock_summarizer = MockSummarizer()
    mock_vs = mock_summarizer.summarize(FakeVisionObserver(workspace).observe())
    assert_true("画面摘要" in mock_vs.text, "MockSummarizer should produce summary text")
    assert_true(mock_vs.model == "mock", "MockSummarizer model should be 'mock'")

    # ScreenObserveTool with summarizer
    tool_with_summarizer = ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=MockSummarizer())
    result_with_summary = tool_with_summarizer.run(ToolRequest("observe.screen", {"query": "看看画面", "target": "fullscreen"}))
    assert_true(result_with_summary.ok, "screen observation with summarizer should succeed")
    assert_true(result_with_summary.agent_state["model_status"] == "ok", "screen observation should mark vision model ok")
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

    tool_without_summarizer = ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=None)
    result_without_summary = tool_without_summarizer.run(ToolRequest("observe.screen", {"query": "看看画面", "target": "fullscreen"}))
    assert_true(result_without_summary.ok, "screen observation should succeed without vision model")
    assert_true(result_without_summary.agent_state["model_status"] == "unconfigured", "screen observation should mark missing vision model")
    assert_true("配置视觉模型" in result_without_summary.display_card.summary, "missing vision model should be visible in card")
    assert_true("sample.png" not in result_without_summary.voice_line.text, "missing vision voice should not read screenshot path")

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

    empty_watch_events = AgentCompanionApp(workspace).handle_user_text("你看到了什么")
    assert_true(any(event.agent_state.get("tool") == "watch.recall" for event in empty_watch_events), "empty watch follow-up should use recall")
    assert_true(not any(event.type == EventType.TASK_FAILED for event in empty_watch_events), "empty watch follow-up should answer naturally, not fail")
    assert_true(not any(event.type == EventType.TASK_COMPLETED for event in empty_watch_events), "watch follow-up should not add generic task completion voice")

    watch_app = AgentCompanionApp(workspace)
    watch_app.tools.register(ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=MockSummarizer()))
    watch_events = watch_app.handle_user_text("陪我看当前画面")
    assert_true(any(event.agent_state.get("tool") == "observe.screen" for event in watch_events), "watch should observe screen first")
    assert_true(watch_app.watch_session.has_context(), "watch session should remember visual context")
    recall_events = watch_app.handle_user_text("你看到了什么")
    assert_true(any(event.agent_state.get("tool") == "watch.recall" for event in recall_events), "watch follow-up should use recall tool")
    assert_true(not any(event.agent_state.get("tool") == "observe.screen" for event in recall_events), "watch follow-up should not repeat screen capture")
    recall_cards = [event for event in recall_events if event.agent_state.get("tool") == "watch.recall"]
    assert_true("画面摘要" in recall_cards[-1].display_card.summary, "watch recall should answer from visual summary")
    assert_true(recall_cards[-1].display_card.artifacts == ["data/agent_companion/vision/sample.png"], "watch recall should show recent screenshot artifact")
    assert_true(all("sample.png" not in event.voice_line.text for event in recall_events), "watch recall voice should not read artifact path")

    fallback_watch_app = AgentCompanionApp(workspace)
    fallback_watch_app.tools.register(ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=None))
    fallback_watch_app.handle_user_text("陪我看当前窗口")
    fallback_recall = fallback_watch_app.handle_user_text("这个页面讲什么")
    fallback_cards = [event for event in fallback_recall if event.agent_state.get("tool") == "watch.recall"]
    assert_true(fallback_cards, "watch fallback should still create recall card")
    assert_true("视觉模型" in fallback_cards[-1].display_card.summary, "watch fallback should mention vision model config")

    previous = os.environ.get("AGENT_COMPANION_CODEX_BIN")
    os.environ["AGENT_COMPANION_CODEX_BIN"] = str(workspace / "missing-codex.exe")
    try:
        app = AgentCompanionApp(workspace)
        approval_events = app.handle_user_text("修复这个项目 bug 并跑测试")
        approval = _approval_payload(approval_events)
        assert_true(bool(approval.get("approval_id")), "coding approval should include approval_id")
        events = app.resolve_approval(str(approval["approval_id"]), approved=True)
        assert_true(any(event.type == EventType.TOOL_FAILED for event in events), "missing Codex should fail")
        assert_true(all("codex2-" not in event.voice_line.text for event in events), "voice leaked task id")
    finally:
        if previous is None:
            os.environ.pop("AGENT_COMPANION_CODEX_BIN", None)
        else:
            os.environ["AGENT_COMPANION_CODEX_BIN"] = previous

    app = AgentCompanionApp(workspace)
    computer_events = app.handle_user_text("点击 100,200")
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED for event in computer_events), "computer action should require approval")
    computer_approval = _approval_payload(computer_events)
    assert_true(str(computer_approval.get("approval_id", "")).startswith("approval-"), "computer approval should include approval_id")
    assert_true(computer_approval.get("tool") == "computer.click", "computer approval should bind tool")
    assert_true(computer_approval.get("task_id") in {event.task_id for event in computer_events}, "computer approval should bind task_id")
    assert_true(computer_approval.get("step_index") == 0, "computer approval should bind step index")
    assert_true(len(str(computer_approval.get("arguments_hash", ""))) == 16, "computer approval should bind arguments hash")
    bypass = app.resolve_approval(str(computer_approval.get("task_id")), approved=True)
    assert_true(not bypass, "task_id must not work as approval id")
    refused = app.resolve_approval(str(computer_approval["approval_id"]), approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in refused), "approval refusal should cancel computer action")
    reused = app.resolve_approval(str(computer_approval["approval_id"]), approved=True)
    assert_true(not reused, "approval id should be single-use")

    app = AgentCompanionApp(workspace)
    approval_events = app.handle_user_text("帮我刷鸣潮日常")
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED for event in approval_events), "game task should require approval")
    game_approval = _approval_payload(approval_events)
    cancelled = app.resolve_approval(str(game_approval["approval_id"]), approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in cancelled), "approval refusal should cancel task")
    duplicate = app.resolve_approval(str(game_approval["approval_id"]), approved=False)
    assert_true(not duplicate, "duplicate approval responses should be ignored")
    print("agent_companion tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
