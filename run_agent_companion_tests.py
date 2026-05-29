from __future__ import annotations

import asyncio
import base64
import json
import os
import inspect
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.audit_store import AUDIT_SCHEMA_VERSION, AuditStore
from agent_companion.core.background_context import BACKGROUND_CONTEXT_VERSION, BackgroundContextStore
from agent_companion.core.computer_use import COMPUTER_AUDIT_STATE_KEY, ComputerAction, ComputerObservation, ComputerUseResult, computer_action_audit_event, verify_post_action
from agent_companion.core.config import LlmConfig, ModelEndpoint, ModelRouteConfig, ModelRouter, load_app_config
from agent_companion.core.llm_planner import plan_from_llm_payload
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core import runtime_status as runtime_status_module
from agent_companion.core.runtime_config_writer import preview_runtime_config_update, update_runtime_config
from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, RiskLevel, ToolRequest, ToolResult, VoiceLine
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.skill_manifest import SKILL_MANIFEST_VERSION, build_native_skill_manifest
from agent_companion.core.speech_input import AsrResult, AsrRuntimeState, MockAsrProvider, OpenAICompatibleAsrProvider, build_asr_provider
from agent_companion.core.tool_compression import compress_tool_result
from agent_companion.core.tools.browser import BrowserTool
from agent_companion.core.tools.chat import CompanionChatTool
from agent_companion.core.tools.computer import ComputerActionTool
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.tools.desktop_workflow import DesktopWorkflowTool
from agent_companion.core.tools.runtime_config import RuntimeConfigUpdateTool
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.tools.targeting import SemanticTargetTool, click_arguments_from_state
from agent_companion.core.tools.watch import WatchRecallTool
from agent_companion.core.vision.accessibility import AccessibilitySnapshot, AccessibleElement, _looks_clickable
from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock, PytesseractOcrExtractor, UnavailableOcrExtractor
from agent_companion.core.vision.regions import group_ocr_regions
from agent_companion.core.vision.schemas import CaptureRect, VisionObservation
from agent_companion.core.vision.summarizer import MockSummarizer, OpenAIVisionSummarizer, VisionSummary
from agent_companion.core.vision.targeting import resolve_target_candidates
from agent_companion.core.vision.visual_detector import UnavailableVisualDetector, VisualCandidate, VisualDetectionResult
from agent_companion.core.voice import safe_voice_line, sprite_for_emotion, strip_emotion_token
from agent_companion.core.watch import WatchFrame, WatchSession
from agent_companion.core.watch_commentary import WatchCommentaryPlanner
from agent_companion.core.watch_transcript import TranscriptResult, TranscriptSegment
from tools.eval_visual_detector import SEMANTIC_CALIBRATION_FAILURE_CATEGORIES, run_eval as run_visual_detector_eval, run_local_semantic_calibration
from tools.joi_doctor import build_doctor_report, doctor_exit_code
from tools.mvp_demo_check import build_mvp_demo_check_report, mvp_demo_check_exit_code
from tools.package_windows_release import build_release_privacy_report, build_windows_release_package, package_exit_code
from tools.packaging_smoke import build_packaging_smoke_report, packaging_smoke_exit_code
from tools.provider_preflight import build_provider_preflight_report, provider_preflight_exit_code
from tools.windows_release_check import build_windows_release_check_report, windows_release_check_exit_code
from tools.windows_setup_wizard import build_windows_setup_plan, windows_setup_exit_code
from tools.windows_handoff_report import build_windows_handoff_report, windows_handoff_exit_code


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

    def fake_probe(*args: object, **kwargs: object) -> bool:
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


def _assert_config_mutation_payload_safe(payload: object, message: str) -> None:
    text = str(payload)
    forbidden = [
        "/Users/",
        "C:\\",
        "token",
        "secret",
        "https://",
        "http://",
        "server_url",
        "api_key",
        "joi.gguf",
        "voice.wav",
        ".yaml",
    ]
    assert_true(all(fragment not in text for fragment in forbidden), message)
    assert_true(re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{4,}", text) is None, message)


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


class FakeLlmPlanner:
    def __init__(self, plan: object | None = None) -> None:
        self.plan_to_return = plan
        self.calls: list[tuple[str, str]] = []

    def plan(self, user_text: str, rule_plan: object) -> object | None:
        self.calls.append((user_text, getattr(rule_plan, "intent", "")))
        return self.plan_to_return


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


class SequenceSummarizer:
    def __init__(self) -> None:
        self.sequence_calls: list[tuple[int, str, list[str]]] = []

    def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
        return VisionSummary("单帧摘要不应用于视频采样。", model="fake")

    def summarize_sequence(self, observations: list[VisionObservation], query: str = "", ocr_texts: list[str] | None = None) -> VisionSummary:
        self.sequence_calls.append((len(observations), query, list(ocr_texts or [])))
        return VisionSummary("连续画面显示一只猫在做亲身实验，弹幕和字幕都围绕猫的反应展开。", model="fake-sequence")


class FakeAudioTranscriber:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def transcribe(self, seconds: float = 5.0) -> TranscriptResult:
        self.calls.append(seconds)
        return TranscriptResult(
            "success",
            "system_audio",
            [TranscriptSegment("系统音频说：小猫正在亲身实验。", 0, int(seconds * 1000), "system_audio", 0.91, 1, "audio")],
            "识别到 1 段系统音频转写。",
        )


class FailingAudioTranscriber:
    def __init__(self, error: str = "asr_unconfigured") -> None:
        self.error = error
        self.calls: list[float] = []

    def transcribe(self, seconds: float = 5.0) -> TranscriptResult:
        self.calls.append(seconds)
        return TranscriptResult("failed", "system_audio", [], "音频转写没有生成文本。", self.error)


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


def _approval_payload_from_dicts(events: list[dict]) -> dict:
    for event in events:
        if event.get("type") == EventType.APPROVAL_REQUIRED.value:
            approval = event.get("agent_state", {}).get("approval")
            if isinstance(approval, dict):
                return approval
    return {}


class _FakeRpcWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def _runtime_apply_rpc_result(bridge: JsonRpcBridge, updates: object, request_id: str) -> dict:
    socket = _FakeRpcWebSocket()
    raw = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "runtime.config.apply", "params": {"updates": updates}}, ensure_ascii=False)
    asyncio.run(bridge._handle_message(socket, raw))
    assert_true(bool(socket.messages), "runtime config apply RPC should send a JSON-RPC response")
    response = json.loads(socket.messages[-1])
    result = response.get("result")
    assert_true(isinstance(result, dict), "runtime config apply RPC should return a result object")
    return result


def _audit_rows(events) -> list[dict]:
    rows: list[dict] = []
    for event in events:
        audit = event.agent_state.get(COMPUTER_AUDIT_STATE_KEY)
        if isinstance(audit, list):
            rows.extend(row for row in audit if isinstance(row, dict))
    return rows


