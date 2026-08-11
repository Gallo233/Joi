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


MIMO_ASR_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_ASR_MODEL = "mimo-v2.5-asr"
# MiMo reads the audio out of a chat turn, and the whole clip travels base64 in
# the request body. 10 MB is the documented ceiling for the encoded form.
MIMO_MAX_ENCODED_BYTES = 10 * 1024 * 1024
# The only two containers it documents. The shell records WAV for exactly this
# reason -- a webm or mp4 blob is refused, and refused after upload.
MIMO_AUDIO_TYPES = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}


class MimoAsrProvider:
    """Transcription through MiMo, which shapes it as a chat completion.

    The audio is a content part inside a user turn rather than an uploaded
    file, so this cannot reuse the OpenAI-compatible transcription client even
    though both speak to an OpenAI-shaped endpoint.
    """

    def __init__(self, config: AsrConfig) -> None:
        self.config = config

    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        import base64
        import json
        import urllib.error
        import urllib.request

        if not audio:
            return AsrResult("", 0.0, "mimo", "empty_audio")
        container = MIMO_AUDIO_TYPES.get((mime_type or "").split(";", 1)[0].strip().casefold())
        if not container:
            # Named distinctly from a generic failure: the recording is fine,
            # it is simply in a container this service will not read, and the
            # fix is in how it was captured rather than in the network.
            return AsrResult("", 0.0, "mimo", "audio_format_unsupported")
        encoded = base64.b64encode(audio).decode("ascii")
        if len(encoded) > MIMO_MAX_ENCODED_BYTES:
            return AsrResult("", 0.0, "mimo", "audio_too_large")

        language = _mimo_language(self.config.language)
        payload = {
            "model": (self.config.model or MIMO_ASR_MODEL).strip(),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:{'audio/wav' if container == 'wav' else 'audio/mpeg'};base64,{encoded}"},
                        }
                    ],
                }
            ],
            "asr_options": {"language": language},
            "stream": False,
        }
        base_url = (self.config.base_url or MIMO_ASR_BASE_URL).strip().rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"api-key": self.config.api_key.strip(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1, self.config.timeout_seconds)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return AsrResult("", 0.0, "mimo", _mimo_http_error(exc.code))
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                return AsrResult("", 0.0, "mimo", "asr_timeout")
            return AsrResult("", 0.0, "mimo", "asr_failed")
        try:
            transcript = str(body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            # A 200 carrying no transcript is a real outcome -- silence, or a
            # refusal -- and must not surface as a crash.
            return AsrResult("", 0.0, "mimo", "empty_transcript")
        if not transcript:
            return AsrResult("", 0.0, "mimo", "empty_transcript")
        return AsrResult(transcript, 0.0, "mimo")


def _mimo_language(configured: str) -> str:
    """The nearest language this recogniser will accept.

    It takes only `zh`, `en` and `auto`, and answers 400 to anything else --
    so a character configured for Japanese would fail every request rather
    than transcribe badly. `auto` is the honest fallback: it is what the
    service offers for everything it does not name.

    It is not a substitute for support. Japanese audio comes back through
    `auto` as approximate Chinese, so voice input for a Japanese-speaking
    character is not usable on this provider -- that is a property of MiMo,
    not something this mapping can fix.
    """

    text = (configured or "").strip().casefold().replace("_", "-").split("-")[0]
    if text in {"zh", "en"}:
        return text
    return "auto"


def _mimo_http_error(status: int) -> str:
    if status in (401, 403):
        return "asr_auth_failed"
    # The account, not the request: worth its own code because topping up is
    # the only fix and no amount of retrying helps.
    if status == 402:
        return "asr_insufficient_balance"
    if status == 429:
        return "asr_rate_limited"
    return "asr_failed"


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
    if provider in {"mimo", "xiaomi_mimo"} and asr.is_configured:
        return MimoAsrProvider(asr), state
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
