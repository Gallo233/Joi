from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol


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


class MockAsrProvider:
    def __init__(self, transcript: str | None = None) -> None:
        self.transcript = transcript

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        transcript = (self.transcript if self.transcript is not None else os.environ.get("AGENT_COMPANION_MOCK_ASR_TEXT", "你好")).strip()
        if not transcript:
            return AsrResult("", 0.0, "mock", "empty_transcript")
        return AsrResult(transcript, 1.0, "mock")
