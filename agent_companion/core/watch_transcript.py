from __future__ import annotations

from dataclasses import dataclass, field
import io
from pathlib import Path
import sys
import wave
from typing import Any, Protocol

from agent_companion.core.speech_input import AsrResult, SpeechInputProvider


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_ms: int = 0
    end_ms: int = 0
    source: str = "ocr_subtitle"
    confidence: float = 0.0
    frame_index: int = 0
    region: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "text": self.text[:240],
            "start_ms": max(0, int(self.start_ms)),
            "end_ms": max(0, int(self.end_ms)),
            "source": _safe_source(self.source),
            "confidence": round(max(0.0, min(1.0, float(self.confidence or 0.0))), 3),
            "frame_index": max(0, int(self.frame_index or 0)),
            "region": _safe_source(self.region),
        }


@dataclass(frozen=True)
class TranscriptResult:
    status: str
    source: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    summary: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "success" and bool(self.segments)

    def compact_text(self, limit: int = 6) -> str:
        rows: list[str] = []
        seen: set[str] = set()
        for segment in self.segments:
            text = _clean_text(segment.text)
            key = _normalize_text(text)
            if not text or key in seen:
                continue
            seen.add(key)
            rows.append(text[:120])
            if len(rows) >= limit:
                break
        return " / ".join(rows)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "status": _safe_status(self.status),
            "source": _safe_source(self.source),
            "summary": self.summary[:300],
            "error": _safe_error(self.error),
            "segments": [segment.to_agent_state() for segment in self.segments[:12]],
        }


class AudioTranscriptProvider(Protocol):
    def transcribe(self, seconds: float = 5.0) -> TranscriptResult:
        ...


class UnavailableAudioTranscriptProvider:
    def __init__(self, reason: str = "system_audio_unavailable") -> None:
        self.reason = reason

    def transcribe(self, seconds: float = 5.0) -> TranscriptResult:
        return TranscriptResult("unavailable", "system_audio", [], "系统音频转写不可用。", self.reason)


class SystemAudioTranscriptProvider:
    def __init__(self, asr: SpeechInputProvider, *, max_seconds: int = 8) -> None:
        self.asr = asr
        self.max_seconds = max(1, int(max_seconds or 8))

    def transcribe(self, seconds: float = 5.0) -> TranscriptResult:
        duration = max(1.0, min(float(seconds or 5.0), float(self.max_seconds)))
        audio, error = capture_system_audio_wav(duration)
        if error:
            return TranscriptResult("unavailable", "system_audio", [], "系统音频没有捕获到。", error)
        result = self.asr.transcribe(audio, "audio/wav")
        return transcript_from_asr_result(result, source="system_audio", duration_ms=int(duration * 1000))


def transcript_from_asr_result(result: AsrResult, *, source: str = "system_audio", duration_ms: int = 0) -> TranscriptResult:
    if not result.ok:
        return TranscriptResult("failed", source, [], "音频转写没有生成文本。", result.error or "empty_transcript")
    text = _clean_text(result.transcript)
    if not text:
        return TranscriptResult("failed", source, [], "音频转写没有生成文本。", "empty_transcript")
    segment = TranscriptSegment(text, 0, max(0, int(duration_ms or 0)), source, result.confidence, 1, "audio")
    return TranscriptResult("success", source, [segment], f"识别到 1 段系统音频转写。")


def transcript_from_ocr_records(records: list[dict[str, Any]], sample_interval_ms: int = 900) -> TranscriptResult:
    merged: dict[str, TranscriptSegment] = {}
    order: list[str] = []
    interval = max(0, int(sample_interval_ms or 0))
    for frame_index, record in enumerate(records, start=1):
        observation = record.get("observation")
        width = int(getattr(observation, "width", 0) or 0)
        height = int(getattr(observation, "height", 0) or 0)
        ocr = record.get("ocr_result")
        for block in getattr(ocr, "text_blocks", []) or []:
            text = _clean_text(str(getattr(block, "text", "") or ""))
            bbox = tuple(getattr(block, "bbox", ()) or ())
            if not _looks_like_caption(text, bbox, width, height):
                continue
            key = _normalize_text(text)
            start_ms = max(0, (frame_index - 1) * interval)
            end_ms = max(start_ms, frame_index * interval)
            confidence = float(getattr(block, "confidence", 0.0) or 0.0)
            region = _caption_region(bbox, width, height)
            if key in merged:
                existing = merged[key]
                merged[key] = TranscriptSegment(
                    existing.text,
                    existing.start_ms,
                    max(existing.end_ms, end_ms),
                    existing.source,
                    max(existing.confidence, confidence),
                    existing.frame_index,
                    existing.region or region,
                )
                continue
            merged[key] = TranscriptSegment(text, start_ms, end_ms, "ocr_subtitle", confidence, frame_index, region)
            order.append(key)
    segments = [merged[key] for key in order[:20]]
    if not segments:
        return TranscriptResult("empty", "ocr_subtitle", [], "没有识别到稳定字幕。", "")
    return TranscriptResult("success", "ocr_subtitle", segments, f"识别到 {len(segments)} 段可用字幕/画面文字。")


