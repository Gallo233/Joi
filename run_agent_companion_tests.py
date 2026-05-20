from __future__ import annotations

import os
import inspect
import tempfile
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.computer_use import COMPUTER_AUDIT_STATE_KEY, ComputerAction, ComputerObservation, ComputerUseResult, computer_action_audit_event, verify_post_action
from agent_companion.core.config import LlmConfig, ModelEndpoint, ModelRouter, load_app_config
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core import runtime_status as runtime_status_module
from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.schemas import DisplayCard, EventType, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.speech_input import AsrResult, AsrRuntimeState, MockAsrProvider, OpenAICompatibleAsrProvider, build_asr_provider
from agent_companion.core.tools.computer import ComputerActionTool
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.tools.targeting import SemanticTargetTool
from agent_companion.core.tools.watch import WatchRecallTool
from agent_companion.core.vision.accessibility import AccessibilitySnapshot, AccessibleElement, _looks_clickable
from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock, PytesseractOcrExtractor, UnavailableOcrExtractor
from agent_companion.core.vision.regions import group_ocr_regions
from agent_companion.core.vision.schemas import CaptureRect, VisionObservation
from agent_companion.core.vision.summarizer import MockSummarizer, OpenAIVisionSummarizer, VisionSummary
from agent_companion.core.vision.targeting import resolve_target_candidates
from agent_companion.core.vision.visual_detector import UnavailableVisualDetector, VisualCandidate, VisualDetectionResult
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch import WatchFrame
from tools.eval_visual_detector import run_eval as run_visual_detector_eval


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _runtime_with_ocr_probe(
    workspace: Path,
    *,
    has_pillow: bool,
    has_pytesseract: bool,
    tesseract_path: str | None,
    version_probe: bool | Exception,
) -> dict:
    original_find_spec = runtime_status_module.importlib.util.find_spec
    original_which = runtime_status_module.shutil.which
    original_probe = runtime_status_module._probe_tesseract_version

    def fake_find_spec(name: str, *args: object, **kwargs: object) -> object | None:
        if name == "PIL":
            return object() if has_pillow else None
        if name == "pytesseract":
            return object() if has_pytesseract else None
        return original_find_spec(name, *args, **kwargs)

    def fake_which(command: str, *args: object, **kwargs: object) -> str | None:
        if command == "tesseract":
            return tesseract_path
        return original_which(command, *args, **kwargs)

    def fake_probe() -> bool:
        if isinstance(version_probe, Exception):
            raise version_probe
        return bool(version_probe)

    runtime_status_module.importlib.util.find_spec = fake_find_spec
    runtime_status_module.shutil.which = fake_which
    runtime_status_module._probe_tesseract_version = fake_probe
    try:
        return build_runtime_status(
            workspace,
            AsrRuntimeState(False, False, "none"),
            {"enabled": False, "configured": False, "provider": "none", "last_error": ""},
        )
    finally:
        runtime_status_module.importlib.util.find_spec = original_find_spec
        runtime_status_module.shutil.which = original_which
        runtime_status_module._probe_tesseract_version = original_probe


def _ocr_status_row(runtime_payload: dict) -> dict:
    return {row["name"]: row for row in runtime_payload["providers"]}["ocr"]


def _assert_runtime_payload_has_no_probe_leaks(runtime_payload: dict) -> None:
    text = str(runtime_payload)
    forbidden = [
        "/Users/",
        "C:\\",
        "private/bin/tesseract-real",
        "stderr raw",
        "traceback",
        "sk-",
        "secret",
        "token",
    ]
    assert_true(all(fragment not in text for fragment in forbidden), "OCR runtime probe leaked raw path, stderr, secret, or token")


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
            capture_rect=CaptureRect(0, 0, 1280, 720),
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
    capture_rect: CaptureRect | None = None,
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
        capture_rect=capture_rect,
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


class FakeAccessibilityObserver:
    def __init__(self, snapshot: AccessibilitySnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[int | None, str]] = []

    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        self.calls.append((window_handle, title))
        return self.snapshot


def _no_accessibility() -> FakeAccessibilityObserver:
    return FakeAccessibilityObserver(AccessibilitySnapshot("unavailable", error="test_unavailable"))


class FakeVisualDetector:
    def __init__(self, result: VisualDetectionResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def detect(self, observation: ComputerObservation, query: str = "") -> VisualDetectionResult:
        self.calls.append((observation.screenshot_rel, query))
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


def _write_ppm(path: Path, width: int, height: int, color: tuple[int, int, int], patch: tuple[int, int, int, int, tuple[int, int, int]] | None = None) -> None:
    rows = bytearray()
    patch_left = patch_top = patch_width = patch_height = 0
    patch_color = color
    if patch is not None:
        patch_left, patch_top, patch_width, patch_height, patch_color = patch
    for y in range(height):
        for x in range(width):
            if patch is not None and patch_left <= x < patch_left + patch_width and patch_top <= y < patch_top + patch_height:
                rows.extend(patch_color)
            else:
                rows.extend(color)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(rows))


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


class FakeInvokeControl:
    def GetInvokePattern(self) -> object:
        return object()


class FakeNonInvokeControl:
    def GetInvokePattern(self) -> object:
        raise RuntimeError("no invoke pattern")


def _approval_payload(events) -> dict:
    for event in events:
        if event.type == EventType.APPROVAL_REQUIRED:
            approval = event.agent_state.get("approval")
            if isinstance(approval, dict):
                return approval
    return {}


def _audit_rows(events) -> list[dict]:
    rows: list[dict] = []
    for event in events:
        audit = event.agent_state.get(COMPUTER_AUDIT_STATE_KEY)
        if isinstance(audit, list):
            rows.extend(row for row in audit if isinstance(row, dict))
    return rows


