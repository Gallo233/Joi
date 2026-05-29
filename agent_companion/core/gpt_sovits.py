from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from agent_companion.core.config import AppConfig, CharacterConfig
from agent_companion.core.voice import sprite_for_emotion


class GptSoVitsClient:
    """Small GPT-SoVITS HTTP client used by Joi voice output."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._server_url = config.tts.server_url.rstrip("/") + "/"
        self._server_process: subprocess.Popen | None = None
        self._current_gpt_model_path = ""
        self._current_sovits_model_path = ""

    def synthesize(self, text: str, character: CharacterConfig, sprite_id: str = "1", emotion: str = "neutral") -> Path:
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS text is empty")

        self._ensure_server_started()
        self._switch_model(character)

        ref_audio_path = character.voice_refer_audio_path()
        prompt_text = character.voice_prompt_text()
        prompt_lang = character.voice_prompt_lang(self._config.tts.prompt_lang)
        effective_sprite_id = str(sprite_id or "").strip()
        if not effective_sprite_id or (effective_sprite_id == "1" and sprite_for_emotion(emotion, "1") != "1"):
            effective_sprite_id = sprite_for_emotion(emotion)
        for sprite in character.sprites:
            if sprite.id == effective_sprite_id:
                if sprite.voice_path and sprite.voice_text:
                    ref_audio_path = sprite.voice_path
                    prompt_text = sprite.voice_text
                break

        output_dir = self._config.base_dir / "data" / "cache" / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(output_dir.glob("tts_*.wav"))) % 1000
        output_path = output_dir / f"tts_{index:03d}.wav"

        payload = {
            "ref_audio_path": self._resolve_config_path(ref_audio_path),
            "prompt_text": prompt_text or "",
            "prompt_lang": prompt_lang or self._config.tts.prompt_lang,
            "text": text,
            "text_lang": character.voice_text_lang(self._config.tts.text_lang),
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": character.voice_speech_speed(self._config.tts.speed_factor),
        }
        data = self._post_bytes("tts", payload, timeout=360)
        if not data:
            raise RuntimeError("GPT-SoVITS returned empty audio")
        output_path.write_bytes(data)
        self._smooth_wav_edges(output_path)
        return output_path

    def shutdown(self) -> None:
        if self._server_process is None:
            return
        try:
            self._server_process.terminate()
            self._server_process.wait(timeout=5)
        except Exception:
            try:
                self._server_process.kill()
            except Exception:
                pass
        self._server_process = None

    def _ensure_server_started(self) -> None:
        if self._server_alive():
            return
        work_path = (self._config.tts.gpt_sovits_work_path or "").strip()
        if not work_path:
            raise RuntimeError("GPT-SoVITS service is not running and gpt_sovits_work_path is empty")
        base = Path(work_path)
        if base.suffix.lower() == ".py":
            base = base.parent
        runtime_python = base / "runtime" / "python.exe"
        api_script = base / "api_v2.py"
        if not runtime_python.is_file() or not api_script.is_file():
            raise RuntimeError(f"Invalid GPT-SoVITS path: {base}")
        self._server_process = subprocess.Popen([str(runtime_python), str(api_script)], cwd=str(base))

    def _server_alive(self) -> bool:
        try:
            req = urllib.request.Request(self._server_url, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as response:
                return response.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except Exception:
            return False

    def _switch_model(self, character: CharacterConfig) -> None:
        gpt_path = self._resolve_config_path(character.voice_gpt_model_path())
        sovits_path = self._resolve_config_path(character.voice_sovits_model_path())
        if gpt_path.endswith(".ckpt") and gpt_path != self._current_gpt_model_path:
            self._get("set_gpt_weights", {"weights_path": gpt_path}, timeout=15)
            self._current_gpt_model_path = gpt_path
        if sovits_path.endswith(".pth") and sovits_path != self._current_sovits_model_path:
            self._get("set_sovits_weights", {"weights_path": sovits_path}, timeout=15)
            self._current_sovits_model_path = sovits_path

    def _resolve_config_path(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        path = Path(value)
        if not path.is_absolute():
            path = self._config.base_dir / path
        return path.resolve().as_posix()

    def _get(self, endpoint: str, params: dict[str, str], timeout: int) -> bytes:
        url = self._server_url + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()

    def _post_bytes(self, endpoint: str, payload: dict, timeout: int) -> bytes:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._server_url + endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GPT-SoVITS HTTP {exc.code}: {body[:240]}") from exc

    @staticmethod
    def _smooth_wav_edges(path: Path) -> None:
        try:
            with wave.open(str(path), "rb") as reader:
                params = reader.getparams()
                frames = bytearray(reader.readframes(reader.getnframes()))
        except Exception:
            return
        if params.sampwidth != 2 or params.nframes <= 8 or not frames:
            return

        channels = max(1, params.nchannels)
        fade_frames = min(params.nframes // 4, max(1, int(params.framerate * 0.012)))
        if fade_frames <= 1:
            return

        def scale_sample(frame_index: int, gain: float) -> None:
            for channel in range(channels):
                offset = (frame_index * channels + channel) * 2
                sample = int.from_bytes(frames[offset : offset + 2], "little", signed=True)
                sample = max(-32768, min(32767, int(sample * gain)))
                frames[offset : offset + 2] = sample.to_bytes(2, "little", signed=True)

        for frame_index in range(fade_frames):
            gain = frame_index / fade_frames
            scale_sample(frame_index, gain)
            scale_sample(params.nframes - frame_index - 1, gain)

        try:
            with wave.open(str(path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(frames)
        except Exception:
            return
