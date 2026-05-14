from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class TtsBridge:
    """Best-effort GPT-SoVITS bridge for outbound voice lines.

    It never falls back to system TTS. If synthesis fails, callers keep showing
    text and simply skip audio playback.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config: Any | None = None
        self._client: Any | None = None
        self._load()

    @property
    def enabled(self) -> bool:
        return bool(
            os.environ.get("AGENT_COMPANION_DISABLE_TTS") != "1"
            and self._config
            and self._config.tts.enabled
            and (self._config.tts.provider or "").lower() == "gpt-sovits"
        )

    def synthesize(self, text: str, sprite_id: str = "1") -> dict[str, str]:
        text = (text or "").strip()
        if not text or not self.enabled or self._config is None:
            return {}
        try:
            if self._client is None:
                from agent_companion.core.gpt_sovits import GptSoVitsClient

                self._client = GptSoVitsClient(self._config)
            output = self._client.synthesize(text, self._config.primary_character, sprite_id)
            resolved = output.resolve()
            payload = {"voice_audio_path": str(resolved)}
            try:
                payload["voice_audio_rel"] = resolved.relative_to(self.workspace).as_posix()
            except ValueError:
                payload["voice_audio_rel"] = str(resolved)
            return payload
        except Exception as exc:
            return {"voice_audio_error": str(exc)[:220]}

    def shutdown(self) -> None:
        if self._client is not None:
            try:
                self._client.shutdown()
            except Exception:
                pass
            self._client = None

    def _load(self) -> None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return
        try:
            from agent_companion.core.config import load_app_config

            self._config = load_app_config(config_path)
        except Exception:
            self._config = None
