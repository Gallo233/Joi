from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from agent_companion.core.minecraft_screen import MinecraftScreenCache, sanitized_screen_text
from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.vision.summarizer import VisionSummary


class _FakeObserver:
    def __init__(self, path: Path | None = None, *, fail: bool = False) -> None:
        self.path = path or (Path(tempfile.gettempdir()) / "fake-shot.png")
        self.fail = fail
        self.calls = 0

    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        self.calls += 1
        if self.fail:
            raise RuntimeError("screen permission missing")
        return VisionObservation(
            target="active_window",
            screenshot_path=self.path,
            screenshot_rel="screens/private-shot.png",
            width=1280,
            height=720,
            title="秘密的窗口标题",
        )


class _FakeOcr:
    def __init__(self, text: str = "生命值 20") -> None:
        self.text = text

    def extract(self, image_path: Path) -> OcrResult:
        return OcrResult("success", "界面文字", text_blocks=[OcrTextBlock(self.text)])


class _FakeSummarizer:
    def __init__(self, text: str = "画面是一片橡树林。") -> None:
        self.text = text

    def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
        return VisionSummary(text=self.text)


class MinecraftScreenCacheTests(unittest.TestCase):
    def test_refresh_composes_summary_and_ocr_without_paths_or_titles(self) -> None:
        observer = _FakeObserver()
        cache = MinecraftScreenCache(observer, _FakeOcr(), _FakeSummarizer(), cache_ttl_seconds=5)
        result = cache.refresh()
        self.assertTrue(result["ok"], result)
        self.assertIn("屏幕摘要：画面是一片橡树林。", result["text"])
        self.assertIn("生命值 20", result["text"])
        for forbidden in ("screens/private-shot.png", "fake-shot", "秘密的窗口标题", "1280"):
            self.assertNotIn(forbidden, result["text"])
        self.assertEqual(result["source"], "screen")

    def test_fresh_cache_is_reused_and_force_recaptures(self) -> None:
        observer = _FakeObserver()
        cache = MinecraftScreenCache(observer, _FakeOcr(), _FakeSummarizer(), cache_ttl_seconds=5)
        cache.refresh()
        cache.refresh()
        self.assertEqual(observer.calls, 1)
        cache.refresh(force=True)
        self.assertEqual(observer.calls, 2)
        cache = MinecraftScreenCache(observer, _FakeOcr(), _FakeSummarizer(), cache_ttl_seconds=0.5)
        cache.refresh()
        time.sleep(0.6)
        cache.refresh()
        self.assertEqual(observer.calls, 4)

    def test_unconfigured_vision_falls_back_to_ocr_only(self) -> None:
        cache = MinecraftScreenCache(_FakeObserver(), _FakeOcr(), summarizer=None)
        result = cache.refresh()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["text"].startswith("画面文字："))
        self.assertNotIn("屏幕摘要", result["text"])

    def test_capture_failure_is_a_bounded_failure_result(self) -> None:
        cache = MinecraftScreenCache(_FakeObserver(fail=True), _FakeOcr(), _FakeSummarizer())
        result = cache.refresh()
        self.assertFalse(result["ok"])
        self.assertEqual(result["text"], "")
        self.assertTrue(result["error"].startswith("screen_observation_failed"))
        self.assertFalse(sanitized_screen_text(result))

    def test_text_is_bounded_to_the_configured_limit(self) -> None:
        summarizer = _FakeSummarizer("长" * 5000)
        cache = MinecraftScreenCache(_FakeObserver(), _FakeOcr(), summarizer, max_text=1200)
        result = cache.refresh()
        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["text"]), 1200)


if __name__ == "__main__":
    unittest.main()
