from __future__ import annotations

import os
import inspect
import tempfile
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.computer_use import ComputerAction, ComputerObservation, ComputerUseResult, verify_post_action
from agent_companion.core.config import LlmConfig, ModelEndpoint, ModelRouter, load_app_config
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import EventType, ToolRequest
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.speech_input import AsrResult, AsrRuntimeState, MockAsrProvider, OpenAICompatibleAsrProvider, build_asr_provider
from agent_companion.core.tools.computer import ComputerActionTool
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.tools.watch import WatchRecallTool
from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock, PytesseractOcrExtractor, UnavailableOcrExtractor
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.vision.summarizer import MockSummarizer, OpenAIVisionSummarizer, VisionSummary
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch import WatchFrame


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


def _fake_computer_observation(
    workspace: Path,
    rel: str = "data/agent_companion/vision/sample.png",
    title: str = "Joi Test Window",
    ocr_text: list[str] | None = None,
    ocr_status: str | None = None,
    width: int = 1280,
    height: int = 720,
) -> ComputerObservation:
    ocr = {}
    if ocr_text is not None or ocr_status is not None:
        ocr = {
            "status": ocr_status or "success",
            "summary": "mock OCR",
            "text_blocks": [{"text": text, "bbox": [0, 0, 40, 20], "confidence": 0.9} for text in (ocr_text or [])],
        }
    return ComputerObservation(
        target="active_window",
        screenshot_path=workspace / rel,
        screenshot_rel=rel,
        width=width,
        height=height,
        title=title,
        window_handle=1234,
        query="test",
        ocr=ocr,
    )


class FakeComputerBackend:
    def __init__(
        self,
        workspace: Path,
        observations: list[ComputerObservation] | None = None,
        fail_on_observe_calls: set[int] | None = None,
    ) -> None:
        self.workspace = workspace
        self.actions: list[ComputerAction] = []
        self.observations = list(observations or [])
        self.fail_on_observe_calls = set(fail_on_observe_calls or set())
        self.observe_calls = 0

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        self.observe_calls += 1
        if self.observe_calls in self.fail_on_observe_calls:
            raise RuntimeError("mock observe failed")
        if self.observations:
            return self.observations.pop(0)
        return ComputerObservation.from_vision(FakeVisionObserver(self.workspace).observe(target=target, query=query))

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        self.actions.append(action)
        return ComputerUseResult(ok=True, action=action, summary=_fake_action_summary(action.action_type))


class FakeOcrExtractor:
    def __init__(self, result: OcrResult) -> None:
        self.result = result

    def extract(self, image_path: Path) -> OcrResult:
        return self.result


class SequenceOcrExtractor:
    def __init__(self, results: list[OcrResult]) -> None:
        self.results = list(results)
        self.calls: list[Path] = []

    def extract(self, image_path: Path) -> OcrResult:
        self.calls.append(image_path)
        if self.results:
            return self.results.pop(0)
        return OcrResult("success", "没有识别到清晰文字。", [])


class RaisingOcrExtractor:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def extract(self, image_path: Path) -> OcrResult:
        raise self.exc


def _fake_action_summary(action_type: str) -> str:
    return {
        "click": "点击了指定位置。",
        "type_text": "输入了一段文字。",
        "scroll": "滚动了当前画面。",
        "hotkey": "按下了快捷键。",
    }.get(action_type, "完成了电脑操作。")


class FakeWatchAnswerer:
    last_used_model = True

    def answer(self, question: str, frames: list[WatchFrame]) -> tuple[str, str]:
        summary = frames[0].summary if frames else "没有画面"
        return f"自然回答会针对“{question}”：{summary}", "llm_answer"


