from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterator


CLOUD_PROVIDERS = {"openai_compatible", "cloud"}
MIMO_PROVIDERS = {"mimo", "xiaomi_mimo"}


class TtsBridge:
    """Outbound voice lines, through the configured character voice.

    GPT-SoVITS gives the character its own voice but is a separate service the
    user must install and run. A hosted `/v1/audio/speech` endpoint takes
    direction and needs only a key, which makes it the way to exercise the rest
    of the voice path before that install exists -- at the cost of sending
    every line off the machine.

    There is deliberately no system-voice fallback. A machine voice speaking
    after the selected character voice fails is both an identity break and a
    second, misleading author of the line. Failure therefore keeps the display
    text and reports a sanitized error, but produces no audio.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config: Any | None = None
        self._client: Any | None = None
        self._last_error = ""
        self._last_ttfb_ms: int | None = None
        self._last_total_ms: int | None = None
        self._warmup_thread: threading.Thread | None = None
        self._load()

    @property
    def _switched_on(self) -> bool:
        return bool(
            os.environ.get("AGENT_COMPANION_DISABLE_TTS") != "1"
            and self._config
            and self._config.tts.enabled
        )

    @property
    def enabled(self) -> bool:
        return bool(self._switched_on and (self._provider == "gpt-sovits" or self._cloud_ready))

    @property
    def _cloud_ready(self) -> bool:
        if not self._config:
            return False
        if self._provider in CLOUD_PROVIDERS:
            return bool(getattr(self._config.tts, "is_cloud_configured", False))
        # MiMo defaults its endpoint and model, so a key is the whole setup.
        if self._provider in MIMO_PROVIDERS:
            return bool(self._config.tts.api_key.strip())
        return False

    @property
    def _provider(self) -> str:
        return str(getattr(self._config, "tts", None) and self._config.tts.provider or "").strip().casefold()

    @property
    def supports_streaming(self) -> bool:
        if not self.enabled or self._config is None:
            return False
        if self._provider == "gpt-sovits":
            return True
        if self._provider not in MIMO_PROVIDERS:
            return False
        from agent_companion.core.mimo_tts import PRESET_MODEL

        return str(self._config.tts.model or "").strip() == PRESET_MODEL

    def synthesize(
        self,
        text: str,
        sprite_id: str = "1",
        emotion: str = "neutral",
        delivery: object | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text or not self._switched_on or self._config is None:
            return {}
        started = time.perf_counter()
        if self._provider == "gpt-sovits":
            payload = self._synthesize_sovits(text, sprite_id, emotion)
        elif self._provider in CLOUD_PROVIDERS or self._provider in MIMO_PROVIDERS:
            payload = self._synthesize_cloud(text, emotion, delivery)
        else:
            return {}
        elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
        self._last_total_ms = elapsed_ms
        if payload.get("voice_audio_path"):
            self._last_ttfb_ms = elapsed_ms
            payload["voice_audio_ttfb_ms"] = elapsed_ms
            payload["voice_audio_total_ms"] = elapsed_ms
        return payload

    def synthesize_stream(
        self,
        text: str,
        emotion: str = "neutral",
        delivery: object | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield provider PCM16 chunks immediately, then one final marker."""

        if not self.supports_streaming or self._config is None:
            return
        sequence = 0
        started = time.perf_counter()
        first_audio_ms: int | None = None
        source = "local" if self._provider == "gpt-sovits" else "cloud"
        try:
            if self._client is None:
                if self._provider == "gpt-sovits":
                    from agent_companion.core.gpt_sovits import GptSoVitsClient

                    self._client = GptSoVitsClient(self._config)
                else:
                    from agent_companion.core.mimo_tts import MimoTtsClient

                    self._client = MimoTtsClient(self._config)
            if self._provider == "gpt-sovits":
                chunks = self._client.stream_pcm16(text, self._config.primary_character, emotion=emotion)
            else:
                chunks = (
                    (chunk, 24000)
                    for chunk in self._client.stream_pcm16(
                        text,
                        self._config.primary_character,
                        emotion,
                        delivery,
                    )
                )
            for chunk, sample_rate in chunks:
                if first_audio_ms is None:
                    first_audio_ms = max(0, int((time.perf_counter() - started) * 1000))
                    self._last_ttfb_ms = first_audio_ms
                yield {
                    "voice_audio_pcm16_base64": base64.b64encode(chunk).decode("ascii"),
                    "voice_audio_sample_rate": sample_rate,
                    "voice_audio_sequence": sequence,
                    "voice_audio_source": source,
                    **({"voice_audio_ttfb_ms": first_audio_ms} if sequence == 0 else {}),
                }
                sequence += 1
            self._last_error = ""
            total_ms = max(0, int((time.perf_counter() - started) * 1000))
            self._last_total_ms = total_ms
            yield {
                "voice_audio_final": True,
                "voice_audio_sequence": sequence,
                "voice_audio_source": source,
                "voice_audio_total_ms": total_ms,
                **({"voice_audio_ttfb_ms": first_audio_ms} if first_audio_ms is not None else {}),
            }
        except Exception as exc:
            self._last_error = _safe_tts_error(exc)
            self._last_total_ms = max(0, int((time.perf_counter() - started) * 1000))
            yield {
                "voice_audio_error": self._last_error,
                "voice_audio_final": True,
                "voice_audio_sequence": sequence,
                "voice_audio_total_ms": self._last_total_ms,
            }

    def _synthesize_sovits(self, text: str, sprite_id: str, emotion: str) -> dict[str, str]:
        try:
            if self._client is None:
                from agent_companion.core.gpt_sovits import GptSoVitsClient

                self._client = GptSoVitsClient(self._config)
            output = self._client.synthesize(text, self._config.primary_character, sprite_id, emotion)
            self._last_error = ""
            return self._audio_payload(output.resolve())
        except Exception as exc:
            self._last_error = _safe_tts_error(exc)
            return {"voice_audio_error": self._last_error}

    def _synthesize_cloud(self, text: str, emotion: str, delivery: object | None = None) -> dict[str, str]:
        try:
            if self._client is None:
                if self._provider in MIMO_PROVIDERS:
                    from agent_companion.core.mimo_tts import MimoTtsClient

                    self._client = MimoTtsClient(self._config)
                else:
                    from agent_companion.core.cloud_tts import CloudTtsClient

                    self._client = CloudTtsClient(self._config)
            if self._provider in MIMO_PROVIDERS:
                output = self._client.synthesize(
                    text,
                    self._config.primary_character,
                    _temporary_output_dir(self.workspace),
                    emotion,
                    delivery,
                )
            else:
                output = self._client.synthesize(
                    text,
                    self._config.primary_character,
                    _temporary_output_dir(self.workspace),
                    emotion,
                )
            self._last_error = ""
            return {**self._audio_payload(output.resolve()), "voice_audio_source": "cloud"}
        except Exception as exc:
            self._last_error = _safe_tts_error(exc)
            return {"voice_audio_error": self._last_error}

    def _audio_payload(self, resolved: Path) -> dict[str, str]:
        payload = {"voice_audio_path": str(resolved)}
        try:
            payload["voice_audio_rel"] = resolved.relative_to(self.workspace).as_posix()
        except ValueError:
            payload["voice_audio_rel"] = str(resolved)
        return payload

    def status_payload(self) -> dict[str, Any]:
        provider = ""
        enabled = False
        configured = False
        if self._config is not None:
            provider = str(self._config.tts.provider or "")
            enabled = self._switched_on
            configured = bool(enabled and (provider.strip().casefold() == "gpt-sovits" or self._cloud_ready))
        return {
            "enabled": enabled,
            "configured": configured,
            "provider": provider or "none",
            "model": str(self._config.tts.model or "") if self._config is not None else "",
            "streaming": self.supports_streaming,
            "timeout_seconds": int(self._config.tts.timeout_seconds or 0) if self._config is not None else 0,
            "last_ttfb_ms": self._last_ttfb_ms,
            "last_total_ms": self._last_total_ms,
            # Kept for protocol compatibility with older shells. It is a hard
            # false, not a configurable capability.
            "system_fallback": False,
            "last_error": self._last_error,
        }

    def warmup(self) -> None:
        """Preload a selected local voice without blocking Core startup."""

        if not self.enabled or self._provider != "gpt-sovits" or self._config is None:
            return
        try:
            if self._client is None:
                from agent_companion.core.gpt_sovits import GptSoVitsClient

                self._client = GptSoVitsClient(self._config)
            self._client.warmup(self._config.primary_character)
            self._last_error = ""
        except Exception as exc:
            # The UI keeps the provider visible as unavailable.  It never
            # substitutes a system voice while local weights are missing.
            self._last_error = _safe_tts_error(exc)

    def start_warmup(self) -> None:
        """Start local weight loading without extending app startup/shutdown."""

        if not self.enabled or self._provider != "gpt-sovits":
            return
        if self._warmup_thread is not None and self._warmup_thread.is_alive():
            return
        self._warmup_thread = threading.Thread(target=self.warmup, name="joi-voice-warmup", daemon=True)
        self._warmup_thread.start()

    def shutdown(self) -> None:
        if self._client is not None:
            try:
                self._client.shutdown()
            except Exception:
                pass
            self._client = None

    def stop_local_service(self) -> None:
        """Release the local voice service when Joi itself is closing.

        Separate from `shutdown`, which only drops the client: reloading a
        character or switching provider must not tear down a service the next
        line is about to use. Quitting is the one moment it should go, so the
        weights stop occupying several gigabytes while Joi is not running.
        """

        client = self._client
        self._client = None
        if client is None:
            return
        try:
            stop = getattr(client, "stop_owned_server", None)
            (stop or client.shutdown)()
        except Exception:
            pass

    def voice_language(self) -> str:
        """The language the selected character voice is set to speak.

        Callers that write a line for this voice need it before synthesis, not
        after: text written in another language is read as that language's
        readings of the same characters rather than as words.
        """

        if self._config is None:
            return ""
        try:
            return str(self._config.primary_character.voice_text_lang(self._config.tts.text_lang) or "")
        except (AttributeError, ValueError):
            return ""

    def reload(self) -> None:
        self.shutdown()
        self._config = None
        self._load()

    def _load(self) -> None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return
        try:
            from agent_companion.core.config import load_app_config

            self._config = load_app_config(config_path)
        except Exception:
            self._config = None