def capture_system_audio_wav(seconds: float = 5.0, sample_rate: int = 48000) -> tuple[bytes, str]:
    if sys.platform != "win32":
        return b"", "system_audio_windows_only"
    try:
        import numpy as np
        import soundcard as sc
    except Exception:
        return b"", "system_audio_dependency_missing"
    try:
        duration = max(1.0, min(float(seconds or 5.0), 30.0))
        microphone = _default_soundcard_loopback(sc)
        if microphone is None:
            return b"", "system_audio_device_missing"
        rate = int(sample_rate or 48000)
        channels = max(1, min(2, int(getattr(microphone, "channels", 2) or 2)))
        frames = int(rate * duration)
        audio = microphone.record(numframes=frames, samplerate=rate, channels=channels)
        if audio is None:
            return b"", "system_audio_capture_failed"
        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 1:
            data = data.reshape((-1, 1))
        data = np.clip(data, -1.0, 1.0)
        pcm = (data * 32767.0).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(int(pcm.shape[1]))
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm.tobytes())
        return buffer.getvalue(), ""
    except Exception:
        return b"", "system_audio_capture_failed"


def _default_soundcard_loopback(sc: Any) -> Any | None:
    try:
        speaker = sc.default_speaker()
        speaker_name = str(getattr(speaker, "name", "") or "")
        for microphone in sc.all_microphones(include_loopback=True):
            if getattr(microphone, "isloopback", False) and str(getattr(microphone, "name", "") or "") == speaker_name:
                return microphone
    except Exception:
        pass
    try:
        microphones = sc.all_microphones(include_loopback=True)
    except Exception:
        return None
    for microphone in microphones:
        try:
            if getattr(microphone, "isloopback", False):
                return microphone
        except Exception:
            continue
    return None


def _looks_like_caption(text: str, bbox: tuple[Any, ...], width: int, height: int) -> bool:
    if not text or len(text) < 2:
        return False
    lowered = text.casefold()
    if lowered.startswith(("http://", "https://", "www.")):
        return False
    if text in _UI_NOISE:
        return False
    if len(text) <= 3 and not any("\u4e00" <= char <= "\u9fff" for char in text):
        return False
    x, y, w, h = _safe_bbox(bbox)
    if width <= 0 or height <= 0:
        return _sentence_like(text)
    center_y = (y + h / 2) / max(1, height)
    center_x = (x + w / 2) / max(1, width)
    coverage = w / max(1, width)
    if center_y >= 0.50 and 0.08 <= center_x <= 0.92:
        return True
    if coverage >= 0.18 and center_y >= 0.34:
        return True
    return _sentence_like(text) and center_y >= 0.08


def _caption_region(bbox: tuple[Any, ...], width: int, height: int) -> str:
    _, y, _, h = _safe_bbox(bbox)
    if height <= 0:
        return "unknown"
    center_y = (y + h / 2) / max(1, height)
    if center_y >= 0.68:
        return "bottom_caption"
    if center_y >= 0.28:
        return "middle_overlay"
    return "top_overlay"


def _safe_bbox(bbox: tuple[Any, ...]) -> tuple[float, float, float, float]:
    if len(bbox) < 4:
        return 0.0, 0.0, 0.0, 0.0
    try:
        return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def _sentence_like(text: str) -> bool:
    if len(text) >= 6 and any("\u4e00" <= char <= "\u9fff" for char in text):
        return True
    return any(mark in text for mark in ("。", "，", "！", "？", ".", ",", "!", "?"))


def _clean_text(text: str) -> str:
    return " ".join((text or "").replace("\u3000", " ").split()).strip()


def _normalize_text(text: str) -> str:
    return "".join(_clean_text(text).casefold().split())


def _safe_status(value: str) -> str:
    return value if value in {"success", "empty", "failed", "unavailable"} else "failed"


def _safe_source(value: str) -> str:
    text = (value or "unknown").strip().casefold()
    return text if text in {"ocr_subtitle", "system_audio", "audio", "bottom_caption", "middle_overlay", "top_overlay", "unknown"} else "unknown"


def _safe_error(value: str) -> str:
    text = (value or "").strip().casefold()
    allowed = {
        "system_audio_unavailable",
        "system_audio_windows_only",
        "system_audio_dependency_missing",
        "system_audio_device_missing",
        "system_audio_capture_failed",
        "empty_transcript",
        "asr_timeout",
        "asr_failed",
        "asr_unconfigured",
        "asr_disabled",
        "mock_asr_developer_only",
        "openai_package_missing",
    }
    return text if text in allowed else "transcript_unavailable" if text else ""


_UI_NOISE = {
    "首页",
    "番剧",
    "直播",
    "游戏中心",
    "会员购",
    "漫画",
    "赛事",
    "下载客户端",
    "投稿",
    "关注",
    "登录",
    "设置",
    "更多",
    "消息",
    "动态",
    "收藏",
    "历史",
    "创作中心",
    "搜索",
    "刷新",
    "关闭",
}