def _selection_id(events) -> str:
    for event in events:
        selection_id = event.agent_state.get("selection_id")
        if isinstance(selection_id, str) and selection_id:
            return selection_id
    return ""


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
    semantic_click_plan = build_plan("点登录按钮")
    assert_true(semantic_click_plan.intent == "semantic_target", "semantic click should use target grounding route")
    assert_true(semantic_click_plan.steps[0].name == "vision.resolve_target", "semantic click should resolve target before clicking")
    contextual_click_plan = build_plan("点那个开始任务")
    assert_true(contextual_click_plan.intent == "semantic_target", "contextual click should use target grounding route")
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
    blocked_approval = safe_voice_line("approval-abcdef123456 已确认，截图 sample.png")
    assert_true("approval-" not in blocked_approval.text and "sample.png" not in blocked_approval.text, "voice leaked approval id or screenshot filename")
    blocked_runtime_detail = safe_voice_line("provider sk-test token path /Users/me/models/joi.gguf trace.log")
    assert_true(
        "sk-" not in blocked_runtime_detail.text
        and "/Users/" not in blocked_runtime_detail.text
        and ".gguf" not in blocked_runtime_detail.text
        and ".log" not in blocked_runtime_detail.text,
        "voice leaked provider secret, local model path, or log filename",
    )

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
    assert_true("ocr_regions" in ocr_result.agent_state["observation"], "screen observation should include OCR regions")
    forbidden_ocr_voice = ["10", "20", "开始任务", "sample.png", "{"]
    assert_true(not any(fragment in ocr_result.voice_line.text for fragment in forbidden_ocr_voice), "OCR voice should not read raw OCR details")

    region_ocr = OcrResult(
        "success",
        "mock regions",
        [
            OcrTextBlock("登录", (860, 30, 60, 24), 0.96),
            OcrTextBlock("菜单", (20, 420, 50, 24), 0.91),
            OcrTextBlock("开始任务", (430, 450, 100, 32), 0.94),
            OcrTextBlock("发送", (450, 900, 80, 30), 0.9),
        ],
    )
    grouped_regions = group_ocr_regions(region_ocr, 1000, 1000)
    grouped_labels = {region.label for region in grouped_regions}
    assert_true({"top_bar", "sidebar", "main_content", "bottom_controls"}.issubset(grouped_labels), "OCR blocks should group into coarse regions")
    grouped_state = [region.to_agent_state() for region in grouped_regions]
    login_candidates = resolve_target_candidates("点登录按钮", grouped_state)
    assert_true(login_candidates and login_candidates[0].label == "登录", "semantic phrase should resolve to matching OCR candidate")
    corner_candidates = resolve_target_candidates("右上角", grouped_state)
    assert_true(corner_candidates and corner_candidates[0].text == "登录", "right-top phrase should resolve to a top/right OCR candidate")
    duplicate_login_ocr = OcrResult(
        "success",
        "duplicate login",
        [
            OcrTextBlock("登录", (40, 30, 60, 24), 0.95),
            OcrTextBlock("登录", (860, 30, 60, 24), 0.95),
        ],
    )
    duplicate_grouped = [region.to_agent_state() for region in group_ocr_regions(duplicate_login_ocr, 1000, 1000)]
    positioned_login = resolve_target_candidates("右上角登录", duplicate_grouped)
    assert_true(positioned_login and positioned_login[0].bbox == (860, 30, 60, 24), "position words should rank the correct duplicate OCR candidate")
    assert_true(positioned_login[0].rank == 1 and positioned_login[0].ambiguity == "none", "resolved candidate should expose rank and non-ambiguous state")
    ambiguous_login = resolve_target_candidates("点登录按钮", duplicate_grouped)
    assert_true(len(ambiguous_login) >= 2 and ambiguous_login[0].ambiguity == "close_score", "close duplicate candidates should be marked ambiguous")
    noisy_candidates = resolve_target_candidates("点登录按钮", group_ocr_regions(OcrResult("success", "noise", [OcrTextBlock("天气", (200, 200, 60, 20), 0.9)]), 1000, 1000))
    assert_true(not noisy_candidates, "missing/noisy OCR should not create confident target candidate")

    target_tool = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=_no_accessibility(),
    )
    target_result = target_tool.run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(target_result.requires_approval, "semantic target should ask for approval before click")
    assert_true(target_result.agent_state["approval_request"]["tool"] == "computer.click", "semantic target approval should resolve to computer.click")
    click_args = target_result.agent_state["approval_request"]["arguments"]
    assert_true(click_args["x"] == 990 and click_args["y"] == 242, "semantic target click should convert relative bbox to absolute screen coordinates")
    assert_true(target_result.agent_state["target_candidate"]["rank"] == 1 and target_result.agent_state["target_candidate"]["ambiguity"] == "none", "high-confidence single target should be unambiguous")
    assert_true(target_result.agent_state["target_candidate"]["preview"]["bbox"] == [860, 30, 60, 24], "semantic target card should keep relative preview bbox")
    assert_true(len(target_result.agent_state["target_candidates"]) >= 1, "semantic target approval should preserve candidate previews")
    assert_true("登录" in target_result.display_card.summary, "semantic target card should name the friendly target")
    forbidden_target_voice = ["登录", "860", "30", "data/", ".png", "{", "vision.resolve_target", "computer.click"]
    assert_true(not any(fragment in target_result.voice_line.text for fragment in forbidden_target_voice), "semantic target voice leaked technical details")

    ambiguous_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-ambiguous.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(duplicate_login_ocr),
        accessibility=_no_accessibility(),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not ambiguous_target.requires_approval, "close top candidates should not generate click approval")
    assert_true("approval_request" not in ambiguous_target.agent_state, "ambiguous semantic target should not synthesize click arguments")
    assert_true(ambiguous_target.agent_state["candidate_selection_required"], "ambiguous semantic target should ask for candidate selection")
    assert_true(ambiguous_target.agent_state["target_candidate"]["ambiguity"] == "close_score", "ambiguous target state should explain close score")
    assert_true(not any(fragment in ambiguous_target.voice_line.text for fragment in forbidden_target_voice), "ambiguous target voice leaked technical details")

    assert_true(_looks_clickable("ButtonControl", FakeInvokeControl()), "UIA clickable should require a real invoke pattern")
    assert_true(not _looks_clickable("ButtonControl", FakeNonInvokeControl()), "UIA role alone must not mark a control clickable")
    assert_true(not _looks_clickable("TextControl", object()), "static UIA text should not be clickable")

    accessibility_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[AccessibleElement("登录", "ButtonControl", (960, 230, 60, 30), enabled=True, clickable=True, confidence=0.92)],
    )
    accessibility_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-accessibility.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=FakeAccessibilityObserver(accessibility_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(accessibility_target.requires_approval, "accessibility button should create approval-gated click candidate")
    assert_true(accessibility_target.agent_state["target_candidate"]["source"] == "accessibility", "accessibility target should preserve source")
    accessibility_click_args = accessibility_target.agent_state["approval_request"]["arguments"]
    assert_true(accessibility_click_args["x"] == 990 and accessibility_click_args["y"] == 245, "accessibility bounds should click by absolute screen center")
    assert_true(accessibility_target.agent_state["target_candidate"]["preview"]["bbox"] == [860, 30, 60, 30], "accessibility candidate should have screenshot-relative preview bbox")

    disabled_button_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[AccessibleElement("登录", "ButtonControl", (960, 230, 60, 30), enabled=False, clickable=True, confidence=0.92)],
    )
    disabled_button_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-accessibility-disabled.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=FakeAccessibilityObserver(disabled_button_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not disabled_button_target.requires_approval, "disabled UIA button must not create click approval")
    assert_true("approval_request" not in disabled_button_target.agent_state, "disabled UIA button must not synthesize click arguments")
    assert_true(disabled_button_target.agent_state["candidate_selection_required"], "disabled UIA button should stay as a confirmable candidate")
    assert_true("不可操作" in disabled_button_target.display_card.summary or "未启用" in disabled_button_target.display_card.summary, "disabled target card should explain unavailable actionability")

    static_text_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[AccessibleElement("登录", "TextControl", (960, 230, 60, 30), enabled=True, clickable=False, confidence=0.92)],
    )
    static_text_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-accessibility-text.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=FakeAccessibilityObserver(static_text_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not static_text_target.requires_approval, "static UIA text alone should not create click approval")
    assert_true(static_text_target.agent_state["candidate_selection_required"], "static UIA text should ask for clarification or selection")
    assert_true(static_text_target.agent_state["target_candidate"]["source"] == "accessibility", "static UIA text should remain visible as a candidate")
    assert_true(static_text_target.agent_state["target_candidate"].get("role") == "TextControl", "static UIA candidate should keep role")

    fused_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-fused.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=FakeAccessibilityObserver(static_text_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(fused_target.requires_approval, "fused OCR/accessibility target should still ask approval before click")
    assert_true(fused_target.agent_state["target_candidate"]["source"] == "fused", "OCR/accessibility same target should fuse")

    disabled_fused_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-fused-disabled.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=FakeAccessibilityObserver(disabled_button_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not disabled_fused_target.requires_approval, "disabled fused OCR/UIA target must not create click approval")
    assert_true(disabled_fused_target.agent_state["target_candidate"]["source"] == "fused", "disabled OCR/UIA same target should still expose fused source")
    assert_true(disabled_fused_target.agent_state["candidate_selection_required"], "disabled fused target should require clarification or candidate selection")
    assert_true("不可操作" in disabled_fused_target.display_card.summary or "未启用" in disabled_fused_target.display_card.summary, "disabled fused card should explain unavailable actionability")

    left_login_ocr = OcrResult("success", "left login", [OcrTextBlock("登录", (40, 30, 60, 24), 0.95)])
    conflict_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-conflict.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(left_login_ocr),
        accessibility=FakeAccessibilityObserver(accessibility_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not conflict_target.requires_approval, "conflicting OCR/accessibility candidates should not click directly")
    assert_true(conflict_target.agent_state["candidate_selection_required"], "conflicting OCR/accessibility candidates should ask for selection")
    assert_true(conflict_target.agent_state["target_candidate"]["ambiguity"] == "close_score", "conflicting target should be marked ambiguous")

    ocr_fallback_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-accessibility-unavailable.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=_no_accessibility(),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(ocr_fallback_target.requires_approval, "accessibility unavailable should keep OCR fallback working")
    assert_true(ocr_fallback_target.agent_state["target_candidate"]["source"] == "ocr", "OCR fallback should preserve source")
    ocr_with_unavailable_detector = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-visual-unavailable.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=_no_accessibility(),
        visual_detector=UnavailableVisualDetector(),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(ocr_with_unavailable_detector.requires_approval, "unavailable visual detector should not break OCR approval path")
    assert_true(ocr_with_unavailable_detector.agent_state["target_candidate"]["source"] == "ocr", "OCR target should remain source when visual detector is unavailable")
    forbidden_uia_voice = forbidden_target_voice + ["990", "245", "selection-", "uiautomation", "TextControl", "ButtonControl", "enabled", "role"]
    assert_true(all(not any(fragment in event_text for fragment in forbidden_uia_voice) for event_text in [accessibility_target.voice_line.text, disabled_button_target.voice_line.text, static_text_target.voice_line.text, fused_target.voice_line.text, disabled_fused_target.voice_line.text, conflict_target.voice_line.text]), "accessibility voice leaked technical details")

    visual_result = VisualDetectionResult(
        "success",
        "mock visual candidates",
        [VisualCandidate("开始任务", (420, 760, 160, 70), 0.66, "底部高对比操作块", region="bottom_controls")],
        artifacts=["data/agent_companion/vision/target-visual.png"],
    )
    visual_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-visual.png",
                    width=1000,
                    height=1000,
                    capture_rect=CaptureRect(100, 200, 1000, 1000),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=_no_accessibility(),
        visual_detector=FakeVisualDetector(visual_result),
    ).run(ToolRequest("vision.resolve_target", {"query": "点开始任务"}))
    assert_true(not visual_target.requires_approval, "visual-only target must not directly create click approval")
    assert_true(visual_target.agent_state["candidate_selection_required"], "visual-only target should ask for candidate selection")
    assert_true(visual_target.agent_state["target_candidate"]["source"] == "visual", "visual fallback should expose visual source")
    assert_true(visual_target.agent_state["visual_detection"]["status"] == "success", "visual detection state should be attached")
    assert_true(visual_target.agent_state["target_candidate"]["preview"]["bbox"] == [420, 760, 160, 70], "visual candidate should keep preview bbox")
    forbidden_visual_voice = forbidden_target_voice + ["visual", "420", "760", "source", "bbox", "data/"]
    assert_true(not any(fragment in visual_target.voice_line.text for fragment in forbidden_visual_voice), "visual target voice leaked technical details")

    visual_selection_app = AgentCompanionApp(workspace)
    visual_selection_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-visual.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
            accessibility=_no_accessibility(),
            visual_detector=FakeVisualDetector(visual_result),
        )
    )
    visual_selection_events = visual_selection_app.handle_user_text("点开始任务")
    visual_selection_id = _selection_id(visual_selection_events)
    assert_true(visual_selection_id.startswith("selection-"), "visual fallback should create pending candidate selection context")
    visual_selected_events = visual_selection_app.select_semantic_target(visual_selection_id, 1)
    visual_approval = _approval_payload(visual_selected_events)
    assert_true(visual_approval.get("tool") == "computer.click", "selected visual candidate should create approval-gated click")
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED and event.agent_state.get("risk") == "medium" for event in visual_selected_events), "visual candidate click should remain medium-risk approval")
    assert_true(all(not any(fragment in event.voice_line.text for fragment in forbidden_visual_voice) for event in visual_selected_events), "visual selection voice leaked technical details")

    disabled_selection_app = AgentCompanionApp(workspace)
    disabled_selection_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-disabled.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
            accessibility=FakeAccessibilityObserver(disabled_button_snapshot),
        )
    )
    disabled_selection_events = disabled_selection_app.handle_user_text("点登录按钮")
    disabled_selection_id = _selection_id(disabled_selection_events)
    disabled_selected_events = disabled_selection_app.select_semantic_target(disabled_selection_id, 1)
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in disabled_selected_events), "disabled candidate selection must not create click approval")
    assert_true(any(event.agent_state.get("candidate_not_actionable") for event in disabled_selected_events), "disabled candidate selection should explain actionability block")
    assert_true(any("不可操作" in event.display_card.summary or "未启用" in event.display_card.summary for event in disabled_selected_events), "disabled selection card should explain unavailable actionability")
    assert_true(all(not any(fragment in event.voice_line.text for fragment in forbidden_uia_voice) for event in disabled_selected_events), "disabled selection voice leaked technical details")

    selection_app = AgentCompanionApp(workspace)
    selection_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-ambiguous.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(duplicate_login_ocr),
            accessibility=_no_accessibility(),
        )
    )
    selection_events = selection_app.handle_user_text("点登录按钮")
    assert_true(any(event.agent_state.get("candidate_selection_required") for event in selection_events), "ambiguous app target should store candidate selection context")
    selection_id = _selection_id(selection_events)
    assert_true(selection_id.startswith("selection-"), "ambiguous selection context should expose a selection_id")
    selected_events = selection_app.select_semantic_target(selection_id, 2)
    selected_approval = _approval_payload(selected_events)
    assert_true(selected_approval.get("tool") == "computer.click", "JSON-RPC candidate selection should synthesize computer click approval")
    selected_card = [event for event in selected_events if event.type == EventType.APPROVAL_REQUIRED][-1]
    assert_true(selected_card.agent_state.get("selected_rank") == 2 or selected_card.agent_state.get("target_candidate", {}).get("rank") == 2, "candidate selection should preserve selected rank")
    assert_true(selected_card.agent_state.get("selection_id") == selection_id, "candidate selection approval should stay bound to selection_id")
    assert_true("已选择候选 2" in selected_card.display_card.summary, "candidate selection card should show selected candidate")
    forbidden_selection_voice = forbidden_target_voice + ["selection-", "100", "200"]
    assert_true(all(not any(fragment in event.voice_line.text for fragment in forbidden_selection_voice) for event in selected_events), "candidate selection voice leaked technical details")
    selected_refused = selection_app.resolve_approval(str(selected_approval["approval_id"]), approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in selected_refused), "candidate selection refusal should cancel action")
    assert_true(not any(event.type == EventType.TOOL_COMPLETED and event.agent_state.get("tool") == "computer.click" for event in selected_refused), "refused candidate selection must not execute click")

    fallback_app = AgentCompanionApp(workspace)
    fallback_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-fallback.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(duplicate_login_ocr),
            accessibility=_no_accessibility(),
        )
    )
    fallback_app.handle_user_text("点登录按钮")
    fallback_events = fallback_app.handle_user_text("选 2")
    assert_true(_approval_payload(fallback_events).get("tool") == "computer.click", "text fallback candidate selection should still work")

    multi_app = AgentCompanionApp(workspace)
    multi_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-first.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    ),
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-second.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(300, 400, 1000, 1000),
                    ),
                ],
            ),
            ocr=FakeOcrExtractor(duplicate_login_ocr),
            accessibility=_no_accessibility(),
        )
    )
    first_selection_id = _selection_id(multi_app.handle_user_text("点登录按钮"))
    second_selection_id = _selection_id(multi_app.handle_user_text("点登录按钮"))
    assert_true(first_selection_id and second_selection_id and first_selection_id != second_selection_id, "multiple semantic selections should keep distinct ids")
    old_selection_events = multi_app.select_semantic_target(first_selection_id, 2)
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in old_selection_events), "old candidate card must not select latest context")
    assert_true(any(event.agent_state.get("selection_not_current") for event in old_selection_events), "old candidate card should explain stale context")
    latest_selection_events = multi_app.select_semantic_target(second_selection_id, 2)
    assert_true(_approval_payload(latest_selection_events).get("tool") == "computer.click", "current selection_id should create click approval")

    bridge = JsonRpcBridge(workspace)
    bridge.app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-rpc.png",
                        width=1000,
                        height=1000,
                        capture_rect=CaptureRect(100, 200, 1000, 1000),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(duplicate_login_ocr),
            accessibility=_no_accessibility(),
        )
    )
    rpc_selection_id = _selection_id(bridge.app.handle_user_text("点登录按钮"))
    rpc_result = bridge.select_semantic_target_command(rpc_selection_id, 2)
    rpc_events = rpc_result.get("events", [])
    assert_true(rpc_result.get("ok") is True and rpc_result.get("submitted") is True, "semantic_target.select command should return submitted result")
    assert_true(any(event.get("type") == "approval_required" and event.get("agent_state", {}).get("approval", {}).get("tool") == "computer.click" for event in rpc_events), "semantic_target.select should create approval_required")

    expired_app = AgentCompanionApp(workspace)
    expired_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[_fake_computer_observation(workspace, rel="data/agent_companion/vision/selection-expired.png", width=1000, height=1000, capture_rect=CaptureRect(100, 200, 1000, 1000))],
            ),
            ocr=FakeOcrExtractor(duplicate_login_ocr),
            accessibility=_no_accessibility(),
        )
    )
    expired_selection_id = _selection_id(expired_app.handle_user_text("点登录按钮"))
    expired_app.semantic_selection.ttl_seconds = -1
    expired_events = expired_app.select_semantic_target(expired_selection_id, 2)
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in expired_events), "expired candidate selection should not create click approval")
    assert_true(any(event.agent_state.get("selection_expired") for event in expired_events), "expired candidate selection should explain stale context")
    missing_selection_events = expired_app.select_semantic_target("selection-missing", 2)
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in missing_selection_events), "missing selection_id should not create click approval")
    assert_true(any(event.agent_state.get("selection_missing") for event in missing_selection_events), "missing selection_id should explain missing context")

    no_pending_selection = AgentCompanionApp(workspace).handle_user_text("选 2")
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in no_pending_selection), "selection without pending context should not click")

    missing_rect_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-no-rect.png",
                    width=1000,
                    height=1000,
                    capture_rect=None,
                )
            ],
        ),
        ocr=FakeOcrExtractor(region_ocr),
        accessibility=_no_accessibility(),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not missing_rect_target.requires_approval, "semantic target should not approve clicks without capture rect")
    assert_true(missing_rect_target.agent_state["needs_clarification"], "missing capture rect should ask for clarification")

    unclear_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(workspace, observations=[_fake_computer_observation(workspace, rel="data/agent_companion/vision/target-missing.png")]),
        ocr=FakeOcrExtractor(OcrResult("success", "noise", [OcrTextBlock("天气", (200, 200, 60, 20), 0.9)])),
        accessibility=_no_accessibility(),
    ).run(ToolRequest("vision.resolve_target", {"query": "点登录按钮"}))
    assert_true(not unclear_target.requires_approval and unclear_target.agent_state["needs_clarification"], "unclear semantic target should ask for clarification")

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

    image_test_dir = Path(tempfile.mkdtemp())
    try:
        image_before = image_test_dir / "before.ppm"
        image_after = image_test_dir / "after.ppm"
        image_same = image_test_dir / "same.ppm"
        image_unreadable = image_test_dir / "broken.ppm"
        _write_ppm(image_before, 80, 60, (20, 20, 20))
        _write_ppm(image_after, 80, 60, (20, 20, 20), patch=(8, 8, 34, 28, (235, 235, 235)))
        _write_ppm(image_same, 80, 60, (20, 20, 20))
        image_unreadable.write_text("not an image", encoding="utf-8")
        image_changed_before = _fake_computer_observation(workspace, rel="data/agent_companion/vision/hash-before.ppm", title="Same", ocr_text=["一样"], width=80, height=60)
        image_changed_after = _fake_computer_observation(workspace, rel="data/agent_companion/vision/hash-after.ppm", title="Same", ocr_text=["一样"], width=80, height=60)
        image_changed_before = ComputerObservation(**{**image_changed_before.__dict__, "screenshot_path": image_before})
        image_changed_after = ComputerObservation(**{**image_changed_after.__dict__, "screenshot_path": image_after})
        image_changed = verify_post_action(image_changed_before, image_changed_after)
        assert_true(image_changed.status == "changed", "pixel-different screenshots should verify as changed")
        assert_true(image_changed.signals.screenshot_changed is True and image_changed.signals.image_changed is True, "image diff signal should be positive")
        image_audit = computer_action_audit_event(
            "task-image-audit",
            ToolResult(
                ok=True,
                agent_state={
                    "tool": "computer.click",
                    "computer_use": {"action": {"type": "click", "x": 12, "y": 34}, "before_artifact": "data/agent_companion/vision/hash-before.ppm", "after_artifact": "data/agent_companion/vision/hash-after.ppm"},
                    "post_action_verification": image_changed.to_agent_state(),
                },
                display_card=DisplayCard("电脑操作", "操作后画面有变化。", status="success"),
                voice_line=safe_voice_line("操作后画面有变化。"),
                risk=RiskLevel.MEDIUM,
            ),
        )
        assert_true(image_audit is not None and image_audit.verification_result["signals"].get("image_changed") == "changed", "audit should include sanitized image verification signal")
        assert_true("12" not in str(image_audit.sanitized_arguments) and "34" not in str(image_audit.sanitized_arguments), "image audit should not leak coordinates")

        image_same_before = _fake_computer_observation(workspace, rel="data/agent_companion/vision/hash-same-before.ppm", title="Same", ocr_text=["一样"], width=80, height=60)
        image_same_after = _fake_computer_observation(workspace, rel="data/agent_companion/vision/hash-same-after.ppm", title="Same", ocr_text=["一样"], width=80, height=60)
        image_same_before = ComputerObservation(**{**image_same_before.__dict__, "screenshot_path": image_same})
        image_same_after = ComputerObservation(**{**image_same_after.__dict__, "screenshot_path": image_same})
        image_same_verification = verify_post_action(image_same_before, image_same_after)
        assert_true(image_same_verification.status == "likely_noop", "identical screenshots should be likely_noop when other signals are unchanged")
        assert_true(image_same_verification.signals.screenshot_changed is False, "identical screenshots should expose unchanged image signal")

        unreadable_before = ComputerObservation(**{**image_same_before.__dict__, "screenshot_path": image_unreadable})
        unreadable_after = ComputerObservation(**{**image_same_after.__dict__, "screenshot_path": image_after})
        unreadable_verification = verify_post_action(unreadable_before, unreadable_after)
        assert_true(unreadable_verification.signals.screenshot_changed is None and unreadable_verification.signals.image_changed is None, "unreadable screenshots should fall back safely")
    finally:
        import shutil
        shutil.rmtree(image_test_dir, ignore_errors=True)

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
    inconclusive_audit = computer_action_audit_event(
        "task-audit-test",
        ToolResult(
            ok=True,
            agent_state={
                "tool": "computer.click",
                "computer_use": {"action": {"type": "click", "x": 88, "y": 99}, "before_artifact": "data/agent_companion/vision/inconclusive-before.png", "after_artifact": "data/agent_companion/vision/inconclusive-after.png"},
                "post_action_verification": {"status": "inconclusive", "summary": "操作已执行，但变化不确定。", "signals": {"ocr_changed": None}},
            },
            display_card=DisplayCard("电脑操作", "操作已执行，但变化不确定。", status="info"),
            voice_line=safe_voice_line("操作执行了，暂时判断不了画面变化。"),
            risk=RiskLevel.MEDIUM,
        ),
    )
    assert_true(inconclusive_audit is not None and inconclusive_audit.event_type == "verification_inconclusive", "inconclusive verification should have a clear audit type")
    forbidden_computer_voice = ["10", "20", "88", "99", "Ctrl", "hello", "data/", ".png", ".ppm", "{", "task-", "完成", "相同", "提交", "新增内容"]
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
            ocr_regions=grouped_state,
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
    region_question_result = WatchRecallTool(workspace, lambda limit: answer_frames[:limit]).run(ToolRequest("watch.recall", {"query": "页面右上角是什么"}))
    assert_true("登录" in region_question_result.display_card.summary, "watch recall should answer top-right region questions from OCR regions")
    assert_true("ocr_regions" in region_question_result.agent_state["watch_context"][0], "watch context should expose OCR regions for planner")

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
    semantic_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(
                workspace,
                rel="data/agent_companion/vision/semantic-app.png",
                width=1000,
                height=1000,
                capture_rect=CaptureRect(100, 200, 1000, 1000),
            )
        ],
    )
    app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=semantic_backend,
            ocr=FakeOcrExtractor(region_ocr),
            accessibility=_no_accessibility(),
        )
    )
    semantic_events = app.handle_user_text("点登录按钮")
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED for event in semantic_events), "semantic target click should request approval")
    semantic_approval = _approval_payload(semantic_events)
    assert_true(semantic_approval.get("tool") == "computer.click", "semantic target approval should bind synthesized computer.click")
    assert_true(any("登录" in event.display_card.summary for event in semantic_events if event.type == EventType.APPROVAL_REQUIRED), "semantic approval card should name target")
    semantic_approval_event = [event for event in semantic_events if event.type == EventType.APPROVAL_REQUIRED][-1]
    assert_true("target_candidates" in semantic_approval_event.agent_state, "semantic approval event should preserve target candidates")
    assert_true(all("登录" not in event.voice_line.text and "semantic-app.png" not in event.voice_line.text for event in semantic_events), "semantic approval voice should stay immersive")
    semantic_refused = app.resolve_approval(str(semantic_approval["approval_id"]), approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in semantic_refused), "semantic target refusal should cancel action")
    assert_true(not semantic_backend.actions, "semantic target refusal must not execute click")

    app = AgentCompanionApp(workspace)
    computer_events = app.handle_user_text("点击 100,200")
    assert_true(any(event.type == EventType.APPROVAL_REQUIRED for event in computer_events), "computer action should require approval")
    computer_approval = _approval_payload(computer_events)
    computer_audit = _audit_rows(computer_events)
    assert_true(any(row.get("event_type") == "approval_pending" for row in computer_audit), "computer approval should create an audit event")
    approval_audit = [row for row in computer_audit if row.get("event_type") == "approval_pending"][-1]
    for field in ("task_id", "event_type", "timestamp", "sanitized_summary", "risk_level", "approval_id", "approval_status", "tool_name", "action_name", "sanitized_arguments", "before_artifacts", "after_artifacts", "verification_result"):
        assert_true(field in approval_audit, f"audit event missing stable field: {field}")
    assert_true(approval_audit["risk_level"] == "medium", "computer approval audit should preserve risk")
    assert_true(approval_audit["sanitized_arguments"].get("target") == "screen_position", "click audit should sanitize target coordinates")
    assert_true("100" not in str(approval_audit["sanitized_arguments"]) and "200" not in str(approval_audit["sanitized_arguments"]), "click audit leaked raw coordinates")
    assert_true(str(computer_approval.get("approval_id", "")).startswith("approval-"), "computer approval should include approval_id")
    assert_true(computer_approval.get("tool") == "computer.click", "computer approval should bind tool")
    assert_true(computer_approval.get("task_id") in {event.task_id for event in computer_events}, "computer approval should bind task_id")
    assert_true(computer_approval.get("step_index") == 0, "computer approval should bind step index")
    assert_true(len(str(computer_approval.get("arguments_hash", ""))) == 16, "computer approval should bind arguments hash")
    bypass = app.resolve_approval(str(computer_approval.get("task_id")), approved=True)
    assert_true(not bypass, "task_id must not work as approval id")
    refused = app.resolve_approval(str(computer_approval["approval_id"]), approved=False)
    assert_true(any(event.type == EventType.TASK_FAILED for event in refused), "approval refusal should cancel computer action")
    assert_true(any(row.get("event_type") == "approval_denied" for row in _audit_rows(refused)), "approval denial should create an audit entry")
    reused = app.resolve_approval(str(computer_approval["approval_id"]), approved=True)
    assert_true(any(event.type == EventType.AUDIT_EVENT for event in reused), "duplicate computer approval should create a clear audit entry")
    assert_true(any(row.get("event_type") == "approval_duplicate" for row in _audit_rows(reused)), "duplicate computer approval audit missing")

    type_audit_app = AgentCompanionApp(workspace)
    type_audit_events = type_audit_app.handle_user_text("输入文字：hello secret token")
    type_audit = _audit_rows(type_audit_events)
    assert_true(any(row.get("tool_name") == "computer.type_text" and row.get("sanitized_arguments", {}).get("input") == "typed_text_hidden" for row in type_audit), "type audit should hide raw typed text")
    assert_true("hello secret token" not in str(type_audit), "type audit leaked raw typed text")

    expired_approval_app = AgentCompanionApp(workspace)
    expired_approval_events = expired_approval_app.handle_user_text("点击 100,200")
    expired_approval = _approval_payload(expired_approval_events)
    expired_approval_app.approval_ttl_seconds = -1
    expired_approval_result = expired_approval_app.resolve_approval(str(expired_approval["approval_id"]), approved=True)
    assert_true(any(event.type == EventType.TASK_FAILED for event in expired_approval_result), "expired approval should fail safely")
    assert_true(any(row.get("event_type") == "approval_expired" for row in _audit_rows(expired_approval_result)), "expired approval should create an audit entry")
    assert_true(not any(event.type == EventType.TOOL_COMPLETED and event.agent_state.get("tool") == "computer.click" for event in expired_approval_result), "expired approval must not run the action")

    action_audit_app = AgentCompanionApp(workspace)
    action_audit_app.tools.register(ComputerActionTool(workspace, "computer.click", "click", FakeComputerBackend(workspace), post_action_settle_ms=0))
    action_audit_events = action_audit_app.handle_user_text("点击 100,200")
    action_audit_approval = _approval_payload(action_audit_events)
    action_audit_result = action_audit_app.resolve_approval(str(action_audit_approval["approval_id"]), approved=True)
    action_rows = _audit_rows(action_audit_events + action_audit_result)
    assert_true(any(row.get("event_type") == "approval_approved" for row in action_rows), "approved action should record approval lifecycle")
    noop_rows = [row for row in action_rows if row.get("event_type") == "verification_noop"]
    assert_true(noop_rows, "likely no-op verification should create an audit entry")
    assert_true(noop_rows[-1].get("verification_result", {}).get("status") == "likely_noop", "no-op audit should preserve verification status")
    assert_true(noop_rows[-1].get("before_artifacts") and noop_rows[-1].get("after_artifacts"), "confirmed action audit should include before/after screenshots")
    assert_true(not any(event.type == EventType.AUDIT_EVENT and event.voice_line.text for event in action_audit_result if "approval-" in event.voice_line.text), "audit events should not speak approval ids")

    unavailable_audit_app = AgentCompanionApp(workspace)
    unavailable_audit_app.tools.register(
        ComputerActionTool(
            workspace,
            "computer.hotkey",
            "hotkey",
            FakeComputerBackend(workspace, observations=[_fake_computer_observation(workspace, rel="data/agent_companion/vision/audit-after-hotkey.png")], fail_on_observe_calls={1}),
            post_action_settle_ms=0,
        )
    )
    unavailable_events = unavailable_audit_app.handle_user_text("按下 Ctrl+L 快捷键")
    unavailable_approval = _approval_payload(unavailable_events)
    unavailable_result = unavailable_audit_app.resolve_approval(str(unavailable_approval["approval_id"]), approved=True)
    assert_true(any(row.get("event_type") == "verification_unavailable" for row in _audit_rows(unavailable_events + unavailable_result)), "unavailable verification should create an audit entry")
    computer_voice_forbidden = ["100", "200", "approval-", "task-", "data/", ".png", ".ppm", "{", "hello secret token", "Ctrl"]
    computer_voice_events = computer_events + refused + reused + expired_approval_result + action_audit_result + unavailable_result + type_audit_events
    assert_true(all(not any(fragment in event.voice_line.text for fragment in computer_voice_forbidden) for event in computer_voice_events), "computer audit lifecycle voice leaked raw machine details")

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
    assert_true(ready_payload["runtime"]["read_only"] and ready_payload["runtime"]["safe_for_display"], "Core ready payload should expose safe read-only runtime status")
    runtime_provider_names = {row["name"] for row in ready_payload["runtime"]["providers"]}
    assert_true(
        {"asr", "tts", "ocr", "text", "vision", "expression", "computer_use", "audit_verification"}.issubset(runtime_provider_names),
        "Runtime status should include provider, platform, audit, and verification rows",
    )
    runtime_payload_text = str(ready_payload["runtime"])
    forbidden_runtime_fragments = ["sk-", "server_url", "gpt_sovits_work_path", "base_url", "api_key", "/Users/", "C:\\", "secret"]
    assert_true(
        all(fragment not in runtime_payload_text for fragment in forbidden_runtime_fragments),
        "Runtime status leaked secrets, endpoints, or private paths",
    )

    with tempfile.TemporaryDirectory() as runtime_tmp_name:
        runtime_tmp = Path(runtime_tmp_name)
        (runtime_tmp / "config.yaml").write_text(
            """
llm:
  provider: openai_compatible
  use_mock: false
  base_url: https://api.private.example/v1
  model: gpt-public-text
  api_key: sk-test-private
  vision_enabled: true
  vision_model: gpt-public-vision
  vision_api_key: sk-test-vision
  expression_enabled: true
  expression_model: gpt-public-expression
  expression_api_key: sk-test-expression
tts:
  enabled: true
  provider: gpt-sovits
  server_url: http://127.0.0.1:9880/
  gpt_sovits_work_path: /Users/private/GPT-SoVITS
ocr:
  timeout_seconds: 7
computer_use:
  post_action_settle_ms: 325
""",
            encoding="utf-8",
        )
        configured_runtime = build_runtime_status(
            runtime_tmp,
            AsrRuntimeState(True, True, "openai_compatible", max_seconds=12, max_bytes=2048, timeout_seconds=9),
            {"enabled": True, "configured": True, "provider": "gpt-sovits", "last_error": "tts_timeout"},
        )
        configured_rows = {row["name"]: row for row in configured_runtime["providers"]}
        assert_true(configured_rows["asr"]["state"] == "ready" and configured_rows["asr"]["timeout_seconds"] == 9, "ASR runtime status should show configured fake state")
        assert_true(configured_rows["tts"]["state"] == "ready" and configured_rows["tts"]["last_error"] == "tts_timeout", "TTS runtime status should show sanitized configured state")
        assert_true(configured_rows["text"]["model"] == "gpt-public-text", "Text model status should expose safe public model name")
        assert_true(configured_rows["vision"]["model"] == "gpt-public-vision", "Vision model status should expose safe public model name")
        assert_true(configured_rows["expression"]["model"] == "gpt-public-expression", "Expression model status should expose safe public model name")
        configured_text = str(configured_runtime)
        assert_true(
            "sk-test" not in configured_text
            and "api.private" not in configured_text
            and "GPT-SoVITS" not in configured_text
            and "/Users/private" not in configured_text,
            "Configured runtime status should redact keys, endpoints, and local TTS/model paths",
        )

    with tempfile.TemporaryDirectory() as runtime_tmp_name:
        runtime_tmp = Path(runtime_tmp_name)
        original_find_spec = runtime_status_module.importlib.util.find_spec

        def fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
            if name in {"PIL", "pytesseract"}:
                return None
            return original_find_spec(name, *args, **kwargs)

        runtime_status_module.importlib.util.find_spec = fake_find_spec
        try:
            unconfigured_runtime = build_runtime_status(
                runtime_tmp,
                AsrRuntimeState(False, False, "none", error="asr_unconfigured"),
                {"enabled": False, "configured": False, "provider": "none", "last_error": ""},
            )
        finally:
            runtime_status_module.importlib.util.find_spec = original_find_spec
        unconfigured_rows = {row["name"]: row for row in unconfigured_runtime["providers"]}
        assert_true(unconfigured_rows["asr"]["state"] == "off" and unconfigured_rows["tts"]["state"] == "off", "Unconfigured ASR/TTS runtime states should be off")
        assert_true(unconfigured_rows["ocr"]["state"] == "unavailable" and "pytesseract_missing" in unconfigured_rows["ocr"]["last_error"], "OCR runtime status should report missing optional dependencies safely")
        assert_true(unconfigured_rows["text"]["state"] == "off" and unconfigured_rows["vision"]["state"] == "off" and unconfigured_rows["expression"]["state"] == "off", "Unconfigured model runtime states should be off")

    with tempfile.TemporaryDirectory() as ocr_tmp_name:
        ocr_tmp = Path(ocr_tmp_name)
        fake_tesseract_path = "/Users/private/bin/tesseract-real"
        pil_missing_runtime = _runtime_with_ocr_probe(
            ocr_tmp,
            has_pillow=False,
            has_pytesseract=True,
            tesseract_path=fake_tesseract_path,
            version_probe=True,
        )
        pil_missing = _ocr_status_row(pil_missing_runtime)
        assert_true(pil_missing["state"] == "unavailable" and not pil_missing["configured"], "OCR should be unavailable when Pillow is missing")
        assert_true(pil_missing["last_error"] == "pillow_missing", "OCR should report sanitized Pillow missing category")
        _assert_runtime_payload_has_no_probe_leaks(pil_missing_runtime)

        pytesseract_missing_runtime = _runtime_with_ocr_probe(
            ocr_tmp,
            has_pillow=True,
            has_pytesseract=False,
            tesseract_path=fake_tesseract_path,
            version_probe=True,
        )
        pytesseract_missing = _ocr_status_row(pytesseract_missing_runtime)
        assert_true(pytesseract_missing["state"] == "unavailable" and not pytesseract_missing["configured"], "OCR should be unavailable when pytesseract is missing")
        assert_true(pytesseract_missing["last_error"] == "pytesseract_missing", "OCR should report sanitized pytesseract missing category")
        _assert_runtime_payload_has_no_probe_leaks(pytesseract_missing_runtime)

        executable_missing_runtime = _runtime_with_ocr_probe(
            ocr_tmp,
            has_pillow=True,
            has_pytesseract=True,
            tesseract_path=None,
            version_probe=True,
        )
        executable_missing = _ocr_status_row(executable_missing_runtime)
        assert_true(executable_missing["state"] == "unavailable" and not executable_missing["configured"], "OCR should be unavailable when the system tesseract executable is missing")
        assert_true(executable_missing["last_error"] == "tesseract_missing", "OCR should report sanitized tesseract executable missing category")
        _assert_runtime_payload_has_no_probe_leaks(executable_missing_runtime)

        probe_failed_runtime = _runtime_with_ocr_probe(
            ocr_tmp,
            has_pillow=True,
            has_pytesseract=True,
            tesseract_path=fake_tesseract_path,
            version_probe=RuntimeError("stderr raw /Users/private/tesseract.log sk-test-secret token C:\\secret\\tesseract.exe"),
        )
        probe_failed = _ocr_status_row(probe_failed_runtime)
        assert_true(probe_failed["state"] == "unavailable" and not probe_failed["configured"], "OCR should be unavailable when tesseract version probe fails")
        assert_true(probe_failed["last_error"] == "tesseract_unavailable", "OCR should report sanitized tesseract probe failure category")
        _assert_runtime_payload_has_no_probe_leaks(probe_failed_runtime)

        ready_ocr_runtime = _runtime_with_ocr_probe(
            ocr_tmp,
            has_pillow=True,
            has_pytesseract=True,
            tesseract_path=fake_tesseract_path,
            version_probe=True,
        )
        ready_ocr = _ocr_status_row(ready_ocr_runtime)
        assert_true(ready_ocr["state"] == "ready" and ready_ocr["configured"], "OCR should only be ready when package, executable, and version probe all pass")
        assert_true(ready_ocr["last_error"] == "", "Ready OCR status should not carry a stale error")
        _assert_runtime_payload_has_no_probe_leaks(ready_ocr_runtime)

    with tempfile.TemporaryDirectory() as runtime_tmp_name:
        runtime_tmp = Path(runtime_tmp_name)
        (runtime_tmp / "config.yaml").write_text(
            """
llm:
  provider: openai_compatible
  use_mock: false
  model: /Users/private/models/joi.gguf
  api_key: sk-test-private
""",
            encoding="utf-8",
        )
        redacted_runtime = build_runtime_status(
            runtime_tmp,
            AsrRuntimeState(False, False, "none"),
            {"enabled": False, "configured": False, "provider": "none", "last_error": ""},
        )
        redacted_rows = {row["name"]: row for row in redacted_runtime["providers"]}
        assert_true(redacted_rows["text"]["model"] == "redacted", "Local model paths should be redacted from runtime status")
        assert_true("/Users/private" not in str(redacted_runtime) and "joi.gguf" not in str(redacted_runtime), "Runtime status should not expose local model paths")

    shell_api_source = (workspace / "agent_companion" / "shell" / "src" / "api.ts").read_text(encoding="utf-8")
    assert_true("transcribeVoice(audioBase64: string, mimeType: string, timeoutMs: number)" in shell_api_source, "voice RPC should accept a method-specific timeout")
    assert_true("语音识别等太久了" in shell_api_source, "voice RPC timeout should be user-friendly")
    voice_runtime_source = (workspace / "agent_companion" / "shell" / "src" / "voiceRuntime.ts").read_text(encoding="utf-8")
    assert_true("shouldPlayVoiceAudio" in voice_runtime_source and "eventEpoch === currentEpoch" in voice_runtime_source, "voice runtime should suppress stale audio by epoch")
    assert_true("event_created_at" in voice_runtime_source, "voice runtime key should include event identity")
    app_vue_source = (workspace / "agent_companion" / "shell" / "src" / "App.vue").read_text(encoding="utf-8")
    assert_true("beginNewVoiceIntent()" in app_vue_source and "voiceEventEpochs.get" in app_vue_source, "Shell should bump and compare voice epochs")
    assert_true("event_created_at: event.created_at" in app_vue_source, "Shell should key voice audio by event timestamp")
    assert_true("runtimeStatusRows" in app_vue_source and "provider-card" in app_vue_source and "运行设置" in app_vue_source, "Shell developer mode should expose runtime provider settings/status view")
    assert_true("providerMeta" in app_vue_source and "providerErrorLabel" in app_vue_source, "Shell runtime status view should render sanitized provider details")
    assert_true("tesseract_missing" in app_vue_source and "tesseract_unavailable" in app_vue_source, "Shell runtime status view should label Tesseract runtime probe failures")
    assert_true("target-overlays" in app_vue_source and "targetPreviewSummary" in app_vue_source, "Shell should render semantic target approval previews")
    assert_true("target-list" in app_vue_source and "targetRank" in app_vue_source, "Shell should show ranked semantic target candidates")
    assert_true("targetSource" in app_vue_source and "UI控件" in app_vue_source and "融合" in app_vue_source and "视觉" in app_vue_source, "Shell should show semantic target candidate source")
    assert_true("selectTargetCandidate" in app_vue_source and "selectSemanticTarget" in app_vue_source, "Shell candidate cards should continue semantic target selection through explicit RPC")
    assert_true("currentSemanticSelectionId" in app_vue_source and "selectionExpired" in app_vue_source, "Shell should disable stale or expired semantic target candidates")
    assert_true("选 ${rank}" not in app_vue_source, "Shell candidate buttons should not send natural-language selection text")
    assert_true("audit-panel" in app_vue_source and "auditEventsForTask" in app_vue_source, "Shell developer mode should render Computer Use audit timeline")
    assert_true("auditArgumentRows" in app_vue_source and "auditArtifacts" in app_vue_source, "Shell audit view should show sanitized arguments and before/after artifacts")
    assert_true("auditSignalRows" in app_vue_source and "image_changed" in app_vue_source, "Shell audit view should show sanitized image verification signals")
    visual_fixture_manifest = (workspace / "tests" / "fixtures" / "visual_detector" / "visual_cases.json").read_text(encoding="utf-8")
    image_fixture_manifest = (workspace / "tests" / "fixtures" / "image_verification" / "image_diff_cases.json").read_text(encoding="utf-8")
    assert_true("video_canvas_controls" in visual_fixture_manifest and "canvas_button_cluster" in visual_fixture_manifest, "committed visual detector regression fixtures should be present")
    assert_true("sparse_page_bottom_action_strip" in visual_fixture_manifest and "modal_low_contrast_actions" in visual_fixture_manifest, "promoted synthetic visual calibration fixtures should be present")
    assert_true("image_diff_subtle_visible_change" in image_fixture_manifest and "image_diff_tiny_compression_noise" in image_fixture_manifest, "committed image-diff regression fixtures should be present")
    assert_true("image_diff_thin_progress_change" in image_fixture_manifest and "image_diff_cursor_blink_noop" in image_fixture_manifest, "promoted synthetic image-diff calibration fixtures should be present")
    eval_source = (workspace / "tools" / "eval_visual_detector.py").read_text(encoding="utf-8")
    assert_true("local private image verification eval: skipped" in eval_source and "image_diff_cases.local.json" in eval_source, "local private image-diff eval should skip when missing")
    assert_true("_print_private_results" in eval_source and "failure_category" in eval_source and "local_private_case_" in eval_source, "local private eval output should be sanitized")
    assert_true(run_visual_detector_eval(workspace, verbose=False) == 0, "visual/image verification eval should pass committed suites and skip or run local private suites safely")
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