def _safe_tts_error(exc: Exception) -> str:
    # Only the class name and message, never the request: a cloud failure
    # carries a URL with the key in the headers, and the settings panel shows
    # whatever comes back from here.
    text = f"{type(exc).__name__} {exc}".casefold()
    status = getattr(exc, "code", None)
    if status in (401, 403):
        return "tts_auth_failed"
    if status == 429:
        return "tts_rate_limited"
    # Checked before the connection cases: "GPT-SoVITS is not installed" would
    # otherwise be reported as a service that is merely down, and the two ask
    # very different things of the user.
    if "not installed" in text or "exited while starting" in text:
        return "tts_not_installed"
    if "timeout" in text or "did not finish starting" in text:
        return "tts_timeout"
    # A service that answers its port but 500s every line is not "unavailable"
    # -- it is running, and reported as running, which is exactly why it went
    # unnoticed for a day. It needs restarting, and the message has to say so.
    if "cannot synthesize" in text or "http 5" in text:
        return "tts_service_unhealthy"
    if "not running" in text or "connection" in text or "refused" in text:
        return "tts_service_unavailable"
    if "gpt_sovits_work_path" in text or "config" in text:
        return "tts_config_error"
    return "tts_failed"


def _temporary_output_dir(workspace: Path) -> Path:
    """Private generated-audio cache, independent of any OS voice backend."""

    root = workspace / "data" / "agent_companion" / "voice"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        return Path(tempfile.gettempdir())