class FailingAsrProvider:
    def __init__(self, error: str = "asr_failed") -> None:
        self.error = error
        self.called = False

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        self.called = True
        return AsrResult("", 0.0, "failing", self.error)


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

    memory_dir = Path(tempfile.mkdtemp())
    try:
        memory = MemoryStore(memory_dir / "memory.sqlite3")
        memory.remember("normal", "可长期保存")
        memory.remember("ephemeral", "临时画面摘要", ephemeral=True)
        memory.remember("sensitive", "敏感屏幕内容", sensitive=True)
        recent_memory = memory.recent(10)
        assert_true(len(recent_memory) == 1 and recent_memory[0]["text"] == "可长期保存", "ephemeral/sensitive memories should not be long-term by default")
    finally:
        import shutil
        shutil.rmtree(memory_dir, ignore_errors=True)

    screen_tool = ScreenObserveTool(workspace, FakeVisionObserver(workspace), ocr=UnavailableOcrExtractor())
    screen_result = screen_tool.run(ToolRequest("observe.screen", {"query": "陪我看当前画面", "target": "fullscreen"}))
    assert_true(screen_result.ok, "screen observation should succeed with fake observer")
    assert_true("computer_observation" in screen_result.agent_state, "screen observation should use computer observation chain")
    assert_true(screen_result.agent_state["observation"]["target"] == "fullscreen", "screen target not preserved")
    assert_true(screen_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "screenshot artifact missing")
    assert_true("sample.png" not in screen_result.voice_line.text, "voice should not read screenshot path")
    assert_true(screen_result.agent_state["observation"]["ocr"]["status"] == "unavailable", "OCR should have safe unavailable fallback")
    assert_true("OCR：" in screen_result.display_card.body, "screen card should include OCR status")

    unavailable_ocr = UnavailableOcrExtractor("OCR 依赖未安装，暂时只能保存截图。")
    unavailable_result = unavailable_ocr.extract(workspace / "missing.png")
    assert_true(unavailable_result.status == "unavailable", "Unavailable OCR should report unavailable")
    assert_true("OCR" in unavailable_result.detail_text(), "Unavailable OCR should have friendly detail")

    mock_ocr = OcrResult(
        "success",
        "识别到 3 段可见文字，包含：登录、设置、开始任务。",
        [
            OcrTextBlock("登录", (10, 20, 48, 20), 0.98),
            OcrTextBlock("设置", (90, 20, 48, 20), 0.96),
            OcrTextBlock("开始任务", (180, 240, 96, 32), 0.93),
        ],
    )
    ocr_tool = ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=MockSummarizer(), ocr=FakeOcrExtractor(mock_ocr))
    ocr_result = ocr_tool.run(ToolRequest("observe.screen", {"query": "看看按钮", "target": "fullscreen"}))
    ocr_state = ocr_result.agent_state["observation"]["ocr"]
    assert_true(ocr_state["status"] == "success", "mock OCR should be included in observation state")
    assert_true(ocr_state["text_blocks"][0]["text"] == "登录", "OCR text block should preserve visible text")
    assert_true(ocr_state["text_blocks"][0]["bbox"] == [10, 20, 48, 20], "OCR text block should include bbox for planner")
    assert_true("开始任务" in ocr_result.display_card.body, "OCR snippets should be visible in task details")
    forbidden_ocr_voice = ["10", "20", "开始任务", "sample.png", "{"]
    assert_true(not any(fragment in ocr_result.voice_line.text for fragment in forbidden_ocr_voice), "OCR voice should not read raw OCR details")

    timeout_tool = ScreenObserveTool(
        workspace,
        FakeVisionObserver(workspace),
        summarizer=MockSummarizer(),
        ocr=RaisingOcrExtractor(TimeoutError("pytesseract timed out at C:\\secret\\ocr.png")),
    )
    timeout_result = timeout_tool.run(ToolRequest("observe.screen", {"query": "看看页面文字", "target": "fullscreen"}))
    timeout_ocr = timeout_result.agent_state["observation"]["ocr"]
    assert_true(timeout_result.ok, "OCR timeout should not fail screen observation")
    assert_true(timeout_ocr["status"] == "failed" and timeout_ocr["error"] == "ocr_timeout", "OCR timeout should be sanitized")
    assert_true("OCR 等太久" in timeout_result.display_card.body, "OCR timeout should be visible in card")
    forbidden_timeout_voice = ["ocr_timeout", "C:\\", ".png", "{", "10", "20"]
    assert_true(not any(fragment in timeout_result.voice_line.text for fragment in forbidden_timeout_voice), "OCR timeout voice should stay natural")

    failing_ocr_tool = ScreenObserveTool(
        workspace,
        FakeVisionObserver(workspace),
        summarizer=MockSummarizer(),
        ocr=RaisingOcrExtractor(RuntimeError('{"path":"C:\\secret\\ocr.log","bbox":[1,2]}')),
    )
    failing_ocr_result = failing_ocr_tool.run(ToolRequest("observe.screen", {"query": "看看页面文字", "target": "fullscreen"}))
    failing_ocr_state = failing_ocr_result.agent_state["observation"]["ocr"]
    assert_true(failing_ocr_result.ok, "OCR failure should not fail screen observation")
    assert_true(failing_ocr_state["status"] == "failed" and failing_ocr_state["error"] == "ocr_failed", "OCR failure should be sanitized")
    forbidden_failure_voice = ["ocr_failed", "C:\\", ".log", "{", "1,2"]
    assert_true(not any(fragment in failing_ocr_result.voice_line.text for fragment in forbidden_failure_voice), "OCR failure voice should stay natural")

    policy = PolicyGate()
    click_decision = policy.classify(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true(click_decision.requires_approval, "computer.click should require approval")
    assert_true(policy.classify(ToolRequest("computer.click", {"x": 100, "y": 200}), approved=True).allowed, "approved computer.click should be allowed")
    public_payload = policy.public_payload(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true("100" not in str(public_payload), "policy preview should not expose raw click coordinates")

    changed_verification = verify_post_action(
        _fake_computer_observation(workspace, title="Before", ocr_text=["登录"]),
        _fake_computer_observation(workspace, title="After", ocr_text=["仪表盘"]),
    )
    assert_true(changed_verification.status == "changed", "verification should detect strong visible changes")
    assert_true(changed_verification.signals.title_changed is True, "verification should report title changes")

    ocr_appeared_verification = verify_post_action(
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/empty-before.png", title="Stable", ocr_text=[]),
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/text-after.png", title="Stable", ocr_text=["新增内容"]),
    )
    assert_true(ocr_appeared_verification.status == "changed", "successful OCR text appearing after action should count as changed")
    assert_true(ocr_appeared_verification.signals.ocr_changed is True, "OCR empty-to-text transition should be a strong signal")

    failed_ocr_verification = verify_post_action(
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/failed-before.png", title="Stable", ocr_text=[], ocr_status="failed"),
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/text-after.png", title="Stable", ocr_text=["新增内容"]),
    )
    assert_true(failed_ocr_verification.status == "likely_noop", "failed OCR before action should not overclaim changed")
    assert_true(failed_ocr_verification.signals.ocr_changed is None, "failed OCR should not drive verification")

    unavailable_ocr_verification = verify_post_action(
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/unavailable-before.png", title="Stable", ocr_text=[], ocr_status="unavailable"),
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/text-after.png", title="Stable", ocr_text=["新增内容"]),
    )
    assert_true(unavailable_ocr_verification.status == "likely_noop", "unavailable OCR before action should not overclaim changed")
    assert_true(unavailable_ocr_verification.signals.ocr_changed is None, "unavailable OCR should not drive verification")

    noop_verification = verify_post_action(
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/before.png", title="Same", ocr_text=["一样"]),
        _fake_computer_observation(workspace, rel="data/agent_companion/vision/after.png", title="Same", ocr_text=["一样"]),
    )
    assert_true(noop_verification.status == "likely_noop", "artifact-only changes should not overclaim success")
    assert_true(noop_verification.signals.artifact_changed is True, "verification should still expose artifact changes")

    unavailable_verification = verify_post_action(None, _fake_computer_observation(workspace))
    assert_true(unavailable_verification.status == "unavailable", "missing before observation should be unavailable")

    fake_backend = FakeComputerBackend(workspace)
    click_tool = ComputerActionTool(workspace, "computer.click", "click", fake_backend, post_action_settle_ms=0)
    click_result = click_tool.run(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true(click_result.ok, "computer.click tool should succeed with fake backend")
    assert_true(click_result.agent_state["computer_use"]["action"]["x"] == 100, "computer action state should keep x for planner")
    assert_true("observation" in click_result.agent_state["computer_use"], "computer action should include after observation")
    assert_true(click_result.agent_state["post_action_verification"]["status"] == "likely_noop", "computer action should include verification status")
    assert_true(click_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "computer action should expose after screenshot artifact")
    assert_true("100" not in click_result.display_card.summary, "computer card summary should be friendly")
    assert_true("100" not in click_result.voice_line.text, "computer voice should not read coordinates")

    changed_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/before-click.png", title="Before", ocr_text=["开始"]),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/after-click.png", title="After", ocr_text=["完成"]),
        ],
    )
    changed_click = ComputerActionTool(workspace, "computer.click", "click", changed_backend, post_action_settle_ms=0).run(ToolRequest("computer.click", {"x": 10, "y": 20}))
    assert_true(changed_click.agent_state["post_action_verification"]["status"] == "changed", "computer action should report changed screen")
    assert_true("操作后画面有变化" in changed_click.display_card.summary, "changed card summary should be friendly")
    assert_true(changed_click.display_card.status == "success", "changed verification should keep success status")

    artifact_only_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/before-scroll.png", title="Same", ocr_text=["相同"]),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/after-scroll.png", title="Same", ocr_text=["相同"]),
        ],
    )
    artifact_only = ComputerActionTool(workspace, "computer.scroll", "scroll", artifact_only_backend, post_action_settle_ms=0).run(ToolRequest("computer.scroll", {"delta": -3}))
    assert_true(artifact_only.agent_state["post_action_verification"]["status"] == "likely_noop", "artifact-only screen comparison should be likely_noop")
    assert_true("变化不明显" in artifact_only.display_card.summary, "likely noop card summary should be friendly")
    assert_true(artifact_only.display_card.status == "info", "likely noop should not look like confirmed success")

    unavailable_backend = FakeComputerBackend(
        workspace,
        observations=[_fake_computer_observation(workspace, rel="data/agent_companion/vision/after-hotkey.png")],
        fail_on_observe_calls={1},
    )
    unavailable_action = ComputerActionTool(workspace, "computer.hotkey", "hotkey", unavailable_backend, post_action_settle_ms=0).run(ToolRequest("computer.hotkey", {"keys": ["Ctrl", "L"]}))
    assert_true(unavailable_action.agent_state["post_action_verification"]["status"] == "unavailable", "missing before comparison should be unavailable")
    assert_true(unavailable_action.display_card.artifacts == ["data/agent_companion/vision/after-hotkey.png"], "unavailable verification should still show after screenshot")
    assert_true(unavailable_action.display_card.status == "info", "unavailable comparison should not look like confirmed success")

    production_style_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/prod-before.png", title="Stable"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/prod-after.png", title="Stable"),
        ],
    )
    production_ocr = SequenceOcrExtractor(
        [
            OcrResult("success", "识别到 1 段可见文字，包含：提交。", [OcrTextBlock("提交", (12, 20, 40, 20), 0.96)]),
            OcrResult("success", "识别到 1 段可见文字，包含：提交成功。", [OcrTextBlock("提交成功", (12, 20, 80, 20), 0.95)]),
        ]
    )
    delay_calls: list[float] = []
    production_style = ComputerActionTool(
        workspace,
        "computer.click",
        "click",
        production_style_backend,
        ocr=production_ocr,
        post_action_settle_ms=250,
        sleep_fn=lambda seconds: delay_calls.append(seconds),
    ).run(ToolRequest("computer.click", {"x": 88, "y": 99}))
    assert_true(delay_calls == [0.25], "computer action should wait configured settle delay before after observation")
    assert_true(len(production_ocr.calls) == 2, "production computer path should OCR both before and after observations")
    assert_true(production_style.agent_state["post_action_verification"]["signals"]["ocr_changed"] is True, "OCR changes should drive real verification")
    assert_true(production_style.agent_state["post_action_verification"]["status"] == "changed", "OCR-changed production path should report changed")
    assert_true(production_style.agent_state["computer_use"]["observation"]["ocr"]["text_blocks"][0]["text"] == "提交成功", "after observation should carry OCR state")

    no_ocr_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/no-ocr-before.png", title="Stable"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/no-ocr-after.png", title="Stable"),
        ],
    )
    no_ocr_result = ComputerActionTool(workspace, "computer.click", "click", no_ocr_backend, post_action_settle_ms=0).run(ToolRequest("computer.click", {"x": 88, "y": 99}))
    assert_true(no_ocr_result.agent_state["post_action_verification"]["status"] == "likely_noop", "test-only OCR should not mask missing production OCR wiring")
    assert_true("ocr" not in no_ocr_result.agent_state["computer_use"]["observation"], "computer observation should only include OCR when extractor is wired")
    forbidden_computer_voice = ["10", "20", "88", "99", "Ctrl", "hello", "data/", ".png", "{", "task-", "完成", "相同", "提交", "新增内容"]
    computer_voice_lines = [changed_click.voice_line.text, artifact_only.voice_line.text, unavailable_action.voice_line.text, production_style.voice_line.text, no_ocr_result.voice_line.text]
    assert_true(all(not any(fragment in line for fragment in forbidden_computer_voice) for line in computer_voice_lines), "computer verification voice leaked technical details")

    type_tool = ComputerActionTool(workspace, "computer.type_text", "type_text", fake_backend, post_action_settle_ms=0)
    type_result = type_tool.run(ToolRequest("computer.type_text", {"text": "hello world"}))
    assert_true(type_result.ok, "computer.type_text tool should succeed with fake backend")
    assert_true("hello" not in type_result.display_card.summary, "type summary should not echo raw text")

    hotkey_tool = ComputerActionTool(workspace, "computer.hotkey", "hotkey", fake_backend, post_action_settle_ms=0)
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
            "asr:\n"
            "  enabled: true\n"
            "  provider: openai_compatible\n"
            "  base_url: https://api.asr.com/v1\n"
            "  model: whisper-1\n"
            "  api_key: sk-asr\n"
            "  language: zh\n"
            "  max_seconds: 7\n"
            "  max_bytes: 4096\n"
            "  timeout_seconds: 9\n"
            "ocr:\n"
            "  timeout_seconds: 4\n"
            "computer_use:\n"
            "  post_action_settle_ms: 0\n"
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
        tmp_config = load_app_config(tmp / "config.yaml")
        assert_true(tmp_config.asr.is_configured, "ASR config should parse as configured")
        assert_true(tmp_config.asr.max_seconds == 7 and tmp_config.asr.max_bytes == 4096, "ASR limits should parse")
        assert_true(tmp_config.asr.timeout_seconds == 9, "ASR timeout should parse")
        assert_true(tmp_config.ocr.timeout_seconds == 4, "OCR timeout should parse")
        assert_true(tmp_config.computer_use.post_action_settle_ms == 0, "computer use settle delay should parse")
        asr_provider, asr_state = build_asr_provider(tmp)
        assert_true(isinstance(asr_provider, OpenAICompatibleAsrProvider), "configured ASR should use OpenAI-compatible provider")
        assert_true(asr_state.configured and asr_state.max_bytes == 4096, "ASR runtime state should expose limits")
        assert_true(asr_state.timeout_seconds == 9, "ASR runtime state should expose timeout")
        assert_true(isinstance(tmp_app._build_ocr_extractor(), PytesseractOcrExtractor), "OCR extractor should build from config")
        assert_true(tmp_app._build_ocr_extractor().timeout_seconds == 4, "OCR extractor should use configured timeout")
        tmp_click_tool = tmp_app.tools._tools.get("computer.click")
        assert_true(tmp_click_tool is not None and tmp_click_tool.ocr is observe_tool.ocr, "computer tool should reuse observe.screen OCR extractor")
        assert_true(tmp_click_tool.post_action_settle_ms == 0, "computer tool should use configured settle delay")
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

    answer_frames = [
        WatchFrame(
            user_question="陪我看当前页面",
            summary="画面摘要：页面正在展示项目路线图。",
            title="Joi Roadmap",
            artifact="data/agent_companion/vision/sample.png",
            model_status="ok",
            ocr_summary="识别到 2 段可见文字。",
            ocr_text=["P4 Watch Together", "P5 Model Router"],
        )
    ]
    answer_tool = WatchRecallTool(workspace, lambda limit: answer_frames[:limit], FakeWatchAnswerer())
    answer_result = answer_tool.run(ToolRequest("watch.recall", {"query": "这个页面讲什么"}))
    assert_true("这个页面讲什么" in answer_result.display_card.summary, "watch answerer should use the follow-up question")
    assert_true(answer_result.agent_state["answer_source"] == "model", "watch answerer should report model source when used")
    assert_true(answer_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "watch answer should keep screenshot artifact for preview")
    ocr_question_tool = WatchRecallTool(workspace, lambda limit: answer_frames[:limit])
    ocr_question_result = ocr_question_tool.run(ToolRequest("watch.recall", {"query": "页面里写了什么"}))
    assert_true("P4 Watch Together" in ocr_question_result.display_card.summary, "watch recall should answer from OCR text")
    assert_true("P5 Model Router" in ocr_question_result.agent_state["watch_context"][0]["ocr_text"], "watch context should expose OCR text for planner")
    assert_true("sample.png" not in ocr_question_result.voice_line.text, "watch OCR voice should not read artifact path")

    watch_app = AgentCompanionApp(workspace)
    before_watch_memory = watch_app.memory.recent(200)
    watch_app.tools.register(ScreenObserveTool(workspace, FakeVisionObserver(workspace), summarizer=MockSummarizer(), ocr=FakeOcrExtractor(mock_ocr)))
    watch_events = watch_app.handle_user_text("陪我看当前画面")
    assert_true(any(event.agent_state.get("tool") == "observe.screen" for event in watch_events), "watch should observe screen first")
    assert_true(watch_app.watch_session.has_context(), "watch session should remember visual context")
    recall_events = watch_app.handle_user_text("你看到了什么")
    assert_true(any(event.agent_state.get("tool") == "watch.recall" for event in recall_events), "watch follow-up should use recall tool")
    assert_true(not any(event.agent_state.get("tool") == "observe.screen" for event in recall_events), "watch follow-up should not repeat screen capture")
    recall_cards = [event for event in recall_events if event.agent_state.get("tool") == "watch.recall"]
    assert_true("画面摘要" in recall_cards[-1].display_card.summary, "watch recall should answer from visual summary")
    assert_true("登录" in recall_cards[-1].display_card.body, "watch recall detail should reuse OCR context")
    assert_true(recall_cards[-1].display_card.artifacts == ["data/agent_companion/vision/sample.png"], "watch recall should show recent screenshot artifact")
    assert_true(recall_cards[-1].agent_state["artifacts"] == ["data/agent_companion/vision/sample.png"], "watch recall should expose artifact preview data")
    assert_true(all("sample.png" not in event.voice_line.text for event in recall_events), "watch recall voice should not read artifact path")
    assert_true(watch_app.memory.recent(200) == before_watch_memory, "watch observations should not enter long-term memory by default")

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

    unconfigured_bridge = JsonRpcBridge(workspace)
    unconfigured_payload = unconfigured_bridge.transcribe_and_submit("", "audio/webm")
    assert_true(not unconfigured_payload["ok"] and unconfigured_payload["error"] == "asr_unconfigured", "unconfigured ASR should fail clearly")
    assert_true(not unconfigured_payload["submitted"], "unconfigured ASR should not submit user.message")
    assert_true(any(event["type"] == "task_failed" for event in unconfigured_payload["events"]), "unconfigured ASR should create a friendly task event")

    predecode_provider = FailingAsrProvider()
    predecode_bridge = JsonRpcBridge(
        workspace,
        asr_provider=predecode_provider,
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=2),
    )
    predecode_payload = predecode_bridge.transcribe_and_submit("AAAAAA==", "audio/webm")
    assert_true(not predecode_payload["ok"] and predecode_payload["error"] == "audio_too_large", "oversized base64 should be rejected before decoding")
    assert_true(not predecode_provider.called, "pre-decode oversized audio should not call ASR provider")

    decoded_provider = FailingAsrProvider()
    decoded_bridge = JsonRpcBridge(
        workspace,
        asr_provider=decoded_provider,
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=2),
    )
    decoded_payload = decoded_bridge.transcribe_and_submit("AAAA", "audio/webm")
    assert_true(not decoded_payload["ok"] and decoded_payload["error"] == "audio_too_large", "decoded oversized audio should still be rejected")
    assert_true(not decoded_provider.called, "decoded oversized audio should not call ASR provider")

    too_large_bridge = JsonRpcBridge(
        workspace,
        asr_provider=MockAsrProvider("你好"),
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=2),
    )
    too_large_payload = too_large_bridge.transcribe_and_submit("AAAAAA==", "audio/webm")
    assert_true(not too_large_payload["ok"] and too_large_payload["error"] == "audio_too_large", "oversized audio should be rejected before ASR")

    noisy_error_provider = FailingAsrProvider('Traceback C:\\secret\\run.ps1 {"task_id":"task-abcdef"} codex.run')
    noisy_error_bridge = JsonRpcBridge(
        workspace,
        asr_provider=noisy_error_provider,
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=4096),
    )
    noisy_error_payload = noisy_error_bridge.transcribe_and_submit("AAAA", "audio/webm")
    assert_true(not noisy_error_payload["ok"] and noisy_error_payload["error"] == "asr_failed", "raw ASR errors should be normalized")
    voice_lines = [event["voice_line"]["text"] for event in noisy_error_payload["events"]]
    forbidden_voice_fragments = ["{", "}", "C:\\", ".ps1", "Traceback", "task-", "codex.run"]
    assert_true(all(not any(fragment in line for fragment in forbidden_voice_fragments) for line in voice_lines), "voice error leaked technical detail")

    timeout_bridge = JsonRpcBridge(
        workspace,
        asr_provider=FailingAsrProvider("asr_timeout"),
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=4096, timeout_seconds=3),
    )
    timeout_payload = timeout_bridge.transcribe_and_submit("AAAA", "audio/webm")
    assert_true(not timeout_payload["ok"] and timeout_payload["error"] == "asr_timeout", "ASR timeout should return friendly error code")

    ready_bridge = JsonRpcBridge(
        workspace,
        asr_provider=MockAsrProvider("你好"),
        asr_state=AsrRuntimeState(True, True, "mock", max_bytes=4096, timeout_seconds=5),
    )
    ready_payload = ready_bridge._ready_payload()
    assert_true(ready_payload["asr"]["timeout_seconds"] == 5, "Core ready payload should expose ASR timeout")
    assert_true("tts" in ready_payload and "provider" in ready_payload["tts"], "Core ready payload should expose safe TTS status")
    assert_true("server_url" not in ready_payload["tts"] and "gpt_sovits_work_path" not in ready_payload["tts"], "TTS status should not expose paths or endpoints")

    shell_api_source = (workspace / "agent_companion" / "shell" / "src" / "api.ts").read_text(encoding="utf-8")
    assert_true("transcribeVoice(audioBase64: string, mimeType: string, timeoutMs: number)" in shell_api_source, "voice RPC should accept a method-specific timeout")
    assert_true("语音识别等太久了" in shell_api_source, "voice RPC timeout should be user-friendly")
    voice_runtime_source = (workspace / "agent_companion" / "shell" / "src" / "voiceRuntime.ts").read_text(encoding="utf-8")
    assert_true("shouldPlayVoiceAudio" in voice_runtime_source and "eventEpoch === currentEpoch" in voice_runtime_source, "voice runtime should suppress stale audio by epoch")
    assert_true("event_created_at" in voice_runtime_source, "voice runtime key should include event identity")
    app_vue_source = (workspace / "agent_companion" / "shell" / "src" / "App.vue").read_text(encoding="utf-8")
    assert_true("beginNewVoiceIntent()" in app_vue_source and "voiceEventEpochs.get" in app_vue_source, "Shell should bump and compare voice epochs")
    assert_true("event_created_at: event.created_at" in app_vue_source, "Shell should key voice audio by event timestamp")
    assert_true("runtimeStatusRows" in app_vue_source and "lastTtsError" in app_vue_source, "Shell developer mode should expose voice runtime status")
    server_source = (workspace / "agent_companion" / "core" / "server.py").read_text(encoding="utf-8")
    assert_true('"event_created_at": event.created_at' in server_source, "Core voice audio payload should include event timestamp")
    tts_bridge_source = (workspace / "agent_companion" / "core" / "tts_bridge.py").read_text(encoding="utf-8")
    assert_true("status_payload" in tts_bridge_source and "_safe_tts_error" in tts_bridge_source, "TTS bridge should expose sanitized status")

    voice_bridge = JsonRpcBridge(workspace, asr_provider=MockAsrProvider("你好"))
    voice_payload = voice_bridge.transcribe_and_submit("", "audio/webm")
    assert_true(voice_payload["ok"] and voice_payload["transcript"] == "你好", "mock ASR should return transcript")
    assert_true(any(event["type"] == "user_message" for event in voice_payload["events"]), "ASR transcript should enter user.message route")

    approval_voice_bridge = JsonRpcBridge(workspace, asr_provider=MockAsrProvider("点击 100,200"))
    approval_voice_payload = approval_voice_bridge.transcribe_and_submit("", "audio/webm")
    voice_events = approval_voice_payload["events"]
    assert_true(any(event["type"] == "approval_required" for event in voice_events), "voice computer command should still require approval")
    assert_true(not any(event["type"] == "tool_completed" and event.get("agent_state", {}).get("tool") == "computer.click" for event in voice_events), "voice command should not bypass approval")

    serial_bridge = JsonRpcBridge(workspace, asr_provider=MockAsrProvider("你好"))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(serial_bridge.submit_user_text, "你好"),
            pool.submit(serial_bridge.transcribe_and_submit, "", "audio/webm"),
        ]
        serial_payloads = [future.result() for future in futures]
    sequences = sorted(int(payload["sequence"]) for payload in serial_payloads)
    assert_true(sequences == [1, 2], "command queue should serialize concurrent mutations")
    print("agent_companion tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