def _write_fake_codex_executable(directory: Path) -> Path:
    script_path = directory / "fake_codex.py"
    script_path.write_text(
        """
import json
import os
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("fake codex 0.0")
    raise SystemExit(0)

def arg_after(flag):
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except Exception:
        return ""

def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)

mode = os.environ.get("JOI_FAKE_CODEX_MODE", "success")
resume_token = os.environ.get("AGENT_COMPANION_CODEX_RESUME_TOKEN", "")
final_path = arg_after("--output-last-message")
emit({"type": "started", "message": "Started in /Users/private/project with token sk-test-secret"})

if mode == "permission" and not resume_token:
    emit({
        "type": "permission_request",
        "message": "Run command cat /Users/me/secret.log --token sk-test",
        "tool": "shell.run",
        "arguments": {"cmd": "cat /Users/me/secret.log", "approval_id": "approval-abcdef123456"},
        "resume_supported": True,
        "resume_token": "resume-safe-token",
    })
    raise SystemExit(0)

if mode == "permission_no_resume":
    emit({
        "type": "permission_request",
        "message": "Escalation required for C:\\\\secret\\\\run.ps1 sk-test",
        "tool": "shell.run",
        "arguments": {"cmd": "powershell C:\\\\secret\\\\run.ps1"},
        "resume_supported": False,
    })
    raise SystemExit(0)

if mode == "fail":
    emit({"type": "error", "message": "Traceback /Users/me/error.log stderr sk-test approval-abcdef123456"})
    sys.stderr.write("Traceback /Users/me/raw.log stderr token sk-test approval-abcdef123456\\n")
    raise SystemExit(7)

emit({"type": "progress", "message": "Working safely"})
if final_path:
    Path(final_path).write_text("Final touched /Users/me/project/app.py with command --danger and token sk-test", encoding="utf-8")
emit({"type": "final", "message": "Done"})
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        shim_path = directory / "codex.cmd"
        shim_path.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
    else:
        shim_path = directory / "codex"
        shim_path.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script_path))} \"$@\"\n", encoding="utf-8")
        os.chmod(shim_path, 0o755)
    return shim_path


def _assert_no_codex_voice_leaks(events_or_results, message: str) -> None:
    forbidden = [
        "/Users/",
        "C:\\",
        "data/",
        ".jsonl",
        ".log",
        ".txt",
        ".py",
        "{",
        "}",
        "sk-",
        "secret",
        "token",
        "stderr",
        "Traceback",
        "approval-",
        "task-",
        "resume-",
        "--",
        "cat ",
    ]
    lines = []
    for item in events_or_results:
        voice = getattr(item, "voice_line", None)
        if voice is not None:
            lines.append(voice.text)
    assert_true(all(not any(fragment in line for fragment in forbidden) for line in lines), message)


def _assert_no_codex_safe_text_leaks(payload: object, message: str) -> None:
    text = str(payload)
    forbidden = [
        "/Users/",
        "C:\\",
        "secret.log",
        "raw.log",
        "error.log",
        "app.py",
        "sk-",
        "approval-abcdef",
        "resume-safe-token",
        "--token",
        "--danger",
        "Traceback",
        "stderr raw",
    ]
    assert_true(all(fragment not in text for fragment in forbidden), message)


def _selection_id(events) -> str:
    for event in events:
        selection_id = event.agent_state.get("selection_id")
        if isinstance(selection_id, str) and selection_id:
            return selection_id
    return ""


_FORBIDDEN_PRIVATE_CALIBRATION_OUTPUT = [
    "data/",
    "local_visual_eval",
    ".png",
    ".ppm",
    "C:\\",
    "/Users/",
    "http",
    "example",
    "Traceback",
    "账号",
    "真实目标",
    "Alice",
]


def _run_private_semantic_calibration_probe(workspace: Path, name: str, payload: object, *, expect_success: bool = False) -> str:
    manifest_dir = workspace / "data" / "local_visual_eval"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / name
    if isinstance(payload, str):
        manifest.write_text(payload, encoding="utf-8")
    else:
        manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        probe = subprocess.run(
            [
                sys.executable,
                str(workspace / "tools" / "calibrate_semantic_grounding.py"),
                "--manifest",
                str(manifest),
            ],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        manifest.unlink(missing_ok=True)
    output = f"{probe.stdout}\n{probe.stderr}"
    expected_returncode = 0 if expect_success else 1
    assert_true(probe.returncode == expected_returncode, f"private calibration probe should exit {expected_returncode}")
    assert_true(
        all(fragment not in output for fragment in _FORBIDDEN_PRIVATE_CALIBRATION_OUTPUT),
        "malformed private calibration output leaked private manifest details",
    )
    return output


def _run_p4_closeout_report_tool(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(workspace / "tools" / "p4_closeout_report.py"),
            *args,
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_vision_summary_empty_retry(workspace: Path) -> None:
    image_path = workspace / "data" / "agent_companion" / "vision" / "summarizer-fallback-test.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"not-a-real-png-but-good-enough-for-base64")

    class EmptyThenSummary(OpenAIVisionSummarizer):
        def __init__(self) -> None:
            super().__init__("https://unused.invalid/v1", "mimo-test", "test-key")
            self.prompts: list[str] = []

        def _request_summary(self, b64: str, prompt: str) -> VisionSummary:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return VisionSummary(text="", model=self.model)
            return VisionSummary(text="这是可见画面的摘要。", model=self.model)

    try:
        summarizer = EmptyThenSummary()
        result = summarizer.summarize(
            VisionObservation(
                target="active_window",
                screenshot_path=image_path,
                screenshot_rel="data/agent_companion/vision/summarizer-fallback-test.png",
                width=640,
                height=360,
                title="Bilibili",
                window_handle=123,
                capture_rect=CaptureRect(0, 0, 640, 360),
                query="陪我看这个视频",
            ),
            "陪我看这个视频",
        )
        assert_true(result.text == "这是可见画面的摘要。", "vision summarizer should retry once when the first provider response is empty")
        assert_true(len(summarizer.prompts) == 2, "vision summarizer should issue exactly one fallback request after an empty response")
        assert_true("不要输出 JSON" in summarizer.prompts[1], "vision fallback prompt should stay user-facing and machine-text free")
    finally:
        image_path.unlink(missing_ok=True)


def main() -> int:
    workspace = Path(__file__).resolve().parent
    os.environ["AGENT_COMPANION_DISABLE_LLM"] = "1"
    os.environ["AGENT_COMPANION_BROWSER_STUB"] = "1"
    _assert_vision_summary_empty_retry(workspace)
    assert_true("approved" not in inspect.signature(AgentCompanionApp.handle_user_text).parameters, "user message should not accept approval bypass")
    assert_true(build_plan("修复这个项目 bug 并跑测试").intent == "coding", "coding route failed")
    watch_plan = build_plan("陪我看这个视频")
    assert_true(watch_plan.intent == "watch_together", "watch route failed")
    assert_true(watch_plan.steps[0].name == "observe.screen", "watch route should use screen observation")
    live_video_plan = build_plan("这个视频在讲什么")
    assert_true(live_video_plan.intent == "watch_together", "current video content questions should refresh screen observation")
    assert_true(live_video_plan.steps[0].name == "observe.screen", "current video questions should observe the active video window")
    assert_true(live_video_plan.steps[0].arguments.get("sample_count") == 4, "current video questions should request a denser temporal sample")
    transcript_plan = build_plan("实时转写当前视频")
    assert_true(transcript_plan.intent == "watch_together", "video transcription should use watch route")
    assert_true(transcript_plan.steps[0].arguments.get("transcribe") is True and transcript_plan.steps[0].arguments.get("sample_count") == 8, "video transcription should request OCR subtitle transcript sampling")
    audio_transcript_plan = build_plan("用系统音频听一下这个视频")
    assert_true(audio_transcript_plan.steps[0].arguments.get("transcript_source") == "system_audio", "system audio requests should select audio transcript source")
    current_page_plan = build_plan("看看当前页面")
    assert_true(current_page_plan.intent == "watch_together" and current_page_plan.steps[0].name == "observe.screen", "current browser page should use screen observation")
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
    open_codex_plan = build_plan("请帮我打开codex")
    assert_true(open_codex_plan.intent == "desktop_workflow", "open app route should use desktop workflow")
    assert_true(open_codex_plan.steps[0].name == "computer.workflow", "open app should use computer.workflow")
    assert_true(open_codex_plan.steps[0].arguments.get("workflow") == "open_app", "open app workflow not parsed")
    edge_bili_plan = build_plan("用edge打开bilibili并搜索猫视频")
    assert_true(edge_bili_plan.intent == "desktop_workflow", "browser desktop workflow route failed")
    assert_true(edge_bili_plan.steps[0].arguments.get("workflow") == "open_web_search", "browser workflow not parsed")
    assert_true(edge_bili_plan.steps[0].arguments.get("site") == "bilibili", "bilibili site not parsed")
    assert_true(edge_bili_plan.steps[0].arguments.get("query") == "猫视频", "site search query not parsed")
    short_bili_search_plan = build_plan("打开b站搜猫猫视频")
    assert_true(short_bili_search_plan.intent == "desktop_workflow", "short Bilibili search should use desktop workflow")
    assert_true(short_bili_search_plan.steps[0].arguments.get("site") == "bilibili", "short Bilibili site not parsed")
    assert_true(short_bili_search_plan.steps[0].arguments.get("query") == "猫猫视频", "short Bilibili search query not parsed")
    explicit_site_search_plan = build_plan("在B站搜猫猫视频")
    assert_true(explicit_site_search_plan.intent == "desktop_workflow", "site search without open verb should use desktop workflow")
    assert_true(explicit_site_search_plan.steps[0].arguments.get("query") == "猫猫视频", "site search without open verb query not parsed")
    open_bili_plan = build_plan("打开B站")
    assert_true(open_bili_plan.steps[0].arguments.get("workflow") == "open_web_search", "site open should use browser workflow")
    plain_search_plan = build_plan("搜索猫猫视频")
    assert_true(plain_search_plan.intent == "browser" and plain_search_plan.steps[0].name == "browser.search", "generic search should still use browser route without desktop context")
    plain_short_search_plan = build_plan("搜猫猫视频")
    assert_true(plain_short_search_plan.intent == "browser" and plain_short_search_plan.steps[0].arguments.get("query") == "猫猫视频", "short generic search should still use browser route")
    llm_bili_plan = plan_from_llm_payload(
        "帮我在哔哩找猫猫视频",
        {
            "intent": "desktop_workflow",
            "confidence": 0.91,
            "action": "open_web_search",
            "slots": {"browser": "edge", "site": "bilibili", "query": "猫猫视频"},
        },
        task_id="task-llm-bili",
    )
    assert_true(llm_bili_plan is not None and llm_bili_plan.intent == "desktop_workflow", "LLM planner should produce desktop workflow")
    assert_true(llm_bili_plan.steps[0].name == "computer.workflow", "LLM desktop plan should use computer.workflow")
    assert_true(llm_bili_plan.steps[0].arguments.get("site") == "bilibili" and llm_bili_plan.steps[0].arguments.get("query") == "猫猫视频", "LLM planner should preserve safe site search slots")
    llm_video_plan = plan_from_llm_payload(
        "这个视频在讲什么",
        {"intent": "watch_together", "confidence": 0.88, "action": "observe_screen", "slots": {"query": "这个视频在讲什么"}},
        task_id="task-llm-video",
    )
    assert_true(llm_video_plan is not None and llm_video_plan.steps[0].name == "observe.screen", "LLM video watch plan should observe screen")
    assert_true(llm_video_plan.steps[0].arguments.get("sample_count") == 4, "LLM video watch plan should request temporal sampling")
    llm_transcript_plan = plan_from_llm_payload(
        "实时转写当前视频",
        {"intent": "watch_together", "confidence": 0.88, "action": "observe_screen", "slots": {"query": "实时转写当前视频", "transcribe": True}},
        task_id="task-llm-transcript",
    )
    assert_true(llm_transcript_plan is not None and llm_transcript_plan.steps[0].arguments.get("transcribe") is True, "LLM transcript plan should preserve transcript sampling")
    llm_click_plan = plan_from_llm_payload(
        "点右上角登录",
        {"intent": "computer_use", "confidence": 0.86, "action": "click_target", "slots": {"query": "右上角登录"}},
        task_id="task-llm-click",
    )
    assert_true(llm_click_plan is not None and llm_click_plan.intent == "semantic_target", "LLM click plan should route through semantic target grounding")
    assert_true(llm_click_plan.steps[0].name == "vision.resolve_target" and "x" not in llm_click_plan.steps[0].arguments, "LLM planner must not create raw coordinate clicks")
    assert_true(plan_from_llm_payload("危险动作", {"intent": "shell.run", "confidence": 0.99, "slots": {"command": "rm -rf ."}}) is None, "LLM planner should reject unsupported tools")
    assert_true(plan_from_llm_payload("低置信度", {"intent": "desktop_workflow", "confidence": 0.2, "action": "open_app", "slots": {"app": "Codex"}}) is None, "LLM planner should reject low confidence")
    fake_llm_plan = plan_from_llm_payload(
        "帮我在哔哩找猫猫视频",
        {"intent": "desktop_workflow", "confidence": 0.9, "action": "open_web_search", "slots": {"site": "bilibili", "query": "猫猫视频"}},
        task_id="task-fake-llm",
    )
    fake_llm_app = AgentCompanionApp(workspace, llm_planner=FakeLlmPlanner(fake_llm_plan))
    fake_llm_events = fake_llm_app.handle_user_text("帮我在哔哩找猫猫视频")
    assert_true(any(event.type == EventType.PLAN_CREATED and event.agent_state.get("steps") == ["computer.workflow"] for event in fake_llm_events), "Agent app should use LLM planner fallback for ambiguous actionable text")
    fake_plan_event = [event for event in fake_llm_events if event.type == EventType.PLAN_CREATED][-1]
    assert_true(fake_plan_event.agent_state.get("skill_steps", [{}])[0].get("skill_id") == "joi.computer_use", "Plan events should bind tool steps to native skill ids")
    fake_llm_approval = _approval_payload(fake_llm_events)
    assert_true(fake_llm_approval.get("tool") == "computer.workflow", "LLM-planned desktop workflow should still require approval")
    fake_llm_approval_event = [event for event in fake_llm_events if event.type == EventType.APPROVAL_REQUIRED][-1]
    assert_true(fake_llm_approval_event.agent_state.get("skill_id") == "joi.computer_use" and fake_llm_approval_event.agent_state.get("skill_permission_level") == "medium", "Approval events should carry native skill boundary metadata")
    fake_llm_pending = fake_llm_app.pending_steps[fake_llm_approval["approval_id"]]
    fake_llm_args = fake_llm_pending.plan.steps[fake_llm_pending.index].arguments
    assert_true(fake_llm_args.get("site") == "bilibili" and fake_llm_args.get("query") == "猫猫视频", "LLM-planned approval should keep validated slots")
    desktop_context_app = AgentCompanionApp(workspace)
    desktop_context_app._record_desktop_context(
        open_bili_plan,
        open_bili_plan.steps[0],
        ToolResult(
            ok=True,
            agent_state={"tool": "computer.workflow"},
            display_card=DisplayCard("电脑操作", "已打开 B站。", status="success"),
            voice_line=safe_voice_line("打开了。"),
            risk=RiskLevel.MEDIUM,
        ),
    )
    rewritten_search_plan = desktop_context_app._rewrite_plan_for_desktop_context(plain_search_plan)
    assert_true(rewritten_search_plan.intent == "desktop_workflow", "desktop site context should rewrite follow-up search to desktop workflow")
    assert_true(rewritten_search_plan.steps[0].name == "computer.workflow", "follow-up site search should not use built-in browser")
    assert_true(rewritten_search_plan.steps[0].arguments.get("site") == "bilibili", "follow-up site search should keep Bilibili context")
    assert_true(rewritten_search_plan.steps[0].arguments.get("query") == "猫猫视频", "follow-up site search should preserve query")
    desktop_search_events = desktop_context_app.handle_user_text("搜索猫猫视频")
    assert_true(any(event.type == EventType.PLAN_CREATED and event.agent_state.get("steps") == ["computer.workflow"] for event in desktop_search_events), "follow-up site search should plan desktop workflow")
    assert_true(not any(event.agent_state.get("tool") == "browser.search" for event in desktop_search_events), "follow-up site search should not start built-in browser search")
    desktop_search_approval = _approval_payload(desktop_search_events)
    assert_true(desktop_search_approval.get("tool") == "computer.workflow", "follow-up site search should require desktop workflow approval")
    pending_desktop_search = desktop_context_app.pending_steps[desktop_search_approval["approval_id"]]
    pending_desktop_args = pending_desktop_search.plan.steps[pending_desktop_search.index].arguments
    assert_true(pending_desktop_args.get("site") == "bilibili" and pending_desktop_args.get("query") == "猫猫视频", "pending desktop workflow should target Bilibili search")
    rewritten_short_search = desktop_context_app._rewrite_plan_for_desktop_context(plain_short_search_plan)
    assert_true(rewritten_short_search.intent == "desktop_workflow", "short follow-up search should rewrite to desktop workflow")
    assert_true(rewritten_short_search.steps[0].arguments.get("query") == "猫猫视频", "short follow-up search should preserve query")
    browser_tmpdir = tempfile.mkdtemp()
    try:
        browser_tmp = Path(browser_tmpdir)
        browser_result = BrowserTool(browser_tmp, "browser.search").run(ToolRequest("browser.search", {"query": "bilibili"}))
        assert_true(browser_result.ok, "browser stub queue should succeed")
        assert_true((browser_tmp / "data" / "agent_events" / "browser_requests.jsonl").is_file(), "browser stub should use executor request path")
    finally:
        shutil.rmtree(browser_tmpdir, ignore_errors=True)
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
    compression_result = ToolResult(
        ok=True,
        agent_state={
            "tool": "observe.screen",
            "summary": "用户喜欢蓝色主题",
            "screenshot_path": r"C:\secret\screen.png",
            "stdout": "raw " * 400,
            "target_candidate": {
                "label": "保存",
                "source": "ocr",
                "confidence": 0.91,
                "bbox": [10, 20, 30, 40],
                "preview": {"artifact": "data/agent_companion/vision/private.png"},
            },
        },
        display_card=DisplayCard("观察", "看到了保存按钮", "路径 data/agent_companion/vision/private.png", artifacts=["data/agent_companion/vision/private.png"]),
        voice_line=VoiceLine(r"我看到了 C:\secret\screen.png 和 sk-test-token 100,200"),
    )
    compressed = compress_tool_result(compression_result)
    planner_blob = json.dumps(compressed.agent_state, ensure_ascii=False)
    assert_true("screenshot_path" not in planner_blob and "stdout" not in planner_blob and "bbox" not in planner_blob, "JoiJuice planner state leaked raw path/log/coordinates")
    assert_true(compressed.agent_state["target_candidate"]["label"] == "保存", "JoiJuice should preserve semantic target label")
    assert_true("sk-" not in compressed.voice_line and "C:\\" not in compressed.voice_line and "100,200" not in compressed.voice_line, "JoiJuice voice channel leaked sensitive detail")
    assert_true(compressed.memory_candidate is None, "JoiJuice must not auto-create memory candidates from tool summaries")
    compression_app_dir = Path(tempfile.mkdtemp())
    try:
        (compression_app_dir / "agent_companion" / "config").mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / "agent_companion" / "config" / "default_character.yaml", compression_app_dir / "agent_companion" / "config" / "default_character.yaml")
        compression_app = AgentCompanionApp(compression_app_dir)
        compression_app._emit_result("compression-test", compression_result)
        compressed_events = compression_app.bus.drain()
        compressed_event = [event for event in compressed_events if event.task_id == "compression-test"][-1]
        assert_true("joi_juice" in compressed_event.agent_state, "Tool result events should attach JoiJuice channels")
        assert_true("screenshot_path" not in compressed_event.agent_state and "stdout" not in compressed_event.agent_state, "Tool result events should strip raw UI/debug paths and logs")
        assert_true(compressed_event.agent_state.get("result_channels", {}).get("planner") == "joi_juice.planner_state", "Tool result events should declare the compressed planner channel")
        assert_true("screenshot_path" not in json.dumps(compressed_event.agent_state["joi_juice"]["planner_state"], ensure_ascii=False), "JoiJuice planner channel should stay sanitized")
    finally:
        shutil.rmtree(compression_app_dir, ignore_errors=True)
    token_text, token_emotion = strip_emotion_token("<emo: thinking> 我想一下。")
    assert_true(token_text == "我想一下。" and token_emotion == "thinking", "emotion token should be stripped and normalized")
    happy_voice = safe_voice_line("<emo: happy> 做完了。")
    assert_true(happy_voice.text == "做完了。" and happy_voice.emotion == "happy" and happy_voice.sprite == "5", "emotion token should drive sprite sync")
    alert_voice = safe_voice_line("<emo: alert> 需要你确认。")
    assert_true(alert_voice.text == "需要你确认。" and alert_voice.emotion == "alert" and alert_voice.sprite == "4", "alert token should map to caution sprite")
    explicit_sprite_voice = safe_voice_line("<emo: happy> 我在处理。", sprite="3")
    assert_true(explicit_sprite_voice.emotion == "happy" and explicit_sprite_voice.sprite == "3", "explicit sprite should be preserved while token drives emotion")
    happy_emotion_voice = safe_voice_line("做完了。", emotion="happy")
    assert_true(happy_emotion_voice.sprite == "5", "non-neutral emotion should drive sprite even without inline token")
    assert_true(sprite_for_emotion("thinking") == "3" and sprite_for_emotion("unknown") == "1", "emotion sprite map should be stable")

    chat_emotion_result = CompanionChatTool(workspace).run(ToolRequest("companion.chat", {"text": "你现在开心吗"}))
    assert_true(chat_emotion_result.voice_line.emotion == "happy", "chat fallback should infer happy emotion from user text")
    assert_true(chat_emotion_result.voice_line.sprite != "1", "chat emotion should select a non-neutral sprite when available")
    assert_true(chat_emotion_result.agent_state["expression_sync"]["emotion"] == "happy", "chat should expose expression sync state")
    chat_memory_result = CompanionChatTool(workspace).run(
        ToolRequest("companion.chat", {"text": "你知道我喜欢什么吗", "memory_context": [{"text": "用户更喜欢轻量级原生控件", "kind": "preference", "source": "test"}]})
    )
    assert_true("轻量级原生控件" in chat_memory_result.display_card.summary, "chat should answer from approved memory context")
    assert_true(chat_memory_result.agent_state["memory_context"], "chat should expose the approved memory context it used")

    expression_sync_event = AgentCompanionApp(workspace).expression.express(
        AgentEvent(
            EventType.TOOL_COMPLETED,
            "task-expression-sync",
            DisplayCard("表达同步", "测试情绪 token", status="success"),
            VoiceLine("<emo: alert> 需要确认。", "neutral", "1"),
            {"tool": "browser.search"},
        )
    )
    expression_sync = expression_sync_event.agent_state.get("expression_sync", {})
    assert_true(expression_sync_event.voice_line.text == "需要确认。", "expression sync should strip emotion token before TTS")
    assert_true(expression_sync_event.voice_line.emotion == "alert" and expression_sync_event.voice_line.sprite == "4", "expression sync should update voice emotion and sprite")
    assert_true(expression_sync.get("voice_style") == "alert" and expression_sync.get("sprite") == "4", "expression sync state should be sent to frontend")

    memory_dir = Path(tempfile.mkdtemp())
    try:
        memory = MemoryStore(memory_dir / "memory.sqlite3")
        memory.remember("normal", "可长期保存")
        memory.remember("ephemeral", "临时画面摘要", ephemeral=True)
        memory.remember("sensitive", "敏感屏幕内容", sensitive=True)
        recent_memory = memory.recent(10)
        assert_true(len(recent_memory) == 1 and recent_memory[0]["text"] == "可长期保存", "ephemeral/sensitive memories should not be long-term by default")
        assert_true((memory_dir / "memory" / "joi_memory_vault.md").is_file(), "approved memories should rewrite a human-readable vault")
        safe_candidate = memory.propose("preference", "用户更喜欢原生 CSS 变量", source="chat")
        assert_true(safe_candidate["ok"] and memory.pending(10), "safe memory candidates should wait for user authorization")
        candidate_id = int(safe_candidate["candidate"]["id"])
        assert_true(not any(row["text"] == "用户更喜欢原生 CSS 变量" for row in memory.recent(10)), "pending memory candidates must not be saved automatically")
        saved_candidate = memory.save_candidate(candidate_id)
        assert_true(saved_candidate["ok"] and any(row["text"] == "用户更喜欢原生 CSS 变量" for row in memory.recent(10)), "saving a memory candidate should persist it")
        recalled_css = memory.recall("CSS 偏好", 5)
        assert_true(any("原生 CSS 变量" in row["text"] for row in recalled_css), "memory recall should find relevant approved memories")
        query_context = memory.context(10, query="我有什么 CSS 偏好")
        assert_true(any(row.get("source") == "semantic_recall" and "原生 CSS 变量" in row["text"] for row in query_context), "query memory context should prioritize semantic recall")
        vault_text = (memory_dir / "memory" / "joi_memory_vault.md").read_text(encoding="utf-8")
        assert_true("用户更喜欢原生 CSS 变量" in vault_text, "saved memories should appear in the local vault")
        browsed_vault = memory.browse_vault()
        assert_true(
            browsed_vault["path_label"] == "joi_memory_vault.md"
            and any("Saved Memories" == section["title"] and any("原生 CSS 变量" in line for line in section["lines"]) for section in browsed_vault["sections"]),
            "memory vault browsing should expose safe saved-memory sections",
        )
        (memory_dir / "memory" / "joi_memory_vault.md").write_text(
            vault_text + "\n## Manual Notes\n\n- 用户喜欢回答短一点\n- C:\\secret\\raw.log\n",
            encoding="utf-8",
        )
        context_rows = memory.context(10)
        assert_true(any("回答短一点" in row["text"] for row in context_rows), "manual vault notes should enter memory context")
        assert_true(not any("secret" in row["text"] or "raw.log" in row["text"] for row in context_rows), "unsafe manual vault notes should be ignored")
        memory.remember("normal", "用户偏好稳定控件")
        preserved_vault = (memory_dir / "memory" / "joi_memory_vault.md").read_text(encoding="utf-8")
        assert_true("用户喜欢回答短一点" in preserved_vault, "vault rewrites should preserve manual notes")
        rejected_candidate = memory.propose("preference", "用户的 key 是 sk-1234567890abcdef", source="chat")
        assert_true(not rejected_candidate["ok"] and not memory.pending(10), "unsafe memory candidates should fail closed")
        disabled_status = memory.set_enabled(False)
        assert_true(disabled_status["enabled"] is False, "memory store should support a hard disable switch")
        disabled_candidate = memory.propose("preference", "用户更喜欢极简界面", source="chat")
        assert_true(not disabled_candidate["ok"] and disabled_candidate["error"] == "memory_disabled", "disabled memory should not accept new candidates")
        assert_true(memory.remember("preference", "用户更喜欢极简界面") is None, "disabled memory should not save directly")
        assert_true(memory.set_enabled(True)["enabled"] is True, "memory store should re-enable cleanly")
        delete_id = int(saved_candidate["memory"]["id"])
        assert_true(memory.delete(delete_id)["ok"], "approved memories should be deletable")
        assert_true(all(row["id"] != delete_id for row in memory.recent(10)), "deleted memories should be removed from SQLite")
        assert_true(memory.propose("preference", "用户喜欢低噪音提醒", source="chat")["ok"], "clear test should have a pending candidate")
        clear_result = memory.clear()
        assert_true(clear_result["ok"] and not memory.recent(10) and not memory.pending(10), "memory clear should remove saved and pending rows")
        assert_true(not memory.recall("低噪音提醒", 5), "memory clear should remove recall index rows")
        cleared_vault = (memory_dir / "memory" / "joi_memory_vault.md").read_text(encoding="utf-8")
        assert_true("_No saved memories yet._" in cleared_vault and "用户喜欢回答短一点" in cleared_vault, "memory clear should preserve manual vault notes")
    finally:
        shutil.rmtree(memory_dir, ignore_errors=True)

    memory_app_dir = Path(tempfile.mkdtemp())
    try:
        (memory_app_dir / "agent_companion" / "config").mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / "agent_companion" / "config" / "default_character.yaml", memory_app_dir / "agent_companion" / "config" / "default_character.yaml")
        memory_app = AgentCompanionApp(memory_app_dir)
        memory_events = memory_app.handle_user_text("记住我更喜欢轻量级原生控件")
        memory_candidates = [event.agent_state.get("memory_candidate") for event in memory_events if event.agent_state.get("memory_candidate")]
        assert_true(memory_candidates and memory_app.memory.pending(10), "explicit remember requests should create pending memory candidates")
        assert_true(not any("轻量级原生控件" in row["text"] for row in memory_app.memory.recent(20)), "explicit remember requests should still require user save")
        saved_memory = memory_app.memory.save_candidate(int(memory_candidates[-1]["id"]))
        assert_true(saved_memory["ok"] and any("轻量级原生控件" in row["text"] for row in memory_app.memory.recent(20)), "approved explicit memory should persist")
        memory_bridge = JsonRpcBridge(memory_app_dir)
        recall_rpc = memory_bridge.memory_recall_command({"query": "轻量级偏好", "limit": 5})
        assert_true(recall_rpc["ok"] and any("轻量级原生控件" in row["text"] for row in recall_rpc["memories"]), "memory recall RPC should return approved semantic matches")
        vault_rpc = memory_bridge.memory_browse_vault_command()
        assert_true(vault_rpc["ok"] and vault_rpc["vault"]["sections"], "memory vault RPC should expose browsable vault sections")
        memory_status_events = memory_app.handle_user_text("你记得什么")
        assert_true(any(event.agent_state.get("tool") == "memory.status" and event.agent_state.get("memory", {}).get("enabled") is True for event in memory_status_events), "memory status command should expose saved memory state")
        memory_off_events = memory_app.handle_user_text("关闭记忆")
        assert_true(any(event.agent_state.get("memory", {}).get("enabled") is False for event in memory_off_events), "memory disable command should update memory state")
        disabled_memory_events = memory_app.handle_user_text("记住我喜欢透明 UI")
        assert_true(not any(event.agent_state.get("memory_candidate") for event in disabled_memory_events), "disabled memory should suppress explicit memory candidates")
        memory_on_events = memory_app.handle_user_text("开启记忆")
        assert_true(any(event.agent_state.get("memory", {}).get("enabled") is True for event in memory_on_events), "memory enable command should update memory state")
        personalized_events = memory_app.handle_user_text("你知道我喜欢什么吗")
        personalized_chat = [event for event in personalized_events if event.agent_state.get("tool") == "companion.chat"]
        assert_true(personalized_chat and "轻量级原生控件" in personalized_chat[-1].display_card.summary, "approved memories should personalize companion chat")
    finally:
        shutil.rmtree(memory_app_dir, ignore_errors=True)

    screen_tool = ScreenObserveTool(workspace, FakeVisionObserver(workspace), ocr=UnavailableOcrExtractor())
    screen_result = screen_tool.run(ToolRequest("observe.screen", {"query": "陪我看当前画面", "target": "fullscreen"}))
    assert_true(screen_result.ok, "screen observation should succeed with fake observer")
    assert_true("computer_observation" in screen_result.agent_state, "screen observation should use computer observation chain")
    assert_true(screen_result.agent_state["observation"]["target"] == "fullscreen", "screen target not preserved")
    assert_true(screen_result.display_card.artifacts == ["data/agent_companion/vision/sample.png"], "screenshot artifact missing")
    assert_true("sample.png" not in screen_result.voice_line.text, "voice should not read screenshot path")
    assert_true(screen_result.agent_state["observation"]["ocr"]["status"] == "unavailable", "OCR should have safe unavailable fallback")
    assert_true("OCR：" in screen_result.display_card.body, "screen card should include OCR status")

    video_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/video-frame-1.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/video-frame-2.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/video-frame-3.png", title="Bilibili Video"),
        ],
    )
    sequence_summarizer = SequenceSummarizer()
    video_ocr = OcrResult("success", "识别到字幕和弹幕。", [OcrTextBlock("这一条是小猫发的", (12, 80, 160, 24), 0.91), OcrTextBlock("666", (900, 30, 80, 22), 0.88)])
    video_tool = ScreenObserveTool(workspace, computer_backend=video_backend, summarizer=sequence_summarizer, ocr=FakeOcrExtractor(video_ocr))
    video_result = video_tool.run(ToolRequest("observe.screen", {"query": "陪我看这个视频", "sample_interval_ms": 0}))
    assert_true(video_result.ok, "video watch observation should succeed")
    assert_true(video_backend.observe_calls == 3, "video watch should capture multiple temporal frames")
    assert_true(video_result.agent_state["temporal_observation"] is True, "video watch should mark temporal observation")
    assert_true(len(video_result.agent_state["watch_frames"]) == 3, "video watch should expose sampled watch frames")
    assert_true(len(video_result.display_card.artifacts) == 3, "video watch should expose multiple frame artifacts")
    assert_true(sequence_summarizer.sequence_calls and sequence_summarizer.sequence_calls[0][0] == 3, "video watch should use sequence vision summarizer")
    assert_true(any("实时转写" in row for row in sequence_summarizer.sequence_calls[0][2]), "video sequence summary should receive transcript text")
    assert_true("连续画面显示一只猫" in video_result.agent_state["sequence_summary"], "video watch should preserve sequence summary")
    assert_true("连续采样" in video_result.display_card.body and "连续帧文本线索" in video_result.display_card.body, "video card should explain temporal sampling")
    assert_true(video_result.agent_state["transcript"]["status"] == "success", "video watch should expose OCR subtitle transcript state")
    assert_true("这一条是小猫发的" in video_result.agent_state["transcript"]["segments"][0]["text"], "video transcript should include subtitle-like OCR text")
    assert_true("实时转写" in video_result.display_card.body, "video card should show transcript snippets")

    audio_transcriber = FakeAudioTranscriber()
    audio_transcript_tool = ScreenObserveTool(
        workspace,
        computer_backend=FakeComputerBackend(workspace),
        summarizer=SequenceSummarizer(),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        audio_transcriber=audio_transcriber,
    )
    audio_transcript_result = audio_transcript_tool.run(ToolRequest("observe.screen", {"query": "用系统音频听一下这个视频", "sample_count": 2, "sample_interval_ms": 0, "transcribe": True, "transcript_source": "system_audio"}))
    assert_true(audio_transcript_result.agent_state["transcript"]["source"] == "system_audio", "system audio transcript source should be exposed")
    assert_true(audio_transcriber.calls, "system audio transcript provider should be invoked when requested")

    auto_fallback_audio = FailingAudioTranscriber("asr_unconfigured")
    auto_fallback_tool = ScreenObserveTool(
        workspace,
        computer_backend=FakeComputerBackend(workspace),
        summarizer=SequenceSummarizer(),
        ocr=FakeOcrExtractor(video_ocr),
        audio_transcriber=auto_fallback_audio,
    )
    auto_fallback_result = auto_fallback_tool.run(ToolRequest("observe.screen", {"query": "陪我看这个视频", "sample_count": 2, "sample_interval_ms": 0, "transcribe": True, "transcript_source": "auto"}))
    assert_true(auto_fallback_audio.calls, "auto transcript source should try system audio first")
    assert_true(auto_fallback_result.agent_state["transcript"]["source"] == "ocr_subtitle", "auto transcript should fall back to OCR subtitles when audio fails")
    assert_true(auto_fallback_result.agent_state["transcript"]["error"] == "asr_unconfigured", "auto transcript fallback should preserve audio failure reason")

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
    target_evidence = target_result.agent_state["target_candidate"].get("evidence", {})
    assert_true(target_evidence.get("source") == "ocr" and target_evidence.get("confidence_band") in {"medium", "high"}, "OCR target should expose sanitized source and confidence evidence")
    assert_true(target_evidence.get("actionability") == "ocr_text" and target_evidence.get("capture_trust") == "trusted", "OCR target evidence should expose actionability and capture trust")
    assert_true("confirmation_reason" in target_evidence and "bbox" not in str(target_evidence) and "data/" not in str(target_evidence), "target evidence should stay display-safe")
    assert_true(len(target_result.agent_state["target_candidates"]) >= 1, "semantic target approval should preserve candidate previews")
    assert_true("登录" in target_result.display_card.summary, "semantic target card should name the friendly target")
    forbidden_target_voice = ["登录", "860", "30", "data/", ".png", "{", "vision.resolve_target", "computer.click", "source", "bbox", "task-", "approval-", ".log"]
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
    ambiguous_evidence = ambiguous_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(ambiguous_evidence.get("ambiguity_reason") == "close_score" and ambiguous_evidence.get("confirmation_reason"), "close-score target evidence should explain why selection is needed")
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
    accessibility_evidence = accessibility_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(accessibility_evidence.get("source") == "accessibility" and accessibility_evidence.get("actionability") == "actionable", "UIA button evidence should mark actionable source")
    accessibility_click_args = accessibility_target.agent_state["approval_request"]["arguments"]
    assert_true(accessibility_click_args["x"] == 990 and accessibility_click_args["y"] == 245, "accessibility bounds should click by absolute screen center")
    assert_true(accessibility_target.agent_state["target_candidate"]["preview"]["bbox"] == [860, 30, 60, 30], "accessibility candidate should have screenshot-relative preview bbox")

    partial_capture_state = _fake_computer_observation(
        workspace,
        rel="data/agent_companion/vision/target-uia-partial.png",
        width=400,
        height=225,
        capture_rect=CaptureRect(100, 80, 200, 150),
    ).to_agent_state()
    clipped_uia_args = click_arguments_from_state(
        {"source": "accessibility", "bbox": [180, 50, 80, 30], "screen_bbox": [280, 130, 80, 30]},
        partial_capture_state,
    )
    assert_true(clipped_uia_args is None, "clipped UIA screen_bbox center outside capture_rect must not create click args")
    trusted_uia_args = click_arguments_from_state(
        {"source": "accessibility", "bbox": [50, 40, 80, 30], "screen_bbox": [150, 120, 80, 30]},
        partial_capture_state,
    )
    assert_true(trusted_uia_args == {"x": 190, "y": 135}, "trusted UIA screen_bbox center should create expected click args")
    mixed_scale_state = _fake_computer_observation(
        workspace,
        rel="data/agent_companion/vision/target-mixed-monitor.png",
        width=400,
        height=225,
        capture_rect=CaptureRect(-960, 240, 267, 150, scale_x=1.5, scale_y=1.5),
    ).to_agent_state()
    mixed_ocr_args = click_arguments_from_state({"source": "ocr", "bbox": [210, 90, 90, 30]}, mixed_scale_state)
    assert_true(mixed_ocr_args == {"x": -790, "y": 310}, "mixed-scale OCR bbox should convert through trusted negative-origin capture rect")
    mixed_uia_inside_args = click_arguments_from_state(
        {"source": "accessibility", "bbox": [90, 60, 120, 45], "screen_bbox": [-900, 280, 80, 30]},
        mixed_scale_state,
    )
    assert_true(mixed_uia_inside_args == {"x": -860, "y": 295}, "negative-origin UIA screen_bbox inside capture rect should create click args")
    mixed_uia_outside_args = click_arguments_from_state(
        {"source": "accessibility", "bbox": [360, 90, 40, 30], "screen_bbox": [-720, 300, 80, 30]},
        mixed_scale_state,
    )
    assert_true(mixed_uia_outside_args is None, "negative-origin UIA screen_bbox center outside capture rect must fail closed")

    clipped_uia_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[AccessibleElement("目标越界", "ButtonControl", (280, 130, 80, 30), enabled=True, clickable=True, confidence=0.97)],
    )
    clipped_uia_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-uia-clipped.png",
                    width=400,
                    height=225,
                    capture_rect=CaptureRect(100, 80, 200, 150),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=FakeAccessibilityObserver(clipped_uia_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点目标越界"}))
    assert_true(not clipped_uia_target.requires_approval, "partially clipped UIA center outside capture rect must not create approval")
    assert_true(clipped_uia_target.agent_state["needs_clarification"], "unsafe clipped UIA target should ask for clarification")
    assert_true("approval_request" not in clipped_uia_target.agent_state, "unsafe clipped UIA target must not synthesize click arguments")
    assert_true(clipped_uia_target.agent_state["target_candidate"]["preview"]["bbox"] == [180, 50, 80, 30], "unsafe clipped UIA target may keep clamped preview bbox")

    trusted_partial_uia_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[AccessibleElement("目标可信", "ButtonControl", (150, 120, 80, 30), enabled=True, clickable=True, confidence=0.97)],
    )
    trusted_partial_uia_target = SemanticTargetTool(
        workspace,
        computer_backend=FakeComputerBackend(
            workspace,
            observations=[
                _fake_computer_observation(
                    workspace,
                    rel="data/agent_companion/vision/target-uia-trusted.png",
                    width=400,
                    height=225,
                    capture_rect=CaptureRect(100, 80, 200, 150),
                )
            ],
        ),
        ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
        accessibility=FakeAccessibilityObserver(trusted_partial_uia_snapshot),
    ).run(ToolRequest("vision.resolve_target", {"query": "点目标可信"}))
    trusted_partial_click_args = trusted_partial_uia_target.agent_state["approval_request"]["arguments"]
    assert_true(trusted_partial_uia_target.requires_approval, "UIA center inside partial capture rect should keep approval path")
    assert_true(trusted_partial_click_args == {"x": 190, "y": 135}, "trusted partial UIA approval should preserve absolute screen center")

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
    disabled_evidence = disabled_button_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(disabled_evidence.get("actionability") == "disabled" and disabled_evidence.get("source") == "accessibility", "disabled UIA evidence should expose disabled actionability")

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
    static_evidence = static_text_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(static_evidence.get("actionability") == "static_text", "static UIA evidence should explain static text gate")

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
    conflict_evidence = conflict_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(conflict_evidence.get("ambiguity_reason") == "close_score" and conflict_evidence.get("capture_trust") == "trusted", "OCR/UIA conflict evidence should keep close-score and capture trust state")

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
    visual_evidence = visual_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(visual_evidence.get("actionability") == "visual_only" and visual_evidence.get("confidence_band") == "low", "visual-only target evidence should explain visual gate and confidence band")
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

    clipped_uia_selection_app = AgentCompanionApp(workspace)
    clipped_uia_selection_snapshot = AccessibilitySnapshot(
        "success",
        title="Joi Test Window",
        window_handle=1234,
        elements=[
            AccessibleElement("登录", "ButtonControl", (280, 130, 80, 30), enabled=True, clickable=True, confidence=0.97),
            AccessibleElement("登录", "ButtonControl", (140, 130, 72, 30), enabled=True, clickable=True, confidence=0.95),
        ],
    )
    clipped_uia_selection_app.tools.register(
        SemanticTargetTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(
                        workspace,
                        rel="data/agent_companion/vision/selection-uia-clipped.png",
                        width=400,
                        height=225,
                        capture_rect=CaptureRect(100, 80, 200, 150),
                    )
                ],
            ),
            ocr=FakeOcrExtractor(OcrResult("success", "empty", [])),
            accessibility=FakeAccessibilityObserver(clipped_uia_selection_snapshot),
        )
    )
    clipped_uia_selection_events = clipped_uia_selection_app.handle_user_text("点登录按钮")
    clipped_uia_selection_id = _selection_id(clipped_uia_selection_events)
    assert_true(clipped_uia_selection_id.startswith("selection-"), "clipped UIA ambiguity should create pending selection context")
    clipped_uia_selected_events = clipped_uia_selection_app.select_semantic_target(clipped_uia_selection_id, 1)
    assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in clipped_uia_selected_events), "selected clipped UIA candidate outside capture rect must not create click approval")
    assert_true(any(event.agent_state.get("coordinate_untrusted") for event in clipped_uia_selected_events), "selected clipped UIA candidate should report untrusted coordinates")
    clipped_selection_card = [event for event in clipped_uia_selected_events if event.agent_state.get("coordinate_untrusted")][-1]
    clipped_evidence = clipped_selection_card.agent_state.get("target_candidate", {}).get("evidence", {})
    assert_true(clipped_evidence.get("capture_trust") == "untrusted", "untrusted selected candidate evidence should survive continuation")
    assert_true(all(not any(fragment in event.voice_line.text for fragment in forbidden_uia_voice + ["280", "130", "selection-uia-clipped.png"]) for event in clipped_uia_selected_events), "clipped UIA selection voice leaked technical details")

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
    artifact_path = workspace / "data" / "agent_companion" / "vision" / "artifact-read-test.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/l2YqJwAAAABJRU5ErkJggg=="))
    artifact_result = bridge.read_artifact_command("data/agent_companion/vision/artifact-read-test.png")
    assert_true(artifact_result.get("ok") is True and str(artifact_result.get("data_url", "")).startswith("data:image/png;base64,"), "artifact.read should return a data URL for workspace images")
    assert_true(bridge.read_artifact_command("../secret.png").get("error") == "artifact_not_found", "artifact.read must not read outside the workspace")
    ready_payload = bridge._ready_payload()
    ready_blob = json.dumps(ready_payload, ensure_ascii=False, default=str)
    assert_true("workspace_label" in ready_payload and "workspace" not in ready_payload, "ready payload should expose a label instead of an absolute workspace path")
    assert_true(str(workspace) not in ready_blob, "ready payload must not expose local absolute workspace paths")
    memory_status = bridge.app.memory.status()
    assert_true("vault_label" in memory_status and "vault_path" not in memory_status, "memory status should expose a vault label instead of an absolute path")

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
    missing_rect_evidence = missing_rect_target.agent_state["target_candidate"].get("evidence", {})
    assert_true(missing_rect_evidence.get("capture_trust") == "untrusted", "missing capture rect evidence should fail closed")

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
    workflow_decision = policy.classify(ToolRequest("computer.workflow", {"workflow": "open_app", "app": "Codex"}))
    assert_true(workflow_decision.requires_approval, "computer.workflow should require approval")
    public_payload = policy.public_payload(ToolRequest("computer.click", {"x": 100, "y": 200}))
    assert_true("100" not in str(public_payload), "policy preview should not expose raw click coordinates")

    audit_tmpdir = tempfile.mkdtemp()
    try:
        audit_path = Path(audit_tmpdir) / "audit.jsonl"
        audit_store = AuditStore(audit_path)
        audit_store.record_event(
            AgentEvent(
                EventType.APPROVAL_REQUIRED,
                "task-abcdef123456",
                DisplayCard("需要确认", r"打开 C:\secret\screen.png sk-test-private", status="approval", artifacts=[r"data/agent_companion/vision/private.png"]),
                VoiceLine("需要确认。"),
                {
                    "tool": "computer.click",
                    "risk": "medium",
                    "approval": {
                        "approval_id": "approval-abcdef123456",
                        "task_id": "task-abcdef123456",
                        "step_index": 1,
                        "tool": "computer.click",
                    },
                    "policy": {
                        "tool": "computer.click",
                        "reason": "medium 风险动作需要确认。",
                        "arguments_preview": {"target": "指定屏幕位置", "raw": r"C:\secret\screen.png"},
                    },
                    "skill_id": "joi.computer_use",
                    "skill_category": "computer_use",
                    "skill_permission_level": "medium",
                    "skill_audit": "computer_use_audit",
                    COMPUTER_AUDIT_STATE_KEY: [
                        {
                            "event_type": "approval_pending",
                            "sanitized_summary": "Approval is required before this Computer Use action can run.",
                            "risk_level": "medium",
                            "approval_id": "approval-abcdef123456",
                            "approval_status": "pending",
                            "tool_name": "computer.click",
                            "skill_id": "joi.computer_use",
                            "action_name": "click",
                            "sanitized_arguments": {"target": "screen_position"},
                            "before_artifacts": [{"ref": r"data/agent_companion/vision/private.png"}],
                        }
                    ],
                },
            )
        )
        audit_payload = audit_store.recent(10)
        assert_true(audit_payload["version"] == AUDIT_SCHEMA_VERSION and audit_payload["safe_for_display"], "audit store should expose a safe display payload")
        audit_record = audit_payload["records"][-1]
        assert_true(audit_record["outcome"] == "approval_pending" and audit_record["approval"]["status"] == "pending", "audit store should persist approval lifecycle records")
        assert_true(audit_record["skill_id"] == "joi.computer_use" and audit_record["tool"] == "computer.click", "audit store should preserve stable skill/tool ids")
        assert_true(audit_record["computer_audit"][0]["artifact_counts"]["before"] == 1, "audit store should keep artifact counts without refs")
        audit_text = audit_path.read_text(encoding="utf-8")
        forbidden_audit_fragments = ["C:\\", "sk-", "approval-", "task-abcdef", "data/", ".png", "secret"]
        assert_true(all(fragment not in audit_text for fragment in forbidden_audit_fragments), "persistent audit log leaked private ids, paths, artifacts, or secrets")
    finally:
        shutil.rmtree(audit_tmpdir, ignore_errors=True)

    background_tmpdir = tempfile.mkdtemp()
    try:
        background_path = Path(background_tmpdir) / "background_context.json"
        background_store = BackgroundContextStore(background_path)
        background_config = background_store.configure(enabled=True, scope_type="project", label=r"C:\secret\joi")
        background_state = background_config["background"]
        assert_true(background_config["ok"] and background_state["enabled"] and background_state["active"], "background context should enable only after an approved scope")
        assert_true(background_state["active_scope"]["type"] == "project" and background_state["active_scope"]["label"] == "approved_project", "background scope labels should not expose local project paths")
        background_record = background_store.record_summary("这段视频正在讨论猫猫实验和观众弹幕。", source="watch_loop", visual_status="ok", transcript_source="system_audio")
        assert_true(background_record["ok"] and background_record["background"]["recent_count"] == 1, "background context should store summaries for approved scopes")
        cleared_background = background_store.clear_context()["background"]
        assert_true(cleared_background["recent_count"] == 0 and cleared_background["scope_count"] == 1, "background clear should remove summaries without deleting approved scopes")
        disabled_background = background_store.configure(enabled=False)["background"]
        blocked_background = background_store.record_summary("不会记录", source="watch_loop")
        assert_true(disabled_background["enabled"] is False and not blocked_background["ok"] and blocked_background["error"] == "background_disabled", "disabled background context should reject new summaries")
        background_text = background_path.read_text(encoding="utf-8")
        forbidden_background_fragments = ["C:\\", "secret", ".png", "http", "sk-", "approval-", "task-"]
        assert_true(all(fragment not in background_text for fragment in forbidden_background_fragments), "background context store leaked private paths, ids, or secrets")
    finally:
        shutil.rmtree(background_tmpdir, ignore_errors=True)

    doctor_tmpdir = tempfile.mkdtemp()
    try:
        doctor_root = Path(doctor_tmpdir)
        for relative in (
            "README.md",
            "config.example.yaml",
            "requirements.txt",
            "agent_companion/shell/package.json",
            "agent_companion/shell/src-tauri/tauri.conf.json",
            "tools/start_joi.ps1",
        ):
            target = doctor_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
        venv_bin = doctor_root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        venv_bin.mkdir(parents=True, exist_ok=True)
        (venv_bin / ("python.exe" if os.name == "nt" else "python")).write_text("", encoding="utf-8")
        (doctor_root / "agent_companion/shell/node_modules").mkdir(parents=True, exist_ok=True)
        release_shell = doctor_root / "agent_companion/shell/src-tauri/target/release/joi-shell.exe"
        release_shell.parent.mkdir(parents=True, exist_ok=True)
        release_shell.write_text("", encoding="utf-8")
        (doctor_root / "config.yaml").write_text(
            """
llm:
  use_mock: false
  base_url: https://api.example.test/v1
  model: joi-fast
  api_key: sk-private-do-not-print
  vision_enabled: true
  vision_base_url: https://vision.example.test/v1
  vision_model: joi-vision
  vision_api_key: ${JOI_VISION_API_KEY}
asr:
  enabled: true
  provider: openai_compatible
  base_url: https://asr.example.test/v1
  model: whisper-test
  api_key: ${JOI_ASR_API_KEY}
tts:
  enabled: true
  provider: gpt-sovits
  server_url: http://127.0.0.1:9880/
ocr:
  tesseract_cmd: C:\\secret\\tesseract.exe
characters:
  - name: Joi
""",
            encoding="utf-8",
        )
        doctor_report = build_doctor_report(
            doctor_root,
            import_probe=lambda _name: True,
            which_probe=lambda name: "tool" if name in {"npm.cmd", "cargo.exe", "tesseract.exe"} else None,
            port_probe=lambda _port: False,
            env={"JOI_ASR_API_KEY": "sk-asr-private", "JOI_VISION_API_KEY": "sk-vision-private"},
        )
        doctor_text = json.dumps(doctor_report, ensure_ascii=False)
        assert_true(doctor_report["status"] == "ok" and doctor_exit_code(doctor_report) == 0, "doctor should pass a complete local setup")
        assert_true("sk-" not in doctor_text and "C:\\" not in doctor_text and "secret" not in doctor_text, "doctor report leaked secrets or local paths")
        broken_report = build_doctor_report(
            doctor_root / "missing",
            import_probe=lambda _name: False,
            which_probe=lambda _name: None,
            port_probe=lambda _port: True,
            env={},
        )
        assert_true(broken_report["status"] == "fail" and doctor_exit_code(broken_report) == 1 and broken_report["next_actions"], "doctor should fail closed with actionable first-run next steps")
    finally:
        shutil.rmtree(doctor_tmpdir, ignore_errors=True)

    setup_tmpdir = tempfile.mkdtemp()
    try:
        setup_root = Path(setup_tmpdir)
        (setup_root / "config.example.yaml").write_text(
            """
llm:
  use_mock: true
  api_key: ${JOI_LLM_API_KEY}
characters:
  - name: Joi
    color: "#ff4b91"
    setting: setup fixture
""",
            encoding="utf-8",
        )
        dry_setup = build_windows_setup_plan(setup_root)
        assert_true(dry_setup["status"] == "warn" and windows_setup_exit_code(dry_setup) == 0 and not (setup_root / "config.yaml").exists(), "setup wizard dry-run should not write config.yaml")
        apply_setup = build_windows_setup_plan(setup_root, apply=True)
        setup_text = json.dumps(apply_setup, ensure_ascii=False)
        assert_true(apply_setup["status"] == "ok" and windows_setup_exit_code(apply_setup) == 0 and apply_setup["created"] == ["config.yaml"], "setup wizard apply should create config.yaml from example")
        config_before = (setup_root / "config.yaml").read_text(encoding="utf-8")
        second_apply = build_windows_setup_plan(setup_root, apply=True)
        assert_true(second_apply["status"] == "ok" and not second_apply["created"] and (setup_root / "config.yaml").read_text(encoding="utf-8") == config_before, "setup wizard must not overwrite an existing config.yaml")
        forbidden_setup_fragments = ["sk-", "https://", "http://", "C:\\", "/Users/", "api_key", "base_url", "server_url", str(setup_root)]
        assert_true(all(fragment not in setup_text for fragment in forbidden_setup_fragments), "setup wizard leaked secrets, endpoints, or local paths")
    finally:
        shutil.rmtree(setup_tmpdir, ignore_errors=True)

    provider_tmpdir = tempfile.mkdtemp()
    try:
        provider_root = Path(provider_tmpdir)
        (provider_root / "config.yaml").write_text(
            """
llm:
  use_mock: false
  provider: openai_compatible
  base_url: https://private.example/v1
  model: deepseek-v4
  api_key: sk-provider-secret
  vision_enabled: true
  vision_base_url: https://vision.example/v1
  vision_model: vision-model
  vision_api_key: sk-vision-secret
  expression_enabled: true
  expression_model: v4-flash
  expression_api_key: sk-expression-secret
asr:
  enabled: true
  provider: openai_compatible
  base_url: https://asr.example/v1
  model: asr-model
  api_key: sk-asr-secret
tts:
  enabled: true
  provider: gpt-sovits
  server_url: http://127.0.0.1:9880
ocr:
  tesseract_cmd: C:\\secret\\tesseract.exe
characters:
  - name: Joi
    color: "#ff4b91"
    setting: test
""",
            encoding="utf-8",
        )
        provider_report = build_provider_preflight_report(provider_root)
        provider_text = json.dumps(provider_report, ensure_ascii=False)
        assert_true(provider_report["status"] in {"ok", "warn"} and provider_preflight_exit_code(provider_report) == 0, "provider preflight should pass or warn on optional provider gaps")
        assert_true(any(row["name"] == "fast" and row["state"] == "ready" for row in provider_report["providers"]), "provider preflight should report text model readiness")
        assert_true(any(row["name"] == "asr" and row["configured"] for row in provider_report["providers"]), "provider preflight should report ASR readiness")
        assert_true(any(row["name"] == "tts" and row["configured"] for row in provider_report["providers"]), "provider preflight should report TTS readiness")
        forbidden_provider_fragments = ["sk-", "https://", "http://", "C:\\", "/Users/", "secret", "api_key", "base_url", "server_url"]
        assert_true(all(fragment not in provider_text for fragment in forbidden_provider_fragments), "provider preflight leaked secrets, endpoints, or local paths")
        missing_provider_report = build_provider_preflight_report(provider_root / "missing")
        assert_true(missing_provider_report["status"] == "warn" and provider_preflight_exit_code(missing_provider_report) == 0 and missing_provider_report["next_actions"], "provider preflight should warn with setup actions when config is missing")
    finally:
        shutil.rmtree(provider_tmpdir, ignore_errors=True)

    demo_tmpdir = tempfile.mkdtemp()
    old_codex_bin = os.environ.get("AGENT_COMPANION_CODEX_BIN")
    old_ok_ww_runner = os.environ.get("OK_WW_RUNNER")
    try:
        demo_root = Path(demo_tmpdir)
        (demo_root / "config.yaml").write_text(
            """
llm:
  use_mock: true
characters:
  - name: Joi
    color: "#ff4b91"
    setting: demo fixture
""",
            encoding="utf-8",
        )
        fake_codex = demo_root / ("codex.cmd" if os.name == "nt" else "codex")
        fake_codex.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(fake_codex, 0o755)
        fake_ok_ww = demo_root / "run_ok_ww.ps1"
        fake_ok_ww.write_text("# dry-run fixture\n", encoding="utf-8")
        os.environ["AGENT_COMPANION_CODEX_BIN"] = str(fake_codex)
        os.environ["OK_WW_RUNNER"] = str(fake_ok_ww)
        demo_report = build_mvp_demo_check_report(demo_root)
        demo_text = json.dumps(demo_report, ensure_ascii=False)
        assert_true(demo_report["status"] in {"ok", "warn"} and mvp_demo_check_exit_code(demo_report) == 0, "MVP demo check should pass or warn when local optional providers are missing")
        assert_true({row["id"] for row in demo_report["demos"]} == {"watch_together", "coding_task", "game_skill"}, "MVP demo check should cover watch, coding, and game demos")
        assert_true(all(row["prompts"] for row in demo_report["demos"]), "MVP demo check should expose safe demo prompts")
        forbidden_demo_fragments = ["sk-", "https://", "http://", "C:\\", "/Users/", "secret", "api_key", "base_url", "server_url", str(demo_root)]
        assert_true(all(fragment not in demo_text for fragment in forbidden_demo_fragments), "MVP demo check leaked secrets, endpoints, or local paths")
        missing_demo_report = build_mvp_demo_check_report(demo_root / "missing")
        assert_true(missing_demo_report["status"] in {"warn", "fail"} and missing_demo_report["next_actions"], "MVP demo check should explain missing local demo dependencies")
    finally:
        if old_codex_bin is None:
            os.environ.pop("AGENT_COMPANION_CODEX_BIN", None)
        else:
            os.environ["AGENT_COMPANION_CODEX_BIN"] = old_codex_bin
        if old_ok_ww_runner is None:
            os.environ.pop("OK_WW_RUNNER", None)
        else:
            os.environ["OK_WW_RUNNER"] = old_ok_ww_runner
        shutil.rmtree(demo_tmpdir, ignore_errors=True)

    packaging_tmpdir = tempfile.mkdtemp()
    try:
        packaging_root = Path(packaging_tmpdir)
        shell_dir = packaging_root / "agent_companion" / "shell"
        tauri_dir = shell_dir / "src-tauri"
        (tauri_dir / "capabilities").mkdir(parents=True, exist_ok=True)
        (packaging_root / "tools").mkdir(parents=True, exist_ok=True)
        (shell_dir / "package-lock.json").write_text("{}", encoding="utf-8")
        (shell_dir / "package.json").write_text(
            json.dumps(
                {
                    "name": "joi-shell",
                    "private": True,
                    "version": "0.1.0",
                    "scripts": {"build": "vue-tsc --noEmit && vite build", "tauri": "tauri"},
                }
            ),
            encoding="utf-8",
        )
        (tauri_dir / "Cargo.toml").write_text(
            """
[package]
name = "joi-shell"
version = "0.1.0"
edition = "2021"
""",
            encoding="utf-8",
        )
        (tauri_dir / "tauri.conf.json").write_text(
            json.dumps(
                {
                    "productName": "Joi",
                    "version": "0.1.0",
                    "identifier": "local.joi",
                    "build": {"beforeBuildCommand": "npm run build", "frontendDist": "../dist"},
                    "app": {"windows": [{"label": "main", "title": "Joi", "width": 1120, "height": 760, "transparent": True, "decorations": False}]},
                }
            ),
            encoding="utf-8",
        )
        (tauri_dir / "capabilities" / "default.json").write_text(
            json.dumps(
                {
                    "identifier": "default",
                    "windows": ["main"],
                    "permissions": [
                        "core:window:allow-close",
                        "core:window:allow-minimize",
                        "core:window:allow-start-dragging",
                        "core:window:allow-set-always-on-top",
                        "core:window:allow-set-skip-taskbar",
                    ],
                }
            ),
            encoding="utf-8",
        )
        (packaging_root / "start_joi.bat").write_text("powershell -File tools\\start_joi.ps1 %*", encoding="utf-8")
        (packaging_root / "tools" / "start_joi.ps1").write_text("-Doctor\n-Setup\njoi_doctor.py\nwindows_setup_wizard.py\njoi_core.err.log\njoi_core.out.log", encoding="utf-8")
        (packaging_root / "tools" / "joi_doctor.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "mvp_demo_check.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "package_windows_release.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "provider_preflight.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "windows_handoff_report.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "windows_release_check.py").write_text("", encoding="utf-8")
        (packaging_root / "tools" / "windows_setup_wizard.py").write_text("", encoding="utf-8")
        packaging_report = build_packaging_smoke_report(packaging_root)
        assert_true(packaging_report["status"] == "ok" and packaging_smoke_exit_code(packaging_report) == 0, "packaging smoke should pass valid release metadata")
        assert_true(any(item["name"] == "mvp_demo_check" and item["status"] == "ok" for item in packaging_report["items"]), "packaging smoke should require MVP demo check tooling")
        assert_true(any(item["name"] == "provider_preflight" and item["status"] == "ok" for item in packaging_report["items"]), "packaging smoke should require provider preflight tooling")
        assert_true(any(item["name"] == "windows_handoff_report" and item["status"] == "ok" for item in packaging_report["items"]), "packaging smoke should require the Windows handoff report")
        assert_true(any(item["name"] == "windows_release_check" and item["status"] == "ok" for item in packaging_report["items"]), "packaging smoke should require the release readiness aggregator")
        assert_true(any(item["name"] == "windows_setup_wizard" and item["status"] == "ok" for item in packaging_report["items"]), "packaging smoke should require the setup wizard")
        privacy_smoke = {item["name"]: item for item in packaging_report["items"]}.get("release_privacy_policy", {})
        assert_true(privacy_smoke.get("status") == "ok", "packaging smoke should validate the release privacy policy")
        (tauri_dir / "tauri.conf.json").write_text(
            json.dumps({"productName": "Joi", "version": "0.2.0", "identifier": "", "build": {}, "app": {"windows": [{"label": "other"}]}}),
            encoding="utf-8",
        )
        broken_packaging_report = build_packaging_smoke_report(packaging_root)
        assert_true(broken_packaging_report["status"] == "fail" and packaging_smoke_exit_code(broken_packaging_report) == 1 and broken_packaging_report["next_actions"], "packaging smoke should fail closed on release metadata drift")
    finally:
        shutil.rmtree(packaging_tmpdir, ignore_errors=True)

    release_tmpdir = tempfile.mkdtemp()
    try:
        release_root = Path(release_tmpdir)
        for relative in (
            "README.md",
            "config.example.yaml",
            "secrets.example.yaml",
            "requirements.txt",
            "requirements-accessibility.txt",
            "requirements-audio.txt",
            "requirements-ocr.txt",
            "start_joi.bat",
            "agent_companion/README.md",
            "agent_companion/shell/index.html",
            "agent_companion/shell/package.json",
            "agent_companion/shell/package-lock.json",
            "agent_companion/shell/tsconfig.json",
            "agent_companion/shell/vite.config.ts",
            "agent_companion/shell/src-tauri/build.rs",
            "agent_companion/shell/src-tauri/Cargo.lock",
            "agent_companion/shell/src-tauri/Cargo.toml",
            "agent_companion/shell/src-tauri/tauri.conf.json",
            "run_agent_companion_tests.py",
            "tools/joi_doctor.py",
            "tools/mvp_demo_check.py",
            "tools/package_windows_release.py",
            "tools/packaging_smoke.py",
            "tools/provider_preflight.py",
            "tools/smoke_ws_bridge.py",
            "tools/start_joi.ps1",
            "tools/windows_handoff_report.py",
            "tools/windows_release_check.py",
            "tools/windows_setup_wizard.py",
        ):
            target = release_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith("package.json"):
                target.write_text(
                    json.dumps(
                        {
                            "name": "joi-shell",
                            "private": True,
                            "version": "0.1.0",
                            "scripts": {"build": "vue-tsc --noEmit && vite build", "tauri": "tauri"},
                        }
                    ),
                    encoding="utf-8",
                )
            elif relative.endswith("Cargo.toml"):
                target.write_text(
                    """
[package]
name = "joi-shell"
version = "0.1.0"
edition = "2021"
""",
                    encoding="utf-8",
                )
            elif relative.endswith("tauri.conf.json"):
                target.write_text(
                    json.dumps(
                        {
                            "productName": "Joi",
                            "version": "0.1.0",
                            "identifier": "local.joi",
                            "build": {"beforeBuildCommand": "npm run build", "frontendDist": "../dist"},
                            "app": {"windows": [{"label": "main", "title": "Joi", "width": 1120, "height": 760, "transparent": True, "decorations": False}]},
                        }
                    ),
                    encoding="utf-8",
                )
            elif relative == "start_joi.bat":
                target.write_text("powershell -File tools\\start_joi.ps1 %*", encoding="utf-8")
            elif relative == "tools/start_joi.ps1":
                target.write_text("-Doctor\n-Setup\njoi_doctor.py\nwindows_setup_wizard.py\njoi_core.err.log\njoi_core.out.log", encoding="utf-8")
            else:
                target.write_text("release input", encoding="utf-8")
        for relative in (
            "agent_companion/config/default_character.yaml",
            "agent_companion/core/app.py",
            "agent_companion/docs/architecture.md",
            "docs/WINDOWS_FIRST_RUN.md",
            "agent_companion/shell/src/App.vue",
            "agent_companion/shell/src-tauri/capabilities/default.json",
            "agent_companion/shell/src-tauri/icons/icon.png",
            "agent_companion/shell/src-tauri/src/main.rs",
        ):
            target = release_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith("capabilities/default.json"):
                target.write_text(
                    json.dumps(
                        {
                            "identifier": "default",
                            "windows": ["main"],
                            "permissions": [
                                "core:window:allow-close",
                                "core:window:allow-minimize",
                                "core:window:allow-start-dragging",
                                "core:window:allow-set-always-on-top",
                                "core:window:allow-set-skip-taskbar",
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                target.write_text("release input", encoding="utf-8")
        release_exe = release_root / "agent_companion/shell/src-tauri/target/release/joi-shell.exe"
        release_exe.parent.mkdir(parents=True, exist_ok=True)
        release_exe.write_bytes(b"fake exe")
        for forbidden in (
            "config.yaml",
            "secrets.yaml",
            ".env",
            "config.local.yaml",
            "data/local.db",
            "data/local_visual_eval/private_manifest.json",
            "logs/joi_core.err.log",
            "agent_companion/shell/node_modules/private.txt",
            "agent_companion/shell/dist/index.html",
            "agent_companion/shell/src-tauri/target/debug/joi-shell.exe",
            "agent_companion/shell/src-tauri/target/release/private.pdb",
            "agent_companion/core/__pycache__/app.pyc",
        ):
            target = release_root / forbidden
            target.parent.mkdir(parents=True, exist_ok=True)
            if forbidden == "config.yaml":
                target.write_text(
                    """
llm:
  use_mock: true
characters:
  - name: Joi
    color: "#ff4b91"
    setting: release fixture
""",
                    encoding="utf-8",
                )
            else:
                target.write_text("private", encoding="utf-8")
        privacy_report = build_release_privacy_report()
        assert_true(privacy_report["status"] == "ok" and not privacy_report["unprotected_samples"] and privacy_report["protected_sample_count"] >= 10, "Release privacy policy should protect local-only config, data, logs, dependency, and build paths")
        release_report = build_windows_release_package(release_root, output_dir=release_root / "out")
        assert_true(release_report["status"] == "ok" and package_exit_code(release_report) == 0 and release_report["sha256"], "Windows release packager should create a portable zip")
        assert_true(release_report["privacy_policy"]["status"] == "ok", "Windows release report should include a passing privacy policy check")
        readiness_report = build_windows_release_check_report(release_root, include_doctor=False)
        readiness_phases = {phase["name"]: phase for phase in readiness_report["phases"]}
        assert_true(readiness_report["status"] in {"ok", "warn"} and windows_release_check_exit_code(readiness_report) == 0, "Windows release readiness check should pass or warn when only advisory provider gaps remain")
        assert_true(readiness_phases["provider_preflight"]["status"] in {"ok", "warn"} and readiness_phases["mvp_demo_check"]["status"] in {"ok", "warn"} and readiness_phases["release_privacy_policy"]["status"] == "ok" and readiness_phases["release_package_dry_run"]["status"] == "ok", "Windows release readiness check should aggregate provider, demo, privacy, and release dry-run status")
        handoff_report = build_windows_handoff_report(release_root, include_doctor=False, branch="win-desktop-fixes", commit="abc1234")
        handoff_text = json.dumps(handoff_report, ensure_ascii=False)
        assert_true(handoff_report["status"] in {"ok", "warn"} and windows_handoff_exit_code(handoff_report) == 0 and handoff_report["branch"] == "win-desktop-fixes" and handoff_report["commit"] == "abc1234", "Windows handoff report should expose safe branch, commit, and readiness state")
        assert_true(any(phase["name"] == "release_package_dry_run" for phase in handoff_report["phases"]) and "start_joi.bat -Setup" in handoff_report["commands"], "Windows handoff report should include release phases and first-run commands")
        assert_true(all(fragment not in handoff_text for fragment in [str(release_root), "config.yaml", "secrets.yaml", ".env", "logs/", "data/"]), "Windows handoff report leaked local paths or private release inputs")
        zip_path = release_root / release_report["zip"]
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
        names_text = "\n".join(names)
        assert_true(any(name.endswith("agent_companion/shell/src-tauri/target/release/joi-shell.exe") for name in names), "Release zip should include the built shell exe")
        assert_true(any(name.endswith("tools/windows_handoff_report.py") for name in names), "Release zip should include the Windows handoff report")
        assert_true("RELEASE_MANIFEST.json" in names_text, "Release zip should include a safe manifest")
        assert_true(not any(fragment in names_text for fragment in ["config.yaml", "secrets.yaml", ".env", "node_modules", "logs/", "data/", "__pycache__", "private.pdb", "target/debug"]), "Release zip leaked local config, runtime data, dependency folders, or debug artifacts")
        release_exe.unlink()
        missing_exe_report = build_windows_release_package(release_root, output_dir=release_root / "out3", require_exe=True)
        assert_true(missing_exe_report["status"] == "fail" and "release_exe_missing" in missing_exe_report["errors"], "Release packager should require the release shell by default")
        missing_exe_readiness = build_windows_release_check_report(release_root, include_doctor=False)
        assert_true(missing_exe_readiness["status"] == "fail" and windows_release_check_exit_code(missing_exe_readiness) == 1, "Release readiness check should fail when the release shell is missing")
        ci_readiness = build_windows_release_check_report(release_root, include_doctor=False, allow_missing_exe=True)
        assert_true(ci_readiness["status"] in {"ok", "warn"} and windows_release_check_exit_code(ci_readiness) == 0 and not ci_readiness["release_ready"] and ci_readiness["next_actions"], "CI readiness mode should allow metadata checks before the release shell exists")
        ci_handoff = build_windows_handoff_report(release_root, include_doctor=False, allow_missing_exe=True, branch="ci", commit="def5678")
        assert_true(ci_handoff["status"] == "warn" and windows_handoff_exit_code(ci_handoff) == 0 and ci_handoff["next_actions"], "Windows handoff should warn, not fail, when CI allows a missing release shell")
    finally:
        shutil.rmtree(release_tmpdir, ignore_errors=True)

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

    workflow_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/workflow-before.png", title="Before"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/workflow-after.png", title="After"),
        ],
    )
    workflow_tool = DesktopWorkflowTool(workspace, workflow_backend, post_action_settle_ms=0, sleep_fn=lambda seconds: None)
    workflow_result = workflow_tool.run(ToolRequest("computer.workflow", {"workflow": "open_app", "app": "Codex"}))
    assert_true(workflow_result.ok, "computer.workflow open_app should succeed with fake backend")
    assert_true([action.action_type for action in workflow_backend.actions] == ["hotkey", "type_text", "hotkey"], "open_app workflow should use start search, typing, enter")
    assert_true(workflow_backend.actions[1].text == "Codex", "open_app workflow should type app name")
    assert_true(workflow_result.agent_state["computer_use"]["action"]["type"] == "workflow", "workflow action state should be recorded")
    assert_true(workflow_result.agent_state["post_action_verification"]["status"] == "changed", "workflow should verify after observation")
    workflow_audit = computer_action_audit_event("task-workflow", workflow_result)
    assert_true(workflow_audit is not None and workflow_audit.sanitized_arguments.get("workflow") == "open_app", "workflow audit should be sanitized")
    bili_workflow_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/bili-before.png", title="Before"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/bili-after.png", title="Bilibili Search"),
        ],
    )
    bili_workflow_tool = DesktopWorkflowTool(workspace, bili_workflow_backend, post_action_settle_ms=0, sleep_fn=lambda seconds: None)
    bili_workflow_result = bili_workflow_tool.run(ToolRequest("computer.workflow", {"workflow": "open_web_search", "browser": "edge", "site": "bilibili", "query": "猫猫视频"}))
    typed_values = [action.text or "" for action in bili_workflow_backend.actions if action.action_type == "type_text"]
    assert_true(bili_workflow_result.ok, "Bilibili desktop workflow search should succeed with fake backend")
    assert_true(any(value == "Microsoft Edge" for value in typed_values), "Bilibili workflow should launch Edge")
    assert_true(any(value.startswith("https://search.bilibili.com/all?keyword=") for value in typed_values), "Bilibili workflow should navigate to site search URL")
    assert_true(not any(value == "https://www.bilibili.com" for value in typed_values), "Bilibili search workflow should not stop on homepage when a query exists")

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
    assert_true(ModelRouter.stable_routes() == ("fast", "reasoning", "vision", "code", "summarize", "voice_style"), "ModelRouter should expose stable P7 route labels")
    text_ep = router.resolve("text")
    assert_true(text_ep.base_url == "https://api.base.com/v1", "text should use base_url")
    assert_true(text_ep.model == "gpt-4", "text should use base model")
    assert_true(text_ep.api_key == "sk-base", "text should use base api_key")
    assert_true(text_ep.route == "fast" and not text_ep.fallback_reason, "legacy text route should map to stable fast route")
    reasoning_ep = router.resolve("reasoning")
    assert_true(reasoning_ep.route == "reasoning" and reasoning_ep.fallback_reason == "reasoning_fallback_base", "reasoning route should fall back to base model when not overridden")

    # ModelRouter: vision falls back to text when not configured
    vision_ep = router.resolve("vision")
    assert_true(vision_ep.base_url == "https://api.base.com/v1" and vision_ep.fallback_reason == "vision_fallback_base", "unconfigured vision should fall back to base")

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
    voice_style_ep = ModelRouter(expr_llm).resolve("voice_style")
    assert_true(voice_style_ep.model == "gpt-4.1-mini", "voice_style route should use expression model")

    # ModelRouter: expression falls back when not enabled
    expr_ep2 = ModelRouter(base_llm).resolve("expression")
    assert_true(expr_ep2.model == "gpt-4", "unconfigured expression should fall back to base model")
    voice_style_ep2 = ModelRouter(base_llm).resolve("voice_style")
    assert_true(voice_style_ep2.model == "gpt-4", "unconfigured voice_style should fall back to base model")
    route_llm = LlmConfig(
        provider="openai_compatible",
        use_mock=False,
        base_url="https://api.base.com/v1",
        model="gpt-4",
        api_key="sk-base",
        routes={
            "code": ModelRouteConfig(base_url="https://api.code.com/v1", model="gpt-code", api_key="sk-code"),
            "summarize": ModelRouteConfig(model="gpt-summary"),
        },
    )
    code_ep = ModelRouter(route_llm).resolve("code")
    summarize_ep = ModelRouter(route_llm).resolve("summarize")
    assert_true(code_ep.route == "code" and code_ep.model == "gpt-code" and code_ep.api_key == "sk-code", "code route should use explicit override")
    assert_true(summarize_ep.route == "summarize" and summarize_ep.model == "gpt-summary" and summarize_ep.api_key == "sk-base", "summarize route should inherit base API key")
    assert_true("api_key" not in str(code_ep.to_agent_state()) and "sk-code" not in str(code_ep.to_agent_state()), "model usage payload should not expose endpoint secrets")

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
            "  routes:\n"
            "    reasoning:\n"
            "      model: gpt-router-reasoning\n"
            "      api_key: sk-router-reasoning\n"
            "    code:\n"
            "      model: gpt-router-code\n"
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
        assert_true(ModelRouter(tmp_config.llm).resolve("reasoning").model == "gpt-router-reasoning", "config routes should parse reasoning override")
        assert_true(ModelRouter(tmp_config.llm).resolve("code").api_key == "sk-test", "route overrides should inherit base credentials when omitted")
        asr_provider, asr_state = build_asr_provider(tmp)
        assert_true(isinstance(asr_provider, OpenAICompatibleAsrProvider), "configured ASR should use OpenAI-compatible provider")
        assert_true(asr_state.configured and asr_state.max_bytes == 4096, "ASR runtime state should expose limits")
        assert_true(asr_state.timeout_seconds == 9, "ASR runtime state should expose timeout")
        assert_true(isinstance(tmp_app._build_ocr_extractor(), PytesseractOcrExtractor), "OCR extractor should build from config")
        assert_true(tmp_app._build_ocr_extractor().timeout_seconds == 4, "OCR extractor should use configured timeout")
        tmp_click_tool = tmp_app.tools._tools.get("computer.click")
        assert_true(tmp_click_tool is not None and tmp_click_tool.ocr is observe_tool.ocr, "computer tool should reuse observe.screen OCR extractor")
        assert_true(tmp_click_tool.post_action_settle_ms == 0, "computer tool should use configured settle delay")
        tmp_workflow_tool = tmp_app.tools._tools.get("computer.workflow")
        assert_true(tmp_workflow_tool is not None and tmp_workflow_tool.ocr is observe_tool.ocr, "desktop workflow should reuse observe.screen OCR extractor")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    mutation_tmpdir = tempfile.mkdtemp()
    try:
        mutation_tmp = Path(mutation_tmpdir)
        config_path = mutation_tmp / "config.yaml"
        secrets_path = mutation_tmp / "secrets.yaml"
        config_path.write_text(
            """
llm:
  provider: openai_compatible
  use_mock: true
  base_url: https://api.private.example/v1
  model: gpt-public-text
  api_key: ${JOI_LLM_API_KEY}
  vision_enabled: false
  vision_model: gpt-public-vision
  expression_enabled: false
  expression_model: gpt-public-expression
  temperature: 0.7
tts:
  enabled: false
  provider: gpt-sovits
  server_url: http://127.0.0.1:9880/
  gpt_sovits_work_path: /Users/private/GPT-SoVITS
  text_lang: zh
  prompt_lang: zh
  speed_factor: 1.2
  fallback_to_system: false
asr:
  enabled: false
  provider: openai_compatible
  base_url: ${JOI_ASR_BASE_URL}
  model: whisper-1
  api_key: ${JOI_ASR_API_KEY}
  language: zh
  max_seconds: 30
  max_bytes: 12582912
  timeout_seconds: 30
ocr:
  timeout_seconds: 5
computer_use:
  post_action_settle_ms: 200
custom_section:
  unknown_flag: keep-me
""",
            encoding="utf-8",
        )
        secrets_path.write_text(
            """
llm:
  api_key: sk-secret-text
asr:
  api_key: sk-secret-asr
""",
            encoding="utf-8",
        )
        secrets_before = secrets_path.read_text(encoding="utf-8")
        safe_updates = {
            "asr": {
                "enabled": True,
                "provider": "openai_compatible",
                "base_url": "https://asr.private.example/v1",
                "model": "whisper-safe",
                "language": "en",
                "max_seconds": 45,
                "max_bytes": 4096,
                "timeout_seconds": 12,
            },
            "tts": {
                "enabled": True,
                "provider": "gpt-sovits",
                "volume": 0.7,
                "text_lang": "zh",
                "prompt_lang": "zh",
                "speed_factor": 1.1,
                "fallback_to_system": True,
            },
            "ocr": {"timeout_seconds": 8},
            "computer_use": {"post_action_settle_ms": 325},
            "llm": {
                "provider": "openai_compatible",
                "model": "gpt-public-next",
                "vision_enabled": True,
                "vision_model": "gpt-public-vision-next",
                "expression_enabled": True,
                "expression_model": "gpt-public-expression-next",
                "temperature": 0.3,
                "use_mock": False,
            },
            "skills": {
                "joi.computer_use": {"enabled": False},
                "joi.local_files": {"enabled": False},
            },
        }
        preview_mutation = preview_runtime_config_update(mutation_tmp, safe_updates)
        assert_true(preview_mutation.ok and preview_mutation.changed and preview_mutation.dry_run, "safe config mutation preview should report changes")
        _assert_config_mutation_payload_safe(preview_mutation.to_agent_state(), "config mutation preview leaked sensitive detail")
        assert_true("https://asr.private.example" not in preview_mutation.summary, "config mutation summary leaked endpoint")

        applied_mutation = update_runtime_config(mutation_tmp, safe_updates, dry_run=False)
        assert_true(applied_mutation.ok and applied_mutation.changed and not applied_mutation.dry_run, "safe config mutation should write allowlisted fields")
        _assert_config_mutation_payload_safe(applied_mutation.to_agent_state(), "config mutation result leaked sensitive detail")
        mutated_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert_true(mutated_config["asr"]["enabled"] is True and mutated_config["asr"]["max_seconds"] == 45, "ASR allowlist fields should update")
        assert_true(mutated_config["asr"]["base_url"] == "https://asr.private.example/v1", "ASR base_url should be writable but not exposed in summaries")
        assert_true(mutated_config["tts"]["fallback_to_system"] is True and mutated_config["tts"]["volume"] == 0.7, "TTS allowlist fields should update")
        assert_true(mutated_config["ocr"]["timeout_seconds"] == 8 and mutated_config["computer_use"]["post_action_settle_ms"] == 325, "runtime numeric allowlist fields should update")
        assert_true(mutated_config["llm"]["model"] == "gpt-public-next" and mutated_config["llm"]["use_mock"] is False, "LLM allowlist fields should update")
        assert_true(mutated_config["skills"]["joi.computer_use"]["enabled"] is False and mutated_config["skills"]["joi.local_files"]["enabled"] is False, "native skill enabled flags should update through safe runtime config")
        assert_true(mutated_config["custom_section"]["unknown_flag"] == "keep-me", "unknown config fields must be preserved")
        assert_true(mutated_config["llm"]["api_key"] == "${JOI_LLM_API_KEY}" and mutated_config["asr"]["api_key"] == "${JOI_ASR_API_KEY}", "env placeholders must be preserved")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "secrets.yaml must not be rewritten")
        assert_true("sk-secret" not in config_path.read_text(encoding="utf-8"), "secrets must not be copied into config.yaml")

        runtime_update_request = ToolRequest("runtime.update_config", {"updates": {"ocr": {"timeout_seconds": 9}}, "dry_run": True})
        runtime_policy = PolicyGate().classify(runtime_update_request)
        assert_true(runtime_policy.requires_approval and runtime_policy.risk == RiskLevel.MEDIUM, "runtime config updates should require approval by default")
        assert_true("https://" not in str(PolicyGate.public_payload(runtime_update_request)) and "api_key" not in str(PolicyGate.public_payload(runtime_update_request)), "runtime config policy preview should be sanitized")
        runtime_tool_result = RuntimeConfigUpdateTool(mutation_tmp).run(runtime_update_request)
        assert_true(runtime_tool_result.ok and runtime_tool_result.agent_state["runtime_config_update"]["dry_run"], "runtime config tool should support safe dry-run")
        _assert_config_mutation_payload_safe(runtime_tool_result.agent_state["runtime_config_update"], "runtime config tool state leaked sensitive detail")
        _assert_config_mutation_payload_safe(runtime_tool_result.display_card.body, "runtime config tool card leaked sensitive detail")
        assert_true("https://" not in runtime_tool_result.voice_line.text and "api_key" not in runtime_tool_result.voice_line.text and "{" not in runtime_tool_result.voice_line.text, "runtime config tool voice leaked raw config")

        before_forbidden = config_path.read_text(encoding="utf-8")
        forbidden_mutation = update_runtime_config(
            mutation_tmp,
            {
                "llm": {"api_key": "sk-new-secret", "model": "/Users/private/joi.gguf"},
                "tts": {"server_url": "http://127.0.0.1:9880/private"},
                "characters": {"refer_audio_path": "/Users/private/voice.wav"},
            },
            dry_run=False,
        )
        assert_true(not forbidden_mutation.ok, "secret, endpoint server_url, and path-like config writes must be forbidden")
        assert_true(config_path.read_text(encoding="utf-8") == before_forbidden, "forbidden config mutation must not touch config.yaml")
        _assert_config_mutation_payload_safe(forbidden_mutation.to_agent_state(), "forbidden config mutation error leaked sensitive detail")

        before_invalid = config_path.read_text(encoding="utf-8")
        invalid_mutation = update_runtime_config(mutation_tmp, {"ocr": {"timeout_seconds": 0}, "tts": {"volume": "loud"}}, dry_run=False)
        assert_true(not invalid_mutation.ok, "invalid type/range config mutation should fail")
        assert_true(config_path.read_text(encoding="utf-8") == before_invalid, "invalid config mutation must not touch config.yaml")
        invalid_skill_mutation = update_runtime_config(mutation_tmp, {"skills": {"joi.unknown": {"enabled": False}}}, dry_run=False)
        assert_true(not invalid_skill_mutation.ok and invalid_skill_mutation.errors[0]["code"] == "unknown_skill", "unknown skill toggles should fail closed")
        protected_skill_mutation = update_runtime_config(mutation_tmp, {"skills": {"joi.runtime_config": {"enabled": False}}}, dry_run=False)
        assert_true(not protected_skill_mutation.ok and protected_skill_mutation.errors[0]["code"] == "protected_skill", "runtime config skill should not be disabled through runtime config")
    finally:
        shutil.rmtree(mutation_tmpdir, ignore_errors=True)

    p4_20_tmpdir = tempfile.mkdtemp()
    try:
        p4_20_tmp = Path(p4_20_tmpdir)
        character_dir = p4_20_tmp / "agent_companion" / "config"
        character_dir.mkdir(parents=True, exist_ok=True)
        (character_dir / "default_character.yaml").write_text(
            """
id: test-joi
name: Joi
asset_policy: test
style:
  tone: concise
  speech: safe
  boundaries: []
persona: "Test companion."
voice:
  default_lang: zh
  start: "开始。"
  progress: "处理中。"
  done: "完成。"
  failed: "失败。"
""",
            encoding="utf-8",
        )
        config_path = p4_20_tmp / "config.yaml"
        secrets_path = p4_20_tmp / "secrets.yaml"
        config_path.write_text(
            """
llm:
  provider: openai_compatible
  use_mock: true
  base_url: https://api.private.example/v1
  model: gpt-public-text
  api_key: ${JOI_LLM_API_KEY}
  temperature: 0.7
tts:
  enabled: false
  provider: gpt-sovits
  server_url: http://127.0.0.1:9880/
  volume: 0.85
  speed_factor: 1.2
  fallback_to_system: false
asr:
  enabled: false
  provider: openai_compatible
  base_url: ${JOI_ASR_BASE_URL}
  model: whisper-1
  api_key: ${JOI_ASR_API_KEY}
  language: zh
  max_seconds: 30
  max_bytes: 12582912
  timeout_seconds: 30
ocr:
  timeout_seconds: 5
computer_use:
  post_action_settle_ms: 200
characters:
  - name: Joi
    color: "#d76f8f"
    setting: "local test companion"
""",
            encoding="utf-8",
        )
        secrets_path.write_text(
            """
llm:
  api_key: sk-runtime-text-secret
asr:
  api_key: sk-runtime-asr-secret
""",
            encoding="utf-8",
        )
        config_before = config_path.read_text(encoding="utf-8")
        secrets_before = secrets_path.read_text(encoding="utf-8")
        bridge = JsonRpcBridge(p4_20_tmp, asr_provider=MockAsrProvider("你好"), asr_state=AsrRuntimeState(False, False, "none"))
        safe_panel_updates = {
            "asr": {"enabled": True, "max_seconds": 42, "max_bytes": 4096, "timeout_seconds": 11},
            "tts": {"enabled": True, "volume": 0.75, "speed_factor": 1.1, "fallback_to_system": True},
            "ocr": {"timeout_seconds": 7},
            "computer_use": {"post_action_settle_ms": 350},
            "llm": {"temperature": 0.4, "use_mock": False},
            "skills": {"joi.mcp": {"enabled": False}},
        }
        preview_payload = bridge.preview_runtime_config_update_command(safe_panel_updates)
        assert_true(preview_payload["ok"] and preview_payload["preview"]["dry_run"], "runtime config preview RPC should be read-only")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "runtime config preview must not write config.yaml")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "runtime config preview must not touch secrets.yaml")
        _assert_config_mutation_payload_safe(preview_payload["preview"], "runtime config preview RPC leaked sensitive detail")

        invalid_rpc_payload = _runtime_apply_rpc_result(bridge, {"ocr": {"timeout_seconds": 0}, "tts": {"server_url": "http://127.0.0.1:9880/private"}}, "p4-21-invalid")
        assert_true(not invalid_rpc_payload["ok"] and not invalid_rpc_payload["submitted"], "invalid runtime config WebSocket apply should synchronously fail")
        assert_true(not _approval_payload_from_dicts(invalid_rpc_payload.get("events", [])), "invalid runtime config WebSocket apply must not create approval")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "invalid runtime config WebSocket apply must not write config.yaml")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "invalid runtime config WebSocket apply must preserve secrets.yaml")
        _assert_config_mutation_payload_safe(invalid_rpc_payload.get("preview"), "invalid runtime config WebSocket apply leaked sensitive preview detail")

        noop_updates = {
            "asr": {"enabled": False, "max_seconds": 30, "max_bytes": 12582912, "timeout_seconds": 30},
            "tts": {"enabled": False, "volume": 0.85, "speed_factor": 1.2, "fallback_to_system": False},
            "ocr": {"timeout_seconds": 5},
            "computer_use": {"post_action_settle_ms": 200},
            "llm": {"temperature": 0.7, "use_mock": True},
        }
        noop_rpc_payload = _runtime_apply_rpc_result(bridge, noop_updates, "p4-21-noop")
        assert_true(noop_rpc_payload["ok"] and not noop_rpc_payload["submitted"], "no-op runtime config WebSocket apply should not submit approval")
        assert_true(noop_rpc_payload["preview"]["ok"] and not noop_rpc_payload["preview"]["changed"], "no-op runtime config WebSocket apply should return unchanged preview")
        assert_true(not _approval_payload_from_dicts(noop_rpc_payload.get("events", [])), "no-op runtime config WebSocket apply must not create approval")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "no-op runtime config WebSocket apply must not write config.yaml")
        _assert_config_mutation_payload_safe(noop_rpc_payload.get("preview"), "no-op runtime config WebSocket apply leaked sensitive preview detail")

        valid_rpc_payload = _runtime_apply_rpc_result(bridge, safe_panel_updates, "p4-21-valid")
        assert_true(valid_rpc_payload["ok"] and valid_rpc_payload["submitted"], "valid changed runtime config WebSocket apply should submit approval")
        assert_true(valid_rpc_payload["preview"]["ok"] and valid_rpc_payload["preview"]["changed"], "valid runtime config WebSocket apply should return changed preview")
        valid_rpc_approval = _approval_payload_from_dicts(valid_rpc_payload.get("events", []))
        assert_true(bool(valid_rpc_approval), "valid runtime config WebSocket apply should produce an approval event")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "valid runtime config WebSocket apply must not write before approval")
        _assert_config_mutation_payload_safe(valid_rpc_payload, "valid runtime config WebSocket apply leaked sensitive detail")
        denied_ws_result = bridge.resolve_approval_command(str(valid_rpc_approval["approval_id"]), False)
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "denied WebSocket runtime config approval must not write config.yaml")
        _assert_config_mutation_payload_safe(denied_ws_result["events"], "denied WebSocket runtime config approval leaked sensitive detail")

        denied_payload = bridge.apply_runtime_config_update_command(safe_panel_updates)
        assert_true(denied_payload["ok"] and denied_payload["submitted"], "runtime config apply should submit an approval-gated request")
        assert_true(denied_payload["preview"]["ok"] and denied_payload["preview"]["changed"], "runtime config direct apply should include changed preview")
        denied_events = denied_payload["events"]
        denied_approval = _approval_payload_from_dicts(denied_events)
        assert_true(bool(denied_approval), "runtime config apply should create a Joi approval card")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "runtime config apply must not write before approval")
        _assert_config_mutation_payload_safe([event.get("agent_state", {}) for event in denied_events], "runtime config approval state leaked sensitive detail")
        denied_result = bridge.resolve_approval_command(str(denied_approval["approval_id"]), False)
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "denied runtime config approval must not write config.yaml")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "denied runtime config approval must preserve secrets.yaml byte-for-byte")
        _assert_config_mutation_payload_safe(denied_result["events"], "denied runtime config events leaked sensitive detail")

        invalid_payload = bridge.apply_runtime_config_update_command({"ocr": {"timeout_seconds": 0}, "tts": {"volume": "loud"}})
        assert_true(not invalid_payload["ok"] and not invalid_payload["submitted"], "invalid runtime config updates should fail before approval")
        assert_true(config_path.read_text(encoding="utf-8") == config_before, "invalid runtime config update must not write config.yaml")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "invalid runtime config update must preserve secrets.yaml byte-for-byte")
        _assert_config_mutation_payload_safe(invalid_payload["preview"], "invalid runtime config preview leaked sensitive detail")

        apply_payload = bridge.apply_runtime_config_update_command(safe_panel_updates)
        approval = _approval_payload_from_dicts(apply_payload["events"])
        assert_true(bool(approval), "second runtime config apply should create a fresh approval")
        applied_payload = bridge.resolve_approval_command(str(approval["approval_id"]), True)
        assert_true(any(event["type"] == EventType.TOOL_COMPLETED.value for event in applied_payload["events"]), "approved runtime config update should complete the tool")
        mutated_runtime_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert_true(mutated_runtime_config["asr"]["enabled"] is True and mutated_runtime_config["asr"]["max_seconds"] == 42, "approved runtime config update should write ASR safe fields")
        assert_true(mutated_runtime_config["tts"]["volume"] == 0.75 and mutated_runtime_config["tts"]["fallback_to_system"] is True, "approved runtime config update should write TTS safe fields")
        assert_true(mutated_runtime_config["ocr"]["timeout_seconds"] == 7 and mutated_runtime_config["computer_use"]["post_action_settle_ms"] == 350, "approved runtime config update should write local runtime safe fields")
        assert_true(mutated_runtime_config["llm"]["temperature"] == 0.4 and mutated_runtime_config["llm"]["use_mock"] is False, "approved runtime config update should write LLM safe fields")
        assert_true(mutated_runtime_config["skills"]["joi.mcp"]["enabled"] is False, "approved runtime config update should write native skill switches")
        assert_true(secrets_path.read_text(encoding="utf-8") == secrets_before, "approved runtime config update must preserve secrets.yaml byte-for-byte")
        assert_true("sk-runtime" not in config_path.read_text(encoding="utf-8"), "runtime config apply must not copy secrets into config.yaml")
        ready_payload = applied_payload.get("ready") or {}
        assert_true(ready_payload.get("runtime_settings", {}).get("asr", {}).get("max_seconds") == 42, "runtime ready payload should refresh ASR safe settings after apply")
        assert_true(ready_payload.get("runtime_settings", {}).get("tts", {}).get("volume") == 0.75, "runtime ready payload should refresh TTS safe settings after apply")
        assert_true(ready_payload.get("runtime_settings", {}).get("llm", {}).get("temperature") == 0.4, "runtime ready payload should refresh LLM safe settings after apply")
        assert_true(ready_payload.get("runtime_settings", {}).get("skills", {}).get("joi.mcp", {}).get("enabled") is False, "runtime ready payload should refresh native skill switches after apply")
        ready_skill_rows = {row["id"]: row for row in ready_payload.get("skills", {}).get("skills", [])}
        assert_true(ready_skill_rows["joi.mcp"]["enabled"] is False and ready_skill_rows["joi.mcp"]["local_capability"] == "off", "runtime ready skill manifest should mirror disabled native skills")
        assert_true(not bridge.app.policy.classify(ToolRequest("mcp.list_tools", {})).allowed, "runtime reload should apply disabled native skill policy")
        assert_true(ready_payload.get("runtime", {}).get("safe_for_display") is True, "runtime status payload should remain marked safe for display")
        _assert_config_mutation_payload_safe(ready_payload.get("runtime"), "runtime status payload leaked sensitive detail after config apply")
        _assert_config_mutation_payload_safe(ready_payload.get("runtime_settings"), "runtime settings payload leaked sensitive detail after config apply")
        _assert_config_mutation_payload_safe(applied_payload["events"], "approved runtime config events leaked sensitive detail")
        _assert_config_mutation_payload_safe(bridge.app.memory.recent(10), "runtime config memory leaked sensitive detail")
    finally:
        shutil.rmtree(p4_20_tmpdir, ignore_errors=True)

    chat_app = AgentCompanionApp(workspace)
    chat_events = chat_app.handle_user_text("你好")
    assert_true(any(event.display_card.title == "对话" for event in chat_events), "chat should produce a dialogue card")
    chat_tool_event = [event for event in chat_events if event.display_card.title == "对话"][-1]
    assert_true(chat_tool_event.agent_state.get("skill_id") == "joi.companion.chat", "Tool result events should carry native skill ids")
    assert_true(not any(event.type == EventType.PLAN_CREATED for event in chat_events), "chat should not show plan events")
    assert_true(not any(event.type == EventType.TASK_COMPLETED for event in chat_events), "chat should not show task completion")

    empty_watch_events = AgentCompanionApp(workspace).handle_user_text("你看到了什么")
    assert_true(any(event.agent_state.get("tool") == "watch.recall" for event in empty_watch_events), "empty watch follow-up should use recall")
    assert_true(not any(event.type == EventType.TASK_FAILED for event in empty_watch_events), "empty watch follow-up should answer naturally, not fail")
    assert_true(not any(event.type == EventType.TASK_COMPLETED for event in empty_watch_events), "watch follow-up should not add generic task completion voice")

    browser_watch_app = AgentCompanionApp(workspace)
    browser_plan = build_plan("搜索 bilibili")
    browser_watch_app._record_watch_context(
        browser_plan,
        browser_plan.steps[0],
        ToolResult(
            ok=True,
            agent_state={
                "tool": "browser.search",
                "data": {
                    "title": "Bilibili",
                    "elements": [{"text": "搜索"}, {"text": "首页"}],
                    "screenshot": "data/cache/browser/browser-smoke.png",
                },
            },
            display_card=DisplayCard("网页搜索", "已观察到页面《Bilibili》。", status="success", artifacts=["data/cache/browser/browser-smoke.png"]),
            voice_line=safe_voice_line("我看到页面内容了。"),
            risk=RiskLevel.LOW,
        ),
    )
    browser_watch_events = browser_watch_app.handle_user_text("你看到了什么")
    assert_true(any(event.agent_state.get("tool") == "watch.recall" for event in browser_watch_events), "browser watch follow-up should use recall")
    assert_true(any("Bilibili" in event.display_card.body for event in browser_watch_events if event.agent_state.get("tool") == "watch.recall"), "browser observation should be available to watch recall")

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

    video_watch_app = AgentCompanionApp(workspace)
    video_watch_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/watch-video-1.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/watch-video-2.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/watch-video-3.png", title="Bilibili Video"),
        ],
    )
    video_watch_app.tools.register(
        ScreenObserveTool(
            workspace,
            computer_backend=video_watch_backend,
            summarizer=SequenceSummarizer(),
            ocr=FakeOcrExtractor(video_ocr),
        )
    )
    video_watch_app.handle_user_text("陪我看这个视频")
    video_frames = video_watch_app.watch_session.recent(5)
    assert_true(len(video_frames) >= 3 and video_frames[0].sequence_size == 3, "video watch session should keep sampled frames")
    assert_true(video_frames[0].transcript_text and "这一条是小猫发的" in video_frames[0].transcript_text[0], "video watch session should keep transcript snippets")
    video_recall_result = WatchRecallTool(workspace, video_watch_app.watch_session.recent).run(ToolRequest("watch.recall", {"query": "这个视频在讲什么"}))
    assert_true("这一条是小猫发的" in video_recall_result.display_card.summary, "video recall should answer from transcript context")
    assert_true("第 3/3 帧" in video_recall_result.display_card.body or "第 2/3 帧" in video_recall_result.display_card.body, "video recall should expose temporal frame context")
    assert_true("实时转写" in video_recall_result.display_card.body, "video recall should expose transcript context")

    live_video_watch_app = AgentCompanionApp(workspace)
    live_video_backend = FakeComputerBackend(
        workspace,
        observations=[
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/live-video-1.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/live-video-2.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/live-video-3.png", title="Bilibili Video"),
            _fake_computer_observation(workspace, rel="data/agent_companion/vision/live-video-4.png", title="Bilibili Video"),
        ],
    )
    live_video_watch_app.tools.register(
        ScreenObserveTool(
            workspace,
            computer_backend=live_video_backend,
            summarizer=SequenceSummarizer(),
            ocr=FakeOcrExtractor(video_ocr),
        )
    )
    live_video_watch_app.handle_user_text("这个视频在讲什么")
    live_video_frames = live_video_watch_app.watch_session.recent(5)
    assert_true(live_video_backend.observe_calls == 4, "current video questions should refresh with four sampled frames")
    assert_true(live_video_frames and live_video_frames[0].sequence_size == 4, "current video refresh should store dense temporal context")

    rolling_session = WatchSession(limit=2, transcript_limit=8, transcript_window_seconds=300)
    for index, text in enumerate(
        [
            "第一段说小猫正在靠近实验道具。",
            "第二段说小猫开始观察主人的动作。",
            "第三段说小猫做出了反应。",
            "第四段说实验结束，小猫很喜欢。",
        ],
        start=1,
    ):
        rolling_session.add(
            WatchFrame(
                user_question="陪我看这个视频",
                summary=f"连续采样第 {index} 帧",
                title="Bilibili Video",
                artifact=f"data/agent_companion/vision/rolling-{index}.png",
                model_status="skipped",
                transcript_text=[text],
                transcript_source="system_audio",
                transcript_status="success",
            )
        )
    rolling_state = rolling_session.transcript_state()
    assert_true(len(rolling_session.recent(5)) == 2, "visual watch frames should stay bounded")
    assert_true(rolling_state["segment_count"] == 4 and "第一段" in " ".join(rolling_state["recent_text"]), "rolling transcript memory should outlive the short frame buffer")
    rolling_context = rolling_session.recent_with_transcript(3)
    assert_true(rolling_context[0].model_status == "transcript_memory", "watch recall should receive a synthetic rolling transcript context frame")
    rolling_recall = WatchRecallTool(workspace, rolling_session.recent_with_transcript).run(ToolRequest("watch.recall", {"query": "刚才视频在讲什么"}))
    assert_true("第一段" in rolling_recall.display_card.summary and "第四段" in rolling_recall.display_card.body, "watch recall should answer from rolling transcript memory")
    assert_true(rolling_recall.display_card.artifacts == ["data/agent_companion/vision/rolling-4.png"], "rolling transcript recall should still keep the latest visual artifact")

    commentary = WatchCommentaryPlanner(workspace, AgentCompanionApp(workspace).character, min_interval_seconds=5)
    commentary.reset(now=100)
    assert_true(commentary.maybe_comment({"recent_text": ["第一段说小猫正在靠近实验道具。"], "summary": "最近 5 分钟的转写线索：第一段"}, now=103) is None, "watch commentary should respect cooldown")
    first_comment = commentary.maybe_comment({"recent_text": ["第一段说小猫正在靠近实验道具。"], "summary": "最近 5 分钟的转写线索：第一段"}, now=106)
    assert_true(first_comment is not None and first_comment.voice_text and first_comment.emotion in {"neutral", "happy", "thinking", "alert", "worried", "serious"}, "watch commentary should produce a safe short proactive comment")
    repeated_comment = commentary.maybe_comment({"recent_text": ["第一段说小猫正在靠近实验道具。"], "summary": "最近 5 分钟的转写线索：第一段"}, now=160)
    assert_true(repeated_comment is None, "watch commentary should not repeat unchanged transcript")
    next_comment = commentary.maybe_comment({"recent_text": ["第一段说小猫正在靠近实验道具。", "第二段说小猫开始观察主人的动作。"], "summary": "最近 5 分钟的转写线索：第一段 / 第二段"}, now=170)
    assert_true(next_comment is not None and "{" not in next_comment.voice_text, "watch commentary should react only to new transcript content")
    commentary.reset(now=200)
    assert_true(commentary.maybe_comment({"recent_text": ["第三段说小猫做出了反应。"], "summary": "第三段"}, now=230, min_interval_seconds=60) is None, "watch commentary should respect runtime interval overrides")

    watch_loop_bridge = JsonRpcBridge(workspace)
    watch_loop_summarizer = SequenceSummarizer()
    watch_loop_bridge.app.tools.register(
        ScreenObserveTool(
            workspace,
            computer_backend=FakeComputerBackend(
                workspace,
                observations=[
                    _fake_computer_observation(workspace, rel="data/agent_companion/vision/watch-loop-1.png", title="Bilibili Video"),
                    _fake_computer_observation(workspace, rel="data/agent_companion/vision/watch-loop-2.png", title="Bilibili Video"),
                ],
            ),
            summarizer=watch_loop_summarizer,
            ocr=FakeOcrExtractor(video_ocr),
        )
    )
    try:
        background_loop_config = watch_loop_bridge.background_configure_command({"enabled": True, "scope_type": "window", "label": "Bilibili Video"})
        assert_true(background_loop_config["ok"] and background_loop_config["background"]["active"], "background context should accept an approved watch window scope")
        loop_start = watch_loop_bridge.watch_loop_start_command({"query": "陪我看这个视频", "interval_seconds": 60, "sample_count": 2, "sample_interval_ms": 0})
        loop_state = loop_start["watch_loop"]
        assert_true(loop_state["active"] is True and loop_state["iterations"] >= 1, "watch loop start should capture immediately")
        assert_true(watch_loop_bridge.app.watch_session.has_context(), "watch loop should refresh watch session context")
        assert_true("这一条是小猫发的" in " ".join(loop_state["last_transcript"]), "watch loop should expose latest transcript snippets")
        assert_true("这一条是小猫发的" in " ".join(loop_state["rolling_transcript"]) and loop_state["rolling_summary"], "watch loop should expose rolling transcript memory")
        assert_true(watch_loop_summarizer.sequence_calls and "连续画面显示一只猫" in loop_state["last_visual_summary"], "watch loop first tick should capture a low-frequency visual summary")
        assert_true(loop_state["visual_status"] == "ok", "watch loop should expose visual summary status")
        background_after_tick = watch_loop_bridge.background_status_command()["background"]
        assert_true(background_after_tick["recent_count"] >= 1 and not background_after_tick["video_recording"], "watch loop should record only approved background summaries, not video")
        cleared_loop_background = watch_loop_bridge.background_clear_command()["background"]
        assert_true(cleared_loop_background["recent_count"] == 0 and cleared_loop_background["scope_count"] == 1, "background clear RPC should keep approved scopes while clearing summaries")
        loop_events = watch_loop_bridge.app.bus.drain()
        assert_true(any(event.agent_state.get("tool") == "watch.loop" for event in loop_events), "watch loop should emit status events without task cards")
        assert_true(any(event.agent_state.get("tool") == "background.context" for event in loop_events), "background configure/clear should emit auditable background events")
        loop_config = watch_loop_bridge.watch_loop_configure_command({"transcript_source": "ocr_subtitle", "proactive_enabled": False, "commentary_interval_seconds": 60, "vision_interval_ticks": 3})["watch_loop"]
        assert_true(loop_config["transcript_source"] == "ocr_subtitle" and loop_config["proactive_enabled"] is False, "watch loop should hot-update transcript source and proactive setting")
        assert_true(loop_config["commentary_interval_seconds"] == 60, "watch loop should expose proactive commentary interval")
        assert_true(loop_config["vision_interval_ticks"] == 3, "watch loop should expose low-frequency visual summary interval")
        assert_true("active_transcript_source" in loop_config, "watch loop should distinguish configured and active transcript sources")
        loop_status = watch_loop_bridge.watch_loop_status_command()["watch_loop"]
        assert_true(loop_status["active"] is True and loop_status["transcript_status"], "watch loop status RPC should expose current state")
        manual_visual = watch_loop_bridge.watch_loop_refresh_command({"force_visual_summary": True})["watch_loop"]
        assert_true(manual_visual["iterations"] > loop_status["iterations"], "watch loop manual refresh should run an immediate sample")
        assert_true(len(watch_loop_summarizer.sequence_calls) >= 2 and manual_visual["visual_status"] == "ok", "watch loop manual refresh should force a visual summary")
        loop_manual_config = watch_loop_bridge.watch_loop_configure_command({"vision_interval_ticks": 0})["watch_loop"]
        assert_true(loop_manual_config["vision_interval_ticks"] == 0, "watch loop should preserve manual-only visual summary mode")
        loop_recall = WatchRecallTool(workspace, watch_loop_bridge.app.watch_session.recent_with_transcript).run(ToolRequest("watch.recall", {"query": "刚才视频在讲什么"}))
        assert_true("这一条是小猫发的" in loop_recall.display_card.summary and "连续画面显示一只猫" in loop_recall.display_card.summary, "watch loop context should combine transcript and low-frequency vision summary")
        loop_stop = watch_loop_bridge.watch_loop_stop_command()["watch_loop"]
        assert_true(loop_stop["active"] is False, "watch loop stop should deactivate the session")
        inactive_refresh = watch_loop_bridge.watch_loop_refresh_command({"force_visual_summary": True})
        assert_true(inactive_refresh["ok"] is False and inactive_refresh["error"] == "watch_loop_inactive", "watch loop refresh should reject inactive sessions")
    finally:
        watch_loop_bridge.watch_loop.stop(emit=False)

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

    invalid_codex_dir = Path(tempfile.mkdtemp())
    invalid_codex = invalid_codex_dir / ("codex-invalid.exe" if os.name == "nt" else "codex-invalid")
    invalid_codex.write_text("this file exists but is not a valid executable\nC:\\secret\\codex.exe --token sk-test\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(invalid_codex, 0o755)
    previous_invalid_bin = os.environ.get("AGENT_COMPANION_CODEX_BIN")
    try:
        os.environ["AGENT_COMPANION_CODEX_BIN"] = str(invalid_codex)
        invalid_result = CodexTool(workspace).run(ToolRequest("codex.run", {"goal": "修复 bug"}))
        assert_true(not invalid_result.ok, "invalid Codex executable should fail closed")
        assert_true(not invalid_result.requires_approval, "invalid Codex executable must not request approval")
        assert_true("approval_request" not in invalid_result.agent_state, "invalid Codex executable must not create approval state")
        assert_true(invalid_result.agent_state["codex_run"]["status"] == "not_available", "invalid Codex executable should expose not_available status")
        _assert_no_codex_safe_text_leaks(invalid_result.agent_state["codex_run"], "invalid Codex state leaked raw launch details")
        _assert_no_codex_safe_text_leaks(invalid_result.display_card.summary, "invalid Codex card leaked raw launch details")
        _assert_no_codex_safe_text_leaks(invalid_result.display_card.body, "invalid Codex card body leaked raw launch details")
        _assert_no_codex_voice_leaks([invalid_result], "invalid Codex voice leaked raw machine detail")
    finally:
        if previous_invalid_bin is None:
            os.environ.pop("AGENT_COMPANION_CODEX_BIN", None)
        else:
            os.environ["AGENT_COMPANION_CODEX_BIN"] = previous_invalid_bin
        shutil.rmtree(invalid_codex_dir, ignore_errors=True)

    fake_codex_dir = Path(tempfile.mkdtemp())
    fake_codex = _write_fake_codex_executable(fake_codex_dir)
    previous_bin = os.environ.get("AGENT_COMPANION_CODEX_BIN")
    previous_mode = os.environ.get("JOI_FAKE_CODEX_MODE")
    try:
        os.environ["AGENT_COMPANION_CODEX_BIN"] = str(fake_codex)

        os.environ["JOI_FAKE_CODEX_MODE"] = "success"
        codex_success = CodexTool(workspace).run(ToolRequest("codex.run", {"goal": "修复 bug 并跑测试 --secret /Users/me/project"}))
        assert_true(codex_success.ok, "fake Codex success should complete")
        assert_true(codex_success.agent_state["codex_run"]["status"] == "completed", "Codex success should expose completed status")
        assert_true(codex_success.agent_state["codex_run"]["returncode"] == 0, "Codex success should preserve return code")
        assert_true(any(row.get("category") == "final" for row in codex_success.agent_state["codex_run"]["events"]), "Codex final event should be parsed")
        _assert_no_codex_safe_text_leaks(codex_success.agent_state["codex_run"], "Codex run state leaked raw JSONL details")
        _assert_no_codex_safe_text_leaks(codex_success.display_card.body, "Codex success card leaked raw paths or commands")
        _assert_no_codex_voice_leaks([codex_success], "Codex success voice leaked raw machine detail")

        os.environ["JOI_FAKE_CODEX_MODE"] = "fail"
        codex_failure = CodexTool(workspace).run(ToolRequest("codex.run", {"goal": "修复 bug"}))
        assert_true(not codex_failure.ok, "fake Codex nonzero exit should fail")
        assert_true(codex_failure.agent_state["codex_run"]["status"] == "failed", "Codex nonzero exit should expose failed status")
        assert_true(codex_failure.agent_state["codex_run"]["returncode"] == 7, "Codex nonzero exit should preserve return code")
        _assert_no_codex_safe_text_leaks(codex_failure.agent_state["codex_run"], "Codex failure state leaked raw stderr details")
        _assert_no_codex_safe_text_leaks(codex_failure.display_card.body, "Codex failure card leaked raw stderr details")
        _assert_no_codex_voice_leaks([codex_failure], "Codex failure voice leaked raw machine detail")

        os.environ["JOI_FAKE_CODEX_MODE"] = "permission"
        codex_permission = CodexTool(workspace).run(ToolRequest("codex.run", {"goal": "改代码"}))
        assert_true(codex_permission.requires_approval, "resumable Codex permission request should become Joi approval")
        assert_true(codex_permission.agent_state["codex_run"]["status"] == "permission_required", "Codex permission status missing")
        assert_true(codex_permission.agent_state["codex_run"]["permission_required"], "Codex permission flag missing")
        codex_permission_request = codex_permission.agent_state["approval_request"]
        assert_true(codex_permission_request["tool"] == "codex.run", "Codex permission approval should rerun codex.run")
        assert_true("codex_permission_hash" in codex_permission_request["arguments"], "Codex permission approval should bind permission hash")
        _assert_no_codex_safe_text_leaks(codex_permission.agent_state["codex_run"], "Codex permission state leaked raw command or approval id")
        _assert_no_codex_safe_text_leaks(codex_permission.display_card.body, "Codex permission card leaked raw command or approval id")
        _assert_no_codex_voice_leaks([codex_permission], "Codex permission voice leaked raw machine detail")

        os.environ["JOI_FAKE_CODEX_MODE"] = "permission_no_resume"
        codex_fail_closed = CodexTool(workspace).run(ToolRequest("codex.run", {"goal": "改代码"}))
        assert_true(not codex_fail_closed.ok and not codex_fail_closed.requires_approval, "non-resumable Codex permission should fail closed")
        assert_true("暂不能继续" in codex_fail_closed.display_card.summary, "non-resumable Codex permission should explain fail-closed state")
        assert_true(codex_fail_closed.agent_state["codex_run"]["status"] != "permission_required", "non-resumable Codex permission must not look pending")
        assert_true(codex_fail_closed.agent_state["codex_run"]["status"] == "fail_closed", "non-resumable Codex permission should use fail-closed status")
        assert_true(codex_fail_closed.agent_state["codex_run"]["permission_required"] is False, "non-resumable Codex permission must not show UI waiting state")
        assert_true(codex_fail_closed.agent_state["codex_run"]["permission_detected"] is True, "non-resumable Codex permission should still record detection")
        assert_true("approval_request" not in codex_fail_closed.agent_state, "non-resumable Codex permission must not create approval request")
        _assert_no_codex_safe_text_leaks(codex_fail_closed.agent_state["codex_run"], "fail-closed Codex state leaked raw permission detail")
        _assert_no_codex_voice_leaks([codex_fail_closed], "fail-closed Codex voice leaked raw machine detail")

        os.environ["JOI_FAKE_CODEX_MODE"] = "permission"
        codex_app = AgentCompanionApp(workspace)
        initial_codex_events = codex_app.handle_user_text("修复这个项目 bug 并跑测试")
        initial_codex_approval = _approval_payload(initial_codex_events)
        assert_true(initial_codex_approval.get("tool") == "codex.run", "coding task should still require initial Codex approval")
        permission_events = codex_app.resolve_approval(str(initial_codex_approval["approval_id"]), approved=True)
        bridge_approval = _approval_payload(permission_events)
        assert_true(bridge_approval.get("tool") == "codex.run", "Codex permission bridge should create a second approval")
        assert_true(any(event.type == EventType.APPROVAL_REQUIRED and event.agent_state.get("codex_run", {}).get("permission_required") for event in permission_events), "Codex permission card should carry runtime state")
        bridge_completed = codex_app.resolve_approval(str(bridge_approval["approval_id"]), approved=True)
        assert_true(any(event.type == EventType.TOOL_COMPLETED and event.agent_state.get("codex_run", {}).get("status") == "completed" for event in bridge_completed), "approved Codex permission should resume fake runner")
        _assert_no_codex_voice_leaks(initial_codex_events + permission_events + bridge_completed, "Codex approval/resume voice leaked raw machine detail")

        denied_app = AgentCompanionApp(workspace)
        denied_initial = denied_app.handle_user_text("修复这个项目 bug 并跑测试")
        denied_permission = denied_app.resolve_approval(str(_approval_payload(denied_initial)["approval_id"]), approved=True)
        denied_approval = _approval_payload(denied_permission)
        denied_events = denied_app.resolve_approval(str(denied_approval["approval_id"]), approved=False)
        assert_true(any(event.type == EventType.TASK_FAILED for event in denied_events), "denied Codex permission should cancel task")
        assert_true(any("拒绝" in event.display_card.summary or "拒绝" in event.display_card.title for event in denied_events), "denied Codex permission should show friendly failure")
        denied_duplicate = denied_app.resolve_approval(str(denied_approval["approval_id"]), approved=True)
        assert_true(not any(event.agent_state.get("codex_run", {}).get("status") == "completed" for event in denied_duplicate), "duplicate Codex permission must not continue")
        _assert_no_codex_voice_leaks(denied_permission + denied_events + denied_duplicate, "denied Codex permission voice leaked raw machine detail")

        expired_app = AgentCompanionApp(workspace)
        expired_initial = expired_app.handle_user_text("修复这个项目 bug 并跑测试")
        expired_permission = expired_app.resolve_approval(str(_approval_payload(expired_initial)["approval_id"]), approved=True)
        expired_approval = _approval_payload(expired_permission)
        expired_app.approval_ttl_seconds = -1
        expired_events = expired_app.resolve_approval(str(expired_approval["approval_id"]), approved=True)
        assert_true(any(event.type == EventType.TASK_FAILED for event in expired_events), "expired Codex permission should fail safely")
        assert_true(not any(event.agent_state.get("codex_run", {}).get("status") == "completed" for event in expired_events), "expired Codex permission must not continue")
        _assert_no_codex_voice_leaks(expired_permission + expired_events, "expired Codex permission voice leaked raw machine detail")
    finally:
        if previous_bin is None:
            os.environ.pop("AGENT_COMPANION_CODEX_BIN", None)
        else:
            os.environ["AGENT_COMPANION_CODEX_BIN"] = previous_bin
        if previous_mode is None:
            os.environ.pop("JOI_FAKE_CODEX_MODE", None)
        else:
            os.environ["JOI_FAKE_CODEX_MODE"] = previous_mode
        shutil.rmtree(fake_codex_dir, ignore_errors=True)

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
    semantic_candidate = semantic_approval_event.agent_state["target_candidate"]
    assert_true(isinstance(semantic_candidate.get("evidence"), dict), "semantic approval event should preserve candidate evidence")
    semantic_audit = _audit_rows(semantic_events)
    evidence_audit_rows = [row for row in semantic_audit if row.get("event_type") == "target_candidates"]
    assert_true(evidence_audit_rows and evidence_audit_rows[-1].get("candidate_evidence"), "semantic audit should record sanitized candidate evidence")
    assert_true(evidence_audit_rows[-1].get("skill_id") == "joi.computer_use", "Semantic grounding audit should carry native skill id")
    audit_evidence_text = str(evidence_audit_rows[-1].get("candidate_evidence"))
    assert_true(not any(fragment in audit_evidence_text for fragment in ["bbox", "screen_center", "data/", ".png", "approval-", "task-", "ButtonControl", "TextControl"]), "semantic audit evidence leaked raw target details")
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
    for field in ("task_id", "event_type", "timestamp", "sanitized_summary", "risk_level", "approval_id", "approval_status", "tool_name", "skill_id", "action_name", "sanitized_arguments", "before_artifacts", "after_artifacts", "verification_result"):
        assert_true(field in approval_audit, f"audit event missing stable field: {field}")
    assert_true(approval_audit["risk_level"] == "medium", "computer approval audit should preserve risk")
    assert_true(approval_audit["skill_id"] == "joi.computer_use", "computer approval audit should carry native skill id")
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
    assert_true(noop_rows[-1].get("skill_id") == "joi.computer_use", "action verification audit should carry native skill id")
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

    unconfigured_bridge = JsonRpcBridge(
        workspace,
        asr_provider=FailingAsrProvider("asr_unconfigured"),
        asr_state=AsrRuntimeState(False, False, "none", error="asr_unconfigured"),
    )
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
    assert_true(ready_payload["audit"]["version"] == AUDIT_SCHEMA_VERSION and ready_payload["audit"]["safe_for_display"], "Core ready payload should expose safe audit status")
    assert_true(ready_payload["background"]["version"] == BACKGROUND_CONTEXT_VERSION and ready_payload["background"]["safe_for_display"] and ready_payload["background"]["video_recording"] is False, "Core ready payload should expose safe background context status")
    assert_true(ready_payload["skills"]["version"] == SKILL_MANIFEST_VERSION and ready_payload["skills"]["safe_for_display"], "Core ready payload should expose safe native skill manifest")
    skill_ids = {row["id"] for row in ready_payload["skills"]["skills"]}
    assert_true(
        {"joi.codex", "joi.browser", "joi.computer_use", "joi.memory", "joi.voice_input", "joi.voice_output", "joi.ok_ww"}.issubset(skill_ids),
        "P8 skill manifest should include native Codex, Browser/Computer Use, Memory, ASR/TTS, and OK-WW skills",
    )
    skill_rows = {row["id"]: row for row in ready_payload["skills"]["skills"]}
    assert_true(skill_rows["joi.computer_use"]["permission_level"] == "medium" and "computer.click" in skill_rows["joi.computer_use"]["tools"], "Computer Use skill should be medium-risk and tool-bound")
    assert_true("background.configure" in skill_rows["joi.watch"]["rpc_methods"] and "background.clear" in skill_rows["joi.watch"]["rpc_methods"], "Watch skill should include constrained background context controls")
    assert_true(skill_rows["joi.voice_input"]["configured"] and skill_rows["joi.voice_input"]["local_capability"] == "ready", "Voice input skill should mirror ASR runtime readiness")
    assert_true(skill_rows["joi.ok_ww"]["supports_dry_run"], "OK-WW skill should advertise dry-run first")
    assert_true("runtime.update_config" in skill_rows["joi.runtime_config"]["tools"], "Runtime config should be bound to a native skill")
    skill_manifest_payload = ready_bridge.skill_manifest_command()
    assert_true(skill_manifest_payload["ok"] and skill_manifest_payload["skills"]["version"] == SKILL_MANIFEST_VERSION, "skills.list RPC should return the native skill manifest")
    audit_recent_payload = ready_bridge.audit_recent_command(5)
    assert_true(audit_recent_payload["ok"] and audit_recent_payload["audit"]["version"] == AUDIT_SCHEMA_VERSION and audit_recent_payload["audit"]["safe_for_display"], "audit.recent RPC should return safe persisted audit records")
    direct_skill_rows = {
        row["id"]: row
        for row in build_native_skill_manifest(
            workspace,
            asr_state=AsrRuntimeState(False, False, "none", error="asr_unconfigured"),
            tts_status={"enabled": False, "configured": False, "provider": "none"},
            memory_status={"enabled": False},
        )["skills"]
    }
    assert_true(direct_skill_rows["joi.voice_input"]["local_capability"] == "off" and direct_skill_rows["joi.memory"]["enabled"] is False, "Skill manifest should mirror disabled ASR and memory states")

    disabled_skill_tmpdir = tempfile.mkdtemp()
    try:
        disabled_skill_tmp = Path(disabled_skill_tmpdir)
        disabled_character_dir = disabled_skill_tmp / "agent_companion" / "config"
        disabled_character_dir.mkdir(parents=True, exist_ok=True)
        (disabled_character_dir / "default_character.yaml").write_text(
            """
id: test-joi
name: Joi
asset_policy: test
style:
  tone: concise
  speech: safe
  boundaries: []
persona: "Test companion."
voice:
  default_lang: zh
  start: "开始。"
  progress: "处理中。"
  done: "完成。"
  failed: "失败。"
""",
            encoding="utf-8",
        )
        (disabled_skill_tmp / "config.yaml").write_text(
            """
llm:
  use_mock: true
characters:
  - name: Joi
    color: "#d76f8f"
    setting: "local test companion"
skills:
  joi.computer_use:
    enabled: false
""",
            encoding="utf-8",
        )
        disabled_app = AgentCompanionApp(disabled_skill_tmp)
        disabled_events = disabled_app.handle_user_text("点击 100,200")
        blocked_events = [event for event in disabled_events if event.type == EventType.TOOL_FAILED and event.agent_state.get("block_reason") == "skill_disabled"]
        assert_true(blocked_events and blocked_events[-1].agent_state.get("skill_id") == "joi.computer_use", "Disabled native skills should block execution before approval")
        assert_true(not any(event.type == EventType.APPROVAL_REQUIRED for event in disabled_events), "Disabled native skills should not create approval requests")
        assert_true(not any(event.type == EventType.TOOL_COMPLETED and event.agent_state.get("tool") == "computer.click" for event in disabled_events), "Disabled native skills must not run bound tools")
        disabled_audit = disabled_app.audit_store.recent(20)["records"]
        assert_true(any(row.get("outcome") == "blocked" and row.get("block_reason") == "skill_disabled" and row.get("skill_id") == "joi.computer_use" for row in disabled_audit), "Persistent audit should record disabled skill policy blocks")
    finally:
        shutil.rmtree(disabled_skill_tmpdir, ignore_errors=True)

    skill_payload_text = str(ready_payload["skills"])
    assert_true(all(fragment not in skill_payload_text for fragment in ["sk-", "api_key", "base_url", "/Users/", "C:\\", "secret"]), "Skill manifest leaked secrets, endpoints, or private paths")
    runtime_provider_names = {row["name"] for row in ready_payload["runtime"]["providers"]}
    assert_true(
        {"asr", "tts", "ocr", "fast", "reasoning", "vision", "code", "summarize", "voice_style", "computer_use", "audit_verification"}.issubset(runtime_provider_names),
        "Runtime status should include provider, stable model routes, platform, audit, and verification rows",
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
        assert_true(configured_rows["fast"]["model"] == "gpt-public-text", "Fast model status should expose safe public model name")
        assert_true(configured_rows["reasoning"]["model"] == "gpt-public-text" and configured_rows["reasoning"]["notes"], "Reasoning model status should expose base fallback")
        assert_true(configured_rows["vision"]["model"] == "gpt-public-vision", "Vision model status should expose safe public model name")
        assert_true(configured_rows["voice_style"]["model"] == "gpt-public-expression", "Voice style model status should expose safe public model name")
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
        assert_true(unconfigured_rows["fast"]["state"] == "off" and unconfigured_rows["vision"]["state"] == "off" and unconfigured_rows["voice_style"]["state"] == "off", "Unconfigured model runtime states should be off")

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
        assert_true(redacted_rows["fast"]["model"] == "redacted", "Local model paths should be redacted from runtime status")
        assert_true("/Users/private" not in str(redacted_runtime) and "joi.gguf" not in str(redacted_runtime), "Runtime status should not expose local model paths")

    shell_api_source = (workspace / "agent_companion" / "shell" / "src" / "api.ts").read_text(encoding="utf-8")
    assert_true("transcribeVoice(audioBase64: string, mimeType: string, timeoutMs: number)" in shell_api_source, "voice RPC should accept a method-specific timeout")
    assert_true("语音识别等太久了" in shell_api_source, "voice RPC timeout should be user-friendly")
    assert_true("runtime.config.preview" in shell_api_source and "runtime.config.apply" in shell_api_source, "Shell API should expose runtime config preview/apply RPC methods")
    assert_true("skills.list" in shell_api_source and "skillsList()" in shell_api_source and "audit.recent" in shell_api_source and "auditRecent" in shell_api_source, "Shell API should expose native skill manifest and audit RPCs")
    assert_true("background.status" in shell_api_source and "background.configure" in shell_api_source and "background.clear" in shell_api_source, "Shell API should expose constrained background context RPCs")
    assert_true("watch.loop.start" in shell_api_source and "watch.loop.stop" in shell_api_source and "watch.loop.configure" in shell_api_source and "watch.loop.refresh" in shell_api_source, "Shell API should expose realtime watch loop RPC methods")
    assert_true("memory.status" in shell_api_source and "memory.recall" in shell_api_source and "memory.browse_vault" in shell_api_source and "memory.save_candidate" in shell_api_source and "memory.reject_candidate" in shell_api_source and "memory.set_enabled" in shell_api_source and "memory.delete" in shell_api_source and "memory.clear" in shell_api_source, "Shell API should expose memory authorization and recall RPC methods")
    voice_runtime_source = (workspace / "agent_companion" / "shell" / "src" / "voiceRuntime.ts").read_text(encoding="utf-8")
    assert_true("shouldPlayVoiceAudio" in voice_runtime_source and "eventEpoch === currentEpoch" in voice_runtime_source, "voice runtime should suppress stale audio by epoch")
    assert_true("event_created_at" in voice_runtime_source, "voice runtime key should include event identity")
    app_vue_source = (workspace / "agent_companion" / "shell" / "src" / "App.vue").read_text(encoding="utf-8")
    assert_true("beginNewVoiceIntent()" in app_vue_source and "voiceEventEpochs.get" in app_vue_source, "Shell should bump and compare voice epochs")
    assert_true("event_created_at: event.created_at" in app_vue_source, "Shell should key voice audio by event timestamp")
    assert_true("isPlayableVoiceEvent" in app_vue_source and "voice_audio_data_url" in app_vue_source, "Shell should register playable tool-start voice events and prefer inline voice audio")
    assert_true("lastTtsError.value = error instanceof Error" in app_vue_source, "Shell should surface audio playback failures instead of swallowing them")
    assert_true("runtimeStatusRows" in app_vue_source and "provider-card" in app_vue_source and "运行设置" in app_vue_source, "Shell developer mode should expose runtime provider settings/status view")
    assert_true("providerMeta" in app_vue_source and "providerErrorLabel" in app_vue_source, "Shell runtime status view should render sanitized provider details")
    assert_true("tesseract_missing" in app_vue_source and "tesseract_unavailable" in app_vue_source, "Shell runtime status view should label Tesseract runtime probe failures")
    assert_true("runtimeDraft" in app_vue_source and "previewRuntimeSettings" in app_vue_source and "applyRuntimeSettings" in app_vue_source, "Shell developer panel should include runtime settings dry-run/apply controls")
    assert_true("runtime_settings" in app_vue_source and "runtimePreview" in app_vue_source and "提交审批" in app_vue_source, "Shell runtime settings UI should refresh from safe ready payload and require approval apply")
    assert_true("api_key" not in app_vue_source and "server_url" not in app_vue_source and "base_url" not in app_vue_source and "refer_audio_path" not in app_vue_source and "gpt_sovits_work_path" not in app_vue_source, "Shell runtime settings UI must not expose secret, endpoint, or path fields")
    assert_true("target-overlays" in app_vue_source and "targetPreviewSummary" in app_vue_source, "Shell should render semantic target approval previews")
    assert_true("target-list" in app_vue_source and "targetRank" in app_vue_source, "Shell should show ranked semantic target candidates")
    assert_true("targetSource" in app_vue_source and "UI控件" in app_vue_source and "融合" in app_vue_source and "视觉" in app_vue_source, "Shell should show semantic target candidate source")
    assert_true("targetConfidenceChip" in app_vue_source and "targetRiskChip" in app_vue_source and "targetConfirmationReason" in app_vue_source, "Shell should show semantic target evidence chips and confirmation reason")
    assert_true("auditEvidenceRows" in app_vue_source and "candidate_evidence" in app_vue_source, "Shell audit timeline should render candidate evidence")
    assert_true("selectTargetCandidate" in app_vue_source and "selectSemanticTarget" in app_vue_source, "Shell candidate cards should continue semantic target selection through explicit RPC")
    assert_true("currentSemanticSelectionId" in app_vue_source and "selectionExpired" in app_vue_source, "Shell should disable stale or expired semantic target candidates")
    assert_true("选 ${rank}" not in app_vue_source, "Shell candidate buttons should not send natural-language selection text")
    assert_true("audit-panel" in app_vue_source and "auditEventsForTask" in app_vue_source, "Shell developer mode should render Computer Use audit timeline")
    assert_true("auditArgumentRows" in app_vue_source and "auditArtifacts" in app_vue_source, "Shell audit view should show sanitized arguments and before/after artifacts")
    assert_true("auditSignalRows" in app_vue_source and "image_changed" in app_vue_source, "Shell audit view should show sanitized image verification signals")
    assert_true("codexTimeline" in app_vue_source and "codexRunStatusLabel" in app_vue_source and "Codex 运行审计" in app_vue_source, "Shell developer mode should show sanitized Codex run audit state")
    assert_true("fail_closed" in app_vue_source and "权限不可继续" in app_vue_source, "Shell should label Codex fail-closed permission state")
    visual_fixture_manifest = (workspace / "tests" / "fixtures" / "visual_detector" / "visual_cases.json").read_text(encoding="utf-8")
    image_fixture_manifest = (workspace / "tests" / "fixtures" / "image_verification" / "image_diff_cases.json").read_text(encoding="utf-8")
    semantic_fixture_manifest = (workspace / "tests" / "fixtures" / "semantic_grounding" / "semantic_cases.json").read_text(encoding="utf-8")
    semantic_fixture_cases = json.loads(semantic_fixture_manifest)
    assert_true(isinstance(semantic_fixture_cases, list) and len(semantic_fixture_cases) > 38, "semantic grounding suite should expand beyond the P4.26 baseline")
    assert_true("video_canvas_controls" in visual_fixture_manifest and "canvas_button_cluster" in visual_fixture_manifest, "committed visual detector regression fixtures should be present")
    assert_true("sparse_page_bottom_action_strip" in visual_fixture_manifest and "modal_low_contrast_actions" in visual_fixture_manifest, "promoted synthetic visual calibration fixtures should be present")
    assert_true("image_diff_subtle_visible_change" in image_fixture_manifest and "image_diff_tiny_compression_noise" in image_fixture_manifest, "committed image-diff regression fixtures should be present")
    assert_true("image_diff_thin_progress_change" in image_fixture_manifest and "image_diff_cursor_blink_noop" in image_fixture_manifest, "promoted synthetic image-diff calibration fixtures should be present")
    assert_true("semantic_uia_ocr_disagreement_selection" in semantic_fixture_manifest and "semantic_visual_only_selection" in semantic_fixture_manifest, "semantic grounding calibration fixtures should be present")
    assert_true("semantic_static_uia_text_selection" in semantic_fixture_manifest and "semantic_disabled_uia_button_selection" in semantic_fixture_manifest, "static and disabled UIA semantic fixtures should be present")
    assert_true("semantic_dense_duplicate_regions_selection" in semantic_fixture_manifest and "semantic_modal_foreground_fused_approval" in semantic_fixture_manifest, "dense semantic duplicate and fused approval fixtures should be present")
    assert_true("semantic_missing_capture_rect_clarification" in semantic_fixture_manifest and "semantic_bad_scale_clarification" in semantic_fixture_manifest and "semantic_out_of_bounds_uia_clarification" in semantic_fixture_manifest, "dense semantic grounding fixtures should cover unsafe capture rect cases")
    assert_true("semantic_dense_browser_repeated_nav_selection" in semantic_fixture_manifest and "semantic_settings_static_disabled_neighbor_selection" in semantic_fixture_manifest, "dense browser and desktop settings semantic calibration fixtures should be present")
    assert_true("semantic_canvas_hud_visual_ocr_conflict_selection" in semantic_fixture_manifest and "semantic_dense_adjacent_actionable_fused_selection" in semantic_fixture_manifest, "game HUD and adjacent actionable semantic calibration fixtures should be present")
    assert_true("semantic_modal_foreground_background_priority_approval" in semantic_fixture_manifest and "semantic_nested_popover_background_conflict_selection" in semantic_fixture_manifest, "modal foreground/background semantic calibration fixtures should be present")
    assert_true("semantic_nonzero_fractional_capture_approval" in semantic_fixture_manifest and "semantic_retina_scale_capture_approval" in semantic_fixture_manifest and "semantic_fractional_scale_mismatch_clarification" in semantic_fixture_manifest, "fractional and retina capture-scale semantic fixtures should be present")
    assert_true("semantic_edge_offset_low_confidence_selection" in semantic_fixture_manifest, "edge-offset low-confidence semantic fixture should be present")
    assert_true("semantic_stale_uia_snapshot_clarification" in semantic_fixture_manifest and "semantic_clipped_foreground_edge_selection" in semantic_fixture_manifest, "stale UIA and clipped foreground semantic fixtures should be present")
    assert_true("semantic_multi_window_background_conflict_selection" in semantic_fixture_manifest and "semantic_truncated_dropdown_oob_selection" in semantic_fixture_manifest, "multi-window conflict and truncated dropdown semantic fixtures should be present")
    assert_true("semantic_partial_capture_rect_clarification" in semantic_fixture_manifest and "semantic_partial_capture_rect_trusted_approval" in semantic_fixture_manifest, "partial capture rect semantic fixtures should cover fail-closed and approval paths")
    assert_true("semantic_clipped_uia_center_outside_clarification" in semantic_fixture_manifest and "semantic_clipped_uia_selection_still_untrusted" in semantic_fixture_manifest, "clipped UIA screen-bounds fixtures should cover direct and selected unsafe paths")
    assert_true("semantic_uia_screen_bbox_inside_approval" in semantic_fixture_manifest, "trusted UIA screen-bounds fixture should cover positive approval path")
    assert_true("semantic_multi_monitor_offset_mismatch_clarification" in semantic_fixture_manifest and "semantic_window_moved_between_observations_clarification" in semantic_fixture_manifest, "multi-monitor offset and moved-window semantic fixtures should fail closed")
    assert_true("semantic_mixed_monitor_scale_approval" in semantic_fixture_manifest and "semantic_mixed_scale_window_overlap_same_label_selection" in semantic_fixture_manifest, "mixed-scale approval and overlapping-window selection fixtures should be present")
    assert_true("semantic_dense_repeated_actionable_mixed_monitor_selection" in semantic_fixture_manifest, "dense repeated actionable mixed-monitor semantic fixture should be present")
    assert_true("semantic_focus_churn_current_vs_stale_selection" in semantic_fixture_manifest and "semantic_focus_churn_old_window_geometry_clarification" in semantic_fixture_manifest, "focus churn semantic calibration fixtures should be present")
    assert_true("semantic_cross_monitor_drag_stale_geometry_clarification" in semantic_fixture_manifest, "cross-monitor stale geometry semantic fixture should be present")
    assert_true("semantic_dense_browser_topbar_repeated_actions_selection" in semantic_fixture_manifest and "semantic_game_canvas_hud_sparse_visual_cluster_selection" in semantic_fixture_manifest, "real-layout browser top-bar and game HUD semantic fixtures should be present")
    assert_true("semantic_modal_popover_background_competing_selection" in semantic_fixture_manifest, "modal/popover background competition semantic fixture should be present")
    eval_source = (workspace / "tools" / "eval_visual_detector.py").read_text(encoding="utf-8")
    assert_true("local private image verification eval: skipped" in eval_source and "image_diff_cases.local.json" in eval_source, "local private image-diff eval should skip when missing")
    assert_true("_print_private_results" in eval_source and "failure_category" in eval_source and "local_private_case_" in eval_source, "local private eval output should be sanitized")
    assert_true("local private semantic grounding eval: skipped" in eval_source and "semantic_cases.local.json" in eval_source, "local private semantic eval should skip when missing")
    assert_true("approval_tool" in eval_source and "_capture_rect_from_case" in eval_source and "SemanticTargetSelectionTool" in eval_source, "semantic eval should cover approval, selection, and capture-rect grounding paths")
    expected_semantic_categories = {
        "stale_accessibility_geometry",
        "ambiguous_repeated_label",
        "visual_only_low_confidence",
        "capture_rect_untrusted",
        "screen_center_outside_capture",
        "modal_background_conflict",
        "sparse_canvas_no_uia",
        "unexpected_direct_approval",
    }
    assert_true(set(SEMANTIC_CALIBRATION_FAILURE_CATEGORIES) == expected_semantic_categories, "semantic calibration categories should stay stable")
    local_semantic_ok, local_semantic_results, local_semantic_categories, local_semantic_skipped = run_local_semantic_calibration(
        workspace,
        workspace / "data" / "local_visual_eval" / "semantic_cases.test-missing.local.json",
        workspace / "data" / "local_visual_eval",
    )
    assert_true(local_semantic_ok and local_semantic_skipped and not local_semantic_results and not local_semantic_categories, "missing local semantic calibration manifest should skip safely")
    calibration_source = (workspace / "tools" / "calibrate_semantic_grounding.py").read_text(encoding="utf-8")
    assert_true("run_local_semantic_calibration" in calibration_source and "SEMANTIC_CALIBRATION_FAILURE_CATEGORIES" in calibration_source, "semantic calibration runner should reuse eval logic and stable categories")
    assert_true("LOCAL_SEMANTIC_CASE_FILE" in calibration_source and "data/local_visual_eval" not in calibration_source, "calibration runner should use shared local manifest constants without printing private paths")
    calibration_probe = subprocess.run(
        [
            sys.executable,
            str(workspace / "tools" / "calibrate_semantic_grounding.py"),
            "--manifest",
            str(workspace / "data" / "local_visual_eval" / "semantic_cases.test-missing.local.json"),
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    assert_true(calibration_probe.returncode == 0, "missing local semantic calibration manifest should exit successfully")
    calibration_output = f"{calibration_probe.stdout}\n{calibration_probe.stderr}"
    forbidden_calibration_output = ["data/", "local_visual_eval", "semantic_cases", ".png", ".ppm", "http", "C:\\", "/Users/", "目标", "账号"]
    assert_true(all(fragment not in calibration_output for fragment in forbidden_calibration_output), "semantic calibration runner output leaked private manifest details")
    categories_probe = subprocess.run(
        [
            sys.executable,
            str(workspace / "tools" / "calibrate_semantic_grounding.py"),
            "--list-categories",
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    listed_categories = {line.strip() for line in categories_probe.stdout.splitlines() if line.strip()}
    assert_true(categories_probe.returncode == 0 and listed_categories == expected_semantic_categories, "calibration runner should list only stable categories")
    assert_true(not categories_probe.stderr, "calibration category listing should not emit errors")
    invalid_json_output = _run_private_semantic_calibration_probe(workspace, "semantic_cases.invalid-json.local.json", '{"broken":')
    assert_true("local semantic calibration: failed" in invalid_json_output and "private manifest: invalid" in invalid_json_output, "invalid JSON manifest should fail with sanitized invalid report")
    not_list_output = _run_private_semantic_calibration_probe(workspace, "semantic_cases.not-list.local.json", {"image": "C:\\Users\\Alice\\Desktop\\账号.png", "ocr": "账号 https://private.example"})
    assert_true("private manifest: invalid" in not_list_output, "non-list manifest should fail with sanitized invalid report")
    missing_image_size_output = _run_private_semantic_calibration_probe(
        workspace,
        "semantic_cases.missing-size.local.json",
        [
            {
                "id": "private_case_missing_size",
                "image": "C:\\Users\\Alice\\Pictures\\账号按钮.png",
                "query": "点真实目标",
                "ocr_blocks": [{"text": "账号 Alice https://private.example/login", "bbox": [10, 10, 80, 24], "confidence": 0.9}],
            }
        ],
    )
    assert_true("private manifest: invalid" in missing_image_size_output, "missing image_size case should fail with sanitized invalid report")
    private_path_output = _run_private_semantic_calibration_probe(
        workspace,
        "semantic_cases.private-path.local.json",
        [
            {
                "id": "private_case_path",
                "image": "C:\\Users\\Alice\\Pictures\\账号按钮.png",
                "query": "点真实目标",
                "image_size": [400, 225],
                "ocr_blocks": [],
                "expected": {"requires_approval": True},
                "calibration_categories": ["capture_rect_untrusted"],
            }
        ],
    )
    assert_true("failure_categories:" in private_path_output and "capture_rect_untrusted" in private_path_output, "private path failure should report only abstract categories")
    private_text_output = _run_private_semantic_calibration_probe(
        workspace,
        "semantic_cases.private-text.local.json",
        [
            {
                "id": "private_case_text",
                "image": "/Users/Alice/private/账号按钮.ppm",
                "query": "点真实目标",
                "image_size": [400, 225],
                "ocr_blocks": [{"text": "账号 Alice https://private.example/account", "bbox": [20, 20, 120, 28], "confidence": 0.92}],
                "expected": {"requires_approval": True},
                "calibration_categories": ["ambiguous_repeated_label"],
            }
        ],
    )
    assert_true("failure_categories:" in private_text_output and "ambiguous_repeated_label" in private_text_output, "private OCR text failure should report only abstract categories")
    closeout_doc = (workspace / "docs" / "P4_CLOSEOUT_EXPERIENCE.md").read_text(encoding="utf-8")
    for scene in ("browser_click", "watch_page_video", "canvas_video_controls", "game_hud"):
        assert_true(scene in closeout_doc, f"P4 closeout doc should include scene: {scene}")
    for heading in ("用户要说的自然语言", "预期任务卡表现", "预期候选 evidence chips", "预期语音表现", "通过标准", "失败时记录什么", "隐私注意事项"):
        assert_true(closeout_doc.count(heading) >= 4, f"P4 closeout doc should include heading for every script: {heading}")
    assert_true(
        all(fragment in closeout_doc for fragment in ("不提交截图", "OCR", "窗口标题", "账号", "URL", "路径", "approval ids")),
        "P4 closeout doc should state privacy boundaries",
    )
    closeout_tool_source = (workspace / "tools" / "p4_closeout_report.py").read_text(encoding="utf-8")
    assert_true("p4_closeout_report.local.md" in closeout_tool_source and '"data" / "local_visual_eval"' in closeout_tool_source, "P4 report tool should write under ignored local_visual_eval")
    report_path = workspace / "data" / "local_visual_eval" / "p4_closeout_report.local.md"
    report_path.unlink(missing_ok=True)
    closeout_init = _run_p4_closeout_report_tool(workspace, "--init")
    assert_true(closeout_init.returncode == 0 and report_path.is_file(), "P4 closeout report init should create local report")
    closeout_add = _run_p4_closeout_report_tool(workspace, "--add", "browser_click", "--status", "pass", "--category", "ok", "--note", "候选说明清楚")
    assert_true(closeout_add.returncode == 0, "P4 closeout report add should accept sanitized notes")
    report_text = report_path.read_text(encoding="utf-8")
    assert_true("| browser_click | pass | ok | 候选说明清楚 |" in report_text, "P4 closeout report should record scene/status/category/note")
    forbidden_report_text = ["C:\\", "/Users/", "http", "example", "data/", "local_visual_eval", ".png", ".ppm", "task-", "approval-", "账号", "OCR 原文"]
    assert_true(all(fragment not in report_text for fragment in forbidden_report_text), "P4 closeout report leaked private fields")
    rejected_report = _run_p4_closeout_report_tool(
        workspace,
        "--add",
        "browser_click",
        "--status",
        "fail",
        "--category",
        "voice_leak",
        "--note",
        "账号 https://example.com C:\\secret\\screen.png task-private",
    )
    rejected_output = f"{rejected_report.stdout}\n{rejected_report.stderr}"
    assert_true(rejected_report.returncode == 2 and "rejected" in rejected_output, "P4 closeout report should reject obvious private notes")
    assert_true(all(fragment not in rejected_output for fragment in forbidden_report_text), "P4 closeout report rejection leaked private input")
    report_path.unlink(missing_ok=True)
    assert_true(run_visual_detector_eval(workspace, verbose=False) == 0, "visual/image verification eval should pass committed suites and skip or run local private suites safely")
    windows_focus_source = (workspace / "agent_companion" / "core" / "windows_focus.py").read_text(encoding="utf-8")
    assert_true("WindowFromPoint" in windows_focus_source and "GetAncestor" in windows_focus_source, "Windows focus helper should resolve the window underneath hidden Joi")
    assert_true("joi desktop" in windows_focus_source, "Windows focus helper should recognize the Tauri Joi Desktop title")
    windows_observer_source = (workspace / "agent_companion" / "core" / "vision" / "windows.py").read_text(encoding="utf-8")
    assert_true("window_from_point" in windows_observer_source and "hide_foreground_companion_window" in windows_observer_source, "Screen observe should hide Joi and capture the underlying content window")
    server_source = (workspace / "agent_companion" / "core" / "server.py").read_text(encoding="utf-8")
    assert_true('"event_created_at": event.created_at' in server_source, "Core voice audio payload should include event timestamp")
    assert_true('"voice_audio_data_url"' in server_source and "data:audio/wav;base64" in server_source, "Core should send voice audio data URLs so Tauri file asset playback is not required")
    assert_true("winsound.PlaySound" in server_source and "SND_ASYNC" in server_source, "Core should provide Windows local voice playback fallback")
    assert_true("watch.loop.start" in server_source and "watch_loop_start_command" in server_source and "watch_loop_configure_command" in server_source and "watch_loop_refresh_command" in server_source and "watch_loop" in server_source, "Core should expose realtime watch loop RPC and ready state")
    assert_true("background_status_command" in server_source and "background_configure_command" in server_source and "background_clear_command" in server_source and '"background.configure"' in server_source, "Core should expose constrained background context controls")
    assert_true("_watch_loop_should_summarize" in server_source and "skip_summary=not run_vision_summary" in server_source, "Core watch loop should run low-frequency visual summaries")
    assert_true("force_visual_summary" in server_source and '"watch.loop.refresh"' in server_source, "Core watch loop should expose forced visual refresh")
    assert_true("memory_status_command" in server_source and "memory_recall_command" in server_source and "memory_browse_vault_command" in server_source and "memory_set_enabled_command" in server_source and "memory_clear_command" in server_source and '"memory.status"' in server_source and '"memory.recall"' in server_source and '"memory.browse_vault"' in server_source and '"memory.save_candidate"' in server_source and '"memory.clear"' in server_source, "Core should expose P5 memory RPC methods")
    skill_manifest_source = (workspace / "agent_companion" / "core" / "skill_manifest.py").read_text(encoding="utf-8")
    assert_true("SKILL_MANIFEST_VERSION" in skill_manifest_source and "build_native_skill_manifest" in skill_manifest_source and "skill_boundary_for_tool" in skill_manifest_source and "KNOWN_SKILL_IDS" in skill_manifest_source and "_apply_skill_setting" in skill_manifest_source and "normalize_skill_id" in skill_manifest_source and "joi.computer_use" in skill_manifest_source and "joi.voice_input" in skill_manifest_source, "Core should define P8 native skill manifests and execution boundaries")
    assert_true("_computer_use_action_schema" in skill_manifest_source and "llm_driven_action_schema" in skill_manifest_source and "requires_approval_for" in skill_manifest_source, "Computer Use skill should expose a declarative LLM action schema instead of app-specific routes only")
    assert_true('"background.configure"' in skill_manifest_source and '"background.clear"' in skill_manifest_source, "Watch native skill should advertise background context controls")
    assert_true("skill_manifest_command" in server_source and '"skills.list"' in server_source and '"skills"' in server_source and "skill_settings_payload" in server_source and "audit_recent_command" in server_source and '"audit.recent"' in server_source, "Core should expose P8 native skill manifest and P9 audit RPCs")
    audit_store_source = (workspace / "agent_companion" / "core" / "audit_store.py").read_text(encoding="utf-8")
    assert_true("AUDIT_SCHEMA_VERSION" in audit_store_source and "AuditStore" in audit_store_source and "record_event" in audit_store_source and "audit_record_from_event" in audit_store_source and "safe_for_display" in audit_store_source, "Core should persist sanitized P9 audit records")
    background_context_source = (workspace / "agent_companion" / "core" / "background_context.py").read_text(encoding="utf-8")
    assert_true("BACKGROUND_CONTEXT_VERSION" in background_context_source and "BackgroundContextStore" in background_context_source and "record_summary" in background_context_source and "video_recording" in background_context_source and "summaries_only" in background_context_source, "Core should keep constrained background context as approved summaries only")
    runtime_config_writer_source = (workspace / "agent_companion" / "core" / "runtime_config_writer.py").read_text(encoding="utf-8")
    policy_source = (workspace / "agent_companion" / "core" / "policy.py").read_text(encoding="utf-8")
    assert_true("_prepare_skill_update" in runtime_config_writer_source and "unknown_skill" in runtime_config_writer_source and "protected_skill" in runtime_config_writer_source and "joi.local_files" not in runtime_config_writer_source, "Runtime config writer should support dynamic native skill toggles without hardcoding path-sensitive ids")
    assert_true("disabled_skills" in policy_source and "skill_id_for_tool" in policy_source and "skill_disabled" in policy_source, "Policy gate should fail closed for disabled native skills")
    assert_true("WatchCommentaryPlanner" in server_source and '"watch_commentary"' in server_source and '"event_tool"' in server_source, "Core should emit proactive watch comments and tag voice payloads")
    watch_source = (workspace / "agent_companion" / "core" / "watch.py").read_text(encoding="utf-8")
    watch_transcript_source = (workspace / "agent_companion" / "core" / "watch_transcript.py").read_text(encoding="utf-8")
    assert_true("recent_with_transcript" in watch_source and "transcript_state" in watch_source and "transcript_memory" in watch_source, "Watch session should maintain rolling transcript memory")
    assert_true("system_audio_diagnostics" in watch_transcript_source and '"diagnostics"' in watch_transcript_source and "audio_bytes" in watch_transcript_source, "Watch transcript should expose safe system-audio diagnostics")
    screen_observe_source = (workspace / "agent_companion" / "core" / "tools" / "screen_observe.py").read_text(encoding="utf-8")
    assert_true('source in {"auto", "system_audio", "audio"}' in screen_observe_source and "audio_result.error" in screen_observe_source, "Auto transcript source should try system audio and preserve fallback reason")
    commentary_source = (workspace / "agent_companion" / "core" / "watch_commentary.py").read_text(encoding="utf-8")
    assert_true("min_interval_seconds" in commentary_source and "maybe_comment" in commentary_source and "safe_voice_line" in commentary_source, "Watch commentary planner should enforce cooldown and safe voice output")
    tool_compression_source = (workspace / "agent_companion" / "core" / "tool_compression.py").read_text(encoding="utf-8")
    assert_true("compress_tool_result" in tool_compression_source and "build_event_agent_state" in tool_compression_source and "planner_state" in tool_compression_source and "_explicit_memory_candidate" in tool_compression_source, "P6 JoiJuice should expose safe tool-result channels without auto memory")
    memory_source = (workspace / "agent_companion" / "core" / "memory.py").read_text(encoding="utf-8")
    assert_true("memory_candidates" in memory_source and "memory_settings" in memory_source and "memories_fts" in memory_source and "recall" in memory_source and "browse_vault" in memory_source and "context" in memory_source and "_manual_vault_notes" in memory_source and "joi_memory_vault.md" in memory_source and "_rejection_reason" in memory_source, "P5 memory core should use pending candidates, disable switch, semantic recall, local vault browsing/context, and privacy gate")
    chat_source = (workspace / "agent_companion" / "core" / "tools" / "chat.py").read_text(encoding="utf-8")
    assert_true("memory_context" in chat_source and "_memory_prompt" in chat_source and "_fallback_memory_reply" in chat_source, "Chat should consume approved memory context")
    config_source = (workspace / "agent_companion" / "core" / "config.py").read_text(encoding="utf-8")
    runtime_status_source = (workspace / "agent_companion" / "core" / "runtime_status.py").read_text(encoding="utf-8")
    watch_tool_source = (workspace / "agent_companion" / "core" / "tools" / "watch.py").read_text(encoding="utf-8")
    assert_true("MODEL_ROUTES" in config_source and "ModelRouteConfig" in config_source and "fallback_reason" in config_source and "to_agent_state" in config_source, "P7 model router should expose stable routes and safe model usage metadata")
    assert_true("SkillSettingConfig" in config_source and "_parse_skill_settings" in config_source and "skill_enabled" in config_source, "Config should parse safe native skill enabled flags")
    assert_true("ModelRouter.stable_routes()" in runtime_status_source and "MODEL_ROUTE_LABELS" in runtime_status_source, "Runtime status should render stable model route rows")
    assert_true("model_usage" in chat_source and "model_usage" in watch_tool_source, "Chat and watch tools should attach safe model usage metadata")
    tts_bridge_source = (workspace / "agent_companion" / "core" / "tts_bridge.py").read_text(encoding="utf-8")
    assert_true("status_payload" in tts_bridge_source and "_safe_tts_error" in tts_bridge_source, "TTS bridge should expose sanitized status")
    assert_true("emotion" in tts_bridge_source and "sprite_id" in tts_bridge_source, "TTS bridge should accept expression sync inputs")
    shell_source = (workspace / "agent_companion" / "shell" / "src" / "App.vue").read_text(encoding="utf-8")
    shell_style_source = (workspace / "agent_companion" / "shell" / "src" / "styles.css").read_text(encoding="utf-8")
    assert_true("activeExpressionEmotion" in shell_source and "expression_sync" in shell_source and "emotion-${activeExpressionEmotion}" in shell_source, "Shell should bind expression sync to character emotion class")
    assert_true("emotion-status-card" in shell_source and "当前情绪" in shell_source, "Chat cabin should expose a compact emotion status module")
    assert_true("stage-emotion-pill" in shell_source and "情绪 {{ activeEmotionStatus.label }}" in shell_source, "Stage should surface current emotion outside the chat cabin")
    assert_true("accessoryFitStyle" in shell_source and "--acc-hat-top" in shell_source and ":style=\"accessoryFitStyle\"" in shell_source, "Accessory overlays should use adaptive anchor variables")
    assert_true("preventNativeAssetDrag" in shell_source and "@dragstart.capture.prevent" in shell_source, "Compact mascot should block native asset dragging")
    assert_true("miniBubbleHasActions" in shell_source and "mini-approval-actions" in shell_source and "requestMiniChange" in shell_source, "Compact speech bubble should expose approval and change actions")
    assert_true("watchLoopStatus" in shell_source and "watch-session-strip" in shell_source and "stopWatchLoop" in shell_source, "Shell should show and control realtime watch loop state")
    assert_true("rolling_transcript" in shell_source and "transcript_window_seconds" in shell_source and "active_transcript_source" in shell_source and "last_visual_summary" in shell_source, "Shell should display rolling transcript and visual state")
    assert_true("shouldSuppressProactiveVoice" in shell_source and "watch_commentary" in shell_source, "Shell should suppress proactive watch voice while the user is typing")
    assert_true("watchTranscriptSource" in shell_source and "configureWatchLoop" in shell_source and "watchProactiveEnabled" in shell_source, "Shell should expose realtime watch controls")
    assert_true("watchVisionInterval" in shell_source and "refreshWatchVision" in shell_source and "vision_interval_ticks" in shell_source, "Shell should expose visual summary cadence and manual refresh controls")
    assert_true("memoryStatus" in shell_source and "memoryEnabled" in shell_source and "saveMemoryCandidate" in shell_source and "clearMemory" in shell_source and "memory-authorize-bubble" in shell_source and "记忆舱" in shell_source, "Shell should expose P5 memory candidate controls and stage authorization bubble")
    assert_true("backgroundStatus" in shell_source and "background-context-panel" in shell_source and "configureBackgroundScope" in shell_source and "clearBackgroundContext" in shell_source and "syncBackgroundFromEvent" in shell_source, "Shell developer panel should expose constrained background context inspection and controls")
    assert_true("settingsTabs" in shell_source and "settings-tabbar" in shell_source and "activeSettingsTab" in shell_source, "Shell should carry Mac-style settings navigation without Mac-only RPC assumptions")
    assert_true("skill-manifest-section" in shell_source and "nativeSkills" in shell_source and "refreshSkills" in shell_source and "skillName" in shell_source and "setSkillEnabled" in shell_source and "skillEnabled" in shell_source and "skillToggleDisabled" in shell_source, "Shell should expose P8 native skill manifest status and event skill ids")
    assert_true("memory-section" in shell_source and "memorySearchResults" in shell_source and "browseMemoryVault" in shell_source and "memory-vault-panel" in shell_source, "Shell should expose a dedicated memory cabin with recall search and vault preview")
    app_source = (workspace / "agent_companion" / "core" / "app.py").read_text(encoding="utf-8")
    assert_true("_step_with_memory_context" in app_source and "build_event_agent_state" in app_source, "App should inject approved memory context and emit safe JoiJuice event channels")
    desktop_context_source = (workspace / "agent_companion" / "core" / "desktop_context.py").read_text(encoding="utf-8")
    assert_true("rewrite_plan_for_desktop_context" in desktop_context_source and "record_desktop_context" in desktop_context_source and "DesktopContext" in desktop_context_source, "Desktop context planning should live outside the app orchestrator")
    assert_true("annotate_agent_state_with_skill" in app_source and "skill_steps" in app_source and "source_skill" in app_source and "reload_runtime_policy" in app_source and "skill_settings_payload" in app_source and "block_reason" in app_source, "App execution boundary should attach native skill metadata and enforce disabled skills")
    assert_true("--acc-hat-top" in shell_style_source and "mini-speech-bubble.actionable" in shell_style_source, "Shell styles should include adaptive accessory anchors and actionable compact bubbles")
    assert_true("settings-tabbar" in shell_style_source and "memory-command-panel" in shell_style_source and "memory-vault-sections" in shell_style_source, "Shell styles should include Mac-inspired settings tabs and memory cabin surfaces")
    assert_true("skill-grid" in shell_style_source and "skill-card" in shell_style_source and "skill-actions" in shell_style_source, "Shell styles should include native skill manifest cards")
    assert_true("watch-session-strip" in shell_style_source and "watch-session-dot" in shell_style_source and "watch-session-controls" in shell_style_source, "Shell styles should include realtime watch loop status strip")
    assert_true("background-status-grid" in shell_style_source and "background-scope-form" in shell_style_source and "background-row" in shell_style_source, "Shell styles should include background context settings and summary rows")
    doctor_source = (workspace / "tools" / "joi_doctor.py").read_text(encoding="utf-8")
    demo_check_source = (workspace / "tools" / "mvp_demo_check.py").read_text(encoding="utf-8")
    setup_wizard_source = (workspace / "tools" / "windows_setup_wizard.py").read_text(encoding="utf-8")
    release_packager_source = (workspace / "tools" / "package_windows_release.py").read_text(encoding="utf-8")
    packaging_smoke_source = (workspace / "tools" / "packaging_smoke.py").read_text(encoding="utf-8")
    provider_preflight_source = (workspace / "tools" / "provider_preflight.py").read_text(encoding="utf-8")
    handoff_report_source = (workspace / "tools" / "windows_handoff_report.py").read_text(encoding="utf-8")
    release_check_source = (workspace / "tools" / "windows_release_check.py").read_text(encoding="utf-8")
    ci_workflow_source = (workspace / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release_candidate_workflow_source = (workspace / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")
    first_run_doc_source = (workspace / "docs" / "WINDOWS_FIRST_RUN.md").read_text(encoding="utf-8")
    start_joi_source = (workspace / "tools" / "start_joi.ps1").read_text(encoding="utf-8")
    assert_true("build_doctor_report" in doctor_source and "safe_for_display" in doctor_source and "next_actions" in doctor_source, "P10 doctor should expose a safe first-run readiness report")
    assert_true("build_mvp_demo_check_report" in demo_check_source and "watch_together" in demo_check_source and "coding_task" in demo_check_source and "game_skill" in demo_check_source and "privacy_boundary" in demo_check_source, "P10 MVP demo check should expose safe watch/coding/game demo scripts")
    assert_true("build_windows_setup_plan" in setup_wizard_source and "windows_setup_exit_code" in setup_wizard_source and "config.example.yaml" in setup_wizard_source and "config.yaml" in setup_wizard_source and "safe_for_display" in setup_wizard_source, "P10 setup wizard should create local config safely without secrets")
    assert_true("build_windows_release_package" in release_packager_source and "build_release_privacy_report" in release_packager_source and "LOCAL_ONLY_SAMPLE_PATHS" in release_packager_source and "FORBIDDEN_NAMES" in release_packager_source and "RELEASE_MANIFEST.json" in release_packager_source and "tools/mvp_demo_check.py" in release_packager_source and "tools/provider_preflight.py" in release_packager_source and "tools/windows_handoff_report.py" in release_packager_source and "tools/windows_release_check.py" in release_packager_source and "tools/windows_setup_wizard.py" in release_packager_source, "P10 release packager should create a safe portable Windows zip and include release/handoff tooling")
    assert_true("build_packaging_smoke_report" in packaging_smoke_source and "version_alignment" in packaging_smoke_source and "window_permissions" in packaging_smoke_source and "release_privacy_policy" in packaging_smoke_source and "mvp_demo_check" in packaging_smoke_source and "provider_preflight" in packaging_smoke_source and "windows_handoff_report" in packaging_smoke_source and "windows_release_check" in packaging_smoke_source and "windows_setup_wizard" in packaging_smoke_source and "setup_launcher" in packaging_smoke_source, "P10 packaging smoke should validate release metadata, Tauri permissions, release privacy policy, MVP demo check, provider preflight, handoff report, setup wizard, and release readiness tooling")
    assert_true("build_provider_preflight_report" in provider_preflight_source and "build_runtime_status" in provider_preflight_source and "REQUIRED_DEMO_PROVIDERS" in provider_preflight_source and "probe_system_audio_readiness" in provider_preflight_source and "safe_for_display" in provider_preflight_source, "P10 provider preflight should expose sanitized offline provider and system-audio readiness")
    assert_true("build_windows_handoff_report" in handoff_report_source and "build_windows_release_check_report" in handoff_report_source and "safe_for_display" in handoff_report_source and "handoff_ready" in handoff_report_source and "start_joi.bat -Setup" in handoff_report_source, "P10 handoff report should expose safe cross-machine release readiness")
    assert_true("build_windows_release_check_report" in release_check_source and "build_doctor_report" in release_check_source and "build_mvp_demo_check_report" in release_check_source and "build_provider_preflight_report" in release_check_source and "build_packaging_smoke_report" in release_check_source and "build_windows_release_package" in release_check_source and "build_windows_setup_plan" in release_check_source and "release_ready" in release_check_source, "P10 release check should aggregate doctor, setup, demo, provider, smoke, privacy, and package dry-run status")
    assert_true("run_agent_companion_tests.py" in ci_workflow_source and "PYTHONUTF8" in ci_workflow_source and "python -m pip install -r requirements.txt" in ci_workflow_source and "npm run build" in ci_workflow_source and "build --debug --no-bundle" in ci_workflow_source and "tools/packaging_smoke.py" in ci_workflow_source and "tools/mvp_demo_check.py" in ci_workflow_source and "tools/provider_preflight.py" in ci_workflow_source and "tools/package_windows_release.py --dry-run" in ci_workflow_source and "tools/windows_handoff_report.py" in ci_workflow_source and "tools/windows_release_check.py" in ci_workflow_source and "tools/windows_setup_wizard.py" in ci_workflow_source, "CI should force UTF-8 output, install Python dependencies, and cover Python tests, frontend build, packaging smoke, MVP demo check, provider preflight, release dry-run, release readiness, handoff report, setup wizard, and Tauri debug smoke build")
    assert_true("workflow_dispatch" in release_candidate_workflow_source and "PYTHONUTF8" in release_candidate_workflow_source and "npm run tauri -- build" in release_candidate_workflow_source and "tools/package_windows_release.py --output-dir dist" in release_candidate_workflow_source and "actions/upload-artifact" in release_candidate_workflow_source, "Release candidate workflow should force UTF-8 output, build a real Tauri release, package without allow-missing-exe, and upload the zip")
    assert_true("-Doctor" in start_joi_source and "joi_doctor.py" in start_joi_source and "-Setup" in start_joi_source and "windows_setup_wizard.py" in start_joi_source, "Windows launcher should expose doctor and setup modes")
    assert_true("start_joi.bat -Doctor" in first_run_doc_source and "start_joi.bat -Setup" in first_run_doc_source and "windows_setup_wizard.py" in first_run_doc_source and "windows_handoff_report.py" in first_run_doc_source and "Tesseract" in first_run_doc_source and "requirements-audio.txt" in first_run_doc_source and "package_windows_release.py" in first_run_doc_source, "Windows first-run docs should cover setup wizard, doctor, OCR, audio, handoff, and release packaging setup")

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
