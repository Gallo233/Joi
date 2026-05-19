from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
from typing import Protocol

from agent_companion.core.config import AsrConfig, load_app_config


DEFAULT_ASR_MAX_BYTES = 12 * 1024 * 1024
DEFAULT_ASR_MAX_SECONDS = 30
DEFAULT_ASR_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class AsrResult:
    transcript: str
    confidence: float = 0.0
    provider: str = "mock"
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.transcript.strip()) and not self.error


class SpeechInputProvider(Protocol):
    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        ...


@dataclass(frozen=True)
class AsrRuntimeState:
    enabled: bool
    configured: bool
    provider: str
    max_seconds: int = DEFAULT_ASR_MAX_SECONDS
    max_bytes: int = DEFAULT_ASR_MAX_BYTES
    timeout_seconds: int = DEFAULT_ASR_TIMEOUT_SECONDS
    error: str = ""


class UnavailableAsrProvider:
    def __init__(self, reason: str = "asr_unconfigured") -> None:
        self.reason = reason

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        return AsrResult("", 0.0, "unavailable", self.reason)


class MockAsrProvider:
    def __init__(self, transcript: str | None = None) -> None:
        self.transcript = transcript

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        transcript = (self.transcript if self.transcript is not None else os.environ.get("AGENT_COMPANION_MOCK_ASR_TEXT", "你好")).strip()
        if not transcript:
            return AsrResult("", 0.0, "mock", "empty_transcript")
        return AsrResult(transcript, 1.0, "mock")


class OpenAICompatibleAsrProvider:
    def __init__(self, config: AsrConfig) -> None:
        self.config = config
        self._client = None

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        if not audio:
            return AsrResult("", 0.0, "openai_compatible", "empty_audio")
        try:
            from openai import OpenAI
        except Exception:
            return AsrResult("", 0.0, "openai_compatible", "openai_package_missing")
        try:
            if self._client is None:
                self._client = OpenAI(
                    api_key=self.config.api_key,
                    base_url=self.config.base_url,
                    timeout=float(max(1, self.config.timeout_seconds)),
                )
            buffer = io.BytesIO(audio)
            buffer.name = _audio_filename(mime_type)
            kwargs = {"model": self.config.model, "file": buffer}
            if self.config.language:
                kwargs["language"] = self.config.language
            transcription = self._client.audio.transcriptions.create(**kwargs)
            transcript = str(getattr(transcription, "text", "") or "").strip()
            if not transcript:
                return AsrResult("", 0.0, "openai_compatible", "empty_transcript")
            return AsrResult(transcript, 0.0, "openai_compatible")
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                return AsrResult("", 0.0, "openai_compatible", "asr_timeout")
            return AsrResult("", 0.0, "openai_compatible", "asr_failed")


def build_asr_provider(workspace: Path, *, allow_mock: bool = False) -> tuple[SpeechInputProvider, AsrRuntimeState]:
    config_path = workspace / "config.yaml"
    if not config_path.is_file():
        return UnavailableAsrProvider(), AsrRuntimeState(False, False, "none", error="asr_unconfigured")
    try:
        config = load_app_config(config_path)
    except Exception:
        return UnavailableAsrProvider("asr_config_error"), AsrRuntimeState(False, False, "none", error="asr_config_error")

    asr = config.asr
    state = AsrRuntimeState(
        enabled=asr.enabled,
        configured=asr.is_configured,
        provider=asr.provider or "none",
        max_seconds=asr.max_seconds,
        max_bytes=asr.max_bytes,
        timeout_seconds=asr.timeout_seconds,
        error="" if asr.is_configured else "asr_unconfigured",
    )
    if not asr.enabled:
        return UnavailableAsrProvider("asr_disabled"), state
    provider = asr.provider.strip().casefold()
    if provider == "mock":
        if allow_mock or os.environ.get("AGENT_COMPANION_ALLOW_MOCK_ASR") == "1":
            return MockAsrProvider(), state
        return UnavailableAsrProvider("mock_asr_developer_only"), AsrRuntimeState(
            asr.enabled,
            False,
            "mock",
            asr.max_seconds,
            asr.max_bytes,
            asr.timeout_seconds,
            "mock_asr_developer_only",
        )
    if provider in {"openai_compatible", "openai"} and asr.is_configured:
        return OpenAICompatibleAsrProvider(asr), state
    return UnavailableAsrProvider("asr_unconfigured"), state


def _audio_filename(mime_type: str) -> str:
    mime = (mime_type or "").split(";", 1)[0].strip().casefold()
    suffix = {
        "audio/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/ogg": "ogg",
    }.get(mime, "webm")
    return f"joi-voice-input.{suffix}"
