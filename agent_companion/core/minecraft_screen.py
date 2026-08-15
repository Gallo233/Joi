from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from agent_companion.core.vision import OcrExtractor, VisionObserver, VisionSummarizer
from agent_companion.core.vision.ocr import run_ocr_safely


class MinecraftScreenCache:
    """Bounded, sanitized screen evidence for the Minecraft session.

    The realtime voice turn never performs a synchronous capture: ``refresh``
    reuses a fresh cached summary, and the capture itself runs on whatever
    worker thread called it (goal submission already runs off the voice loop).
    One frame feeds both the vision summary and OCR. The cached projection is
    plain text built only from the vision summary and OCR text - no screenshot
    path, geometry, handle or title detail leaves Core, and nothing is
    persisted.
    """

    def __init__(
        self,
        observer: VisionObserver,
        ocr: OcrExtractor,
        summarizer: VisionSummarizer | None = None,
        *,
        cache_ttl_seconds: float = 5.0,
        max_text: int = 1_200,
    ) -> None:
        self._observer = observer
        self._ocr = ocr
        self._summarizer = summarizer
        self._ttl = max(0.5, float(cache_ttl_seconds))
        self._max_text = max(80, min(int(max_text), 4_000))
        self._lock = threading.RLock()
        self._cached: dict[str, Any] = {"ok": False, "text": "", "source": "screen", "captured_at": 0.0, "error": ""}

    def refresh(self, force: bool = False) -> dict[str, Any]:
        """Return a fresh sanitized screen summary, reusing the cache when recent."""

        if not force:
            with self._lock:
                if self._cached.get("ok") and time.monotonic() - float(self._cached.get("captured_at") or 0) < self._ttl:
                    return dict(self._cached)
        try:
            observation = self._observer.observe("active_window", "")
        except Exception as exc:
            result = self._failure(f"screen_observation_failed:{type(exc).__name__}"[:120])
            return result
        summary_text = self._summarize(observation)
        ocr_text = self._read_ocr(observation)
        if summary_text and ocr_text and ocr_text not in summary_text:
            text = f"屏幕摘要：{summary_text}\n画面文字：{ocr_text}"
        elif summary_text:
            text = f"屏幕摘要：{summary_text}"
        elif ocr_text:
            text = f"画面文字：{ocr_text}"
        else:
            text = ""
        if not text:
            return self._failure("screen_observation_empty")
        result = {
            "ok": True,
            "text": _bounded_plain(text, self._max_text),
            "source": "screen",
            "captured_at": time.monotonic(),
            "error": "",
        }
        with self._lock:
            self._cached = result
        return dict(result)

    def cached(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._cached)

    def _failure(self, error: str) -> dict[str, Any]:
        result = {"ok": False, "text": "", "source": "screen", "captured_at": time.monotonic(), "error": error[:120]}
        with self._lock:
            self._cached = result
        return dict(result)

    def _summarize(self, observation: Any) -> str:
        """Vision summary text only; the observation itself never crosses."""

        if self._summarizer is None:
            return ""
        try:
            summary = self._summarizer.summarize(observation, "")
            return _bounded_plain(summary.text, self._max_text)
        except Exception:
            return ""

    def _read_ocr(self, observation: Any) -> str:
        """OCR text of the same frame; paths and geometry never cross."""

        try:
            result = run_ocr_safely(self._ocr, observation.screenshot_path)
        except Exception:
            return ""
        return _bounded_plain(result.detail_text(), self._max_text)


def _bounded_plain(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text.strip()[:limit]


def sanitized_screen_text(result: Mapping[str, Any], limit: int = 1_200) -> str:
    """The one field that may cross the Core boundary into provider context."""

    return _bounded_plain(result.get("text"), limit)
