from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Iterator

from agent_companion.core.config import AppConfig, CharacterConfig
from agent_companion.core.language_policy import spoken_language_override
from agent_companion.core.voice import sprite_for_emotion


# Where a GPT-SoVITS checkout keeps the interpreter that can run it.
#
# The Windows release ships a self-contained `runtime\python.exe`, which is why
# that was once the only path worth looking at. Every other platform installs
# it by hand into an environment of the user's choosing, so a Mac that had
# GPT-SoVITS fully working could still never start it from here.
INTERPRETER_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("runtime", "python.exe"),
    ("runtime", "bin", "python"),
    (".venv", "bin", "python"),
    ("venv", "bin", "python"),
    ("env", "bin", "python"),
)

# Cold-starting GPT-SoVITS means loading model weights, which is tens of
# seconds on a machine without a fast GPU and is the normal case on a Mac.
START_TIMEOUT_SECONDS = 180.0
START_POLL_SECONDS = 1.5


def _process_is_gpt_sovits(pid: int) -> bool:
    """Whether this pid is still the service, rather than a recycled number.

    Checked by what the process is actually running: pids are reused, and
    terminating whatever inherited one would be a far worse outcome than
    leaving a stray service alive.
    """

    try:
        completed = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and "api_v2.py" in completed.stdout


class _UnhealthyService(RuntimeError):
    """The service replied, and the reply says its own inference broke."""


class GptSoVitsClient:
    """Small GPT-SoVITS HTTP client used by Joi voice output."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._server_url = config.tts.server_url.rstrip("/") + "/"
        self._server_process: subprocess.Popen | None = None
        # A service this workspace started on an earlier run and never got to
        # stop -- see `_adopt_orphan`.
        self._adopted_pid = 0
        self._current_gpt_model_path = ""
        self._current_sovits_model_path = ""

    def synthesize(self, text: str, character: CharacterConfig, sprite_id: str = "1", emotion: str = "neutral") -> Path:
        payload = self._synthesis_payload(text, character, sprite_id, emotion)
        output_dir = self._config.base_dir / "data" / "cache" / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(output_dir.glob("tts_*.wav"))) % 1000
        output_path = output_dir / f"tts_{index:03d}.wav"
        data = self._post_bytes("tts", payload, timeout=360)
        if not data:
            raise RuntimeError("GPT-SoVITS returned empty audio")
        output_path.write_bytes(data)
        self._smooth_wav_edges(output_path)
        return output_path

    def _recover_unhealthy_server(self) -> bool:
        """Restart a process that answers its port but can no longer synthesise.

        This is the shape the failure actually took: the service stayed up for
        hours, kept answering `/control` with 200, and returned 500 to every
        single synthesis. `_server_alive` only asks whether the port replies,
        so it read as healthy forever -- and with the system voice switched off
        that meant permanent silence with nothing to restart it.

        Only a process Joi started is restarted here. One the user launched is
        theirs to manage, and killing it out from under them would be a worse
        surprise than the error message.
        """

        if self._server_process is None and not self._adopted_pid:
            return False
        self.stop_owned_server()
        self._current_gpt_model_path = ""
        self._current_sovits_model_path = ""
        try:
            self._ensure_server_started()
        except Exception:
            return False
        return True

    def stream_pcm16(
        self,
        text: str,
        character: CharacterConfig,
        sprite_id: str = "1",
        emotion: str = "neutral",
    ) -> Iterator[tuple[bytes, int]]:
        """Yield mono PCM16 from GPT-SoVITS' official streaming endpoint.

        The endpoint's first streamed bytes are a WAV header carrying the
        model's real sample rate.  Parsing it avoids assuming 24/32 kHz and
        playing a valid local voice at the wrong pitch or speed.
        """

        payload = self._synthesis_payload(text, character, sprite_id, emotion)
        payload.update(
            {
                "media_type": "wav",
                "streaming_mode": int(getattr(self._config.tts, "gpt_sovits_streaming_mode", 2) or 2),
                "fragment_interval": 0.15,
            }
        )
        request = self._post_request("tts", payload)
        with urllib.request.urlopen(request, timeout=360) as response:
            header = response.read(44)
            sample_rate = _stream_wav_sample_rate(header)
            pending = b""
            reader = getattr(response, "read1", response.read)
            while True:
                data = reader(8192)
                if not data:
                    break
                data = pending + data
                even = len(data) - (len(data) % 2)
                if even:
                    yield data[:even], sample_rate
                pending = data[even:]

    def warmup(self, character: CharacterConfig) -> None:
        """Keep the local service, weights and first inference graph resident.

        Loading weights alone leaves Apple MPS to compile its graph on the
        first line the user actually hears. Run one short, discarded request
        during Joi's background warmup so that cost never opens the mouth or
        delays the first visible reply.
        """

        self._ensure_server_started()
        self._switch_model(character)
        if not character.voice_refer_audio_path() or not character.voice_prompt_text():
            return
        language = character.voice_text_lang(self._config.tts.text_lang)
        text = _warmup_text(language)
        payload = self._synthesis_payload(text, character, "1", "neutral")
        payload.update(
            {
                "media_type": "wav",
                "streaming_mode": int(getattr(self._config.tts, "gpt_sovits_streaming_mode", 2) or 2),
                "fragment_interval": 0.1,
            }
        )
        data = self._post_bytes("tts", payload, timeout=360)
        if not data:
            raise RuntimeError("GPT-SoVITS warmup returned empty audio")

    def _synthesis_payload(
        self,
        text: str,
        character: CharacterConfig,
        sprite_id: str,
        emotion: str,
    ) -> dict[str, object]:
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS text is empty")
        self._ensure_server_started()
        self._switch_model(character)
        ref_audio_path = character.voice_refer_audio_path()
        prompt_text = character.voice_prompt_text()
        prompt_lang = character.voice_prompt_lang(self._config.tts.prompt_lang)
        speed_factor = character.voice_speech_speed(self._config.tts.speed_factor)
        effective_sprite_id = str(sprite_id or "").strip()
        if not effective_sprite_id or (effective_sprite_id == "1" and sprite_for_emotion(emotion, "1") != "1"):
            effective_sprite_id = sprite_for_emotion(emotion)
        for sprite in character.sprites:
            if sprite.id == effective_sprite_id:
                if sprite.voice_path and sprite.voice_text:
                    ref_audio_path = sprite.voice_path
                    prompt_text = sprite.voice_text
                break
        # The character package's own emotion map wins over the sprite it was
        # inferred from: a sprite is a picture that happens to carry a clip,
        # while `emotion_map` is the author saying outright how this character
        # sounds when happy. Zero-shot GPT-SoVITS copies the delivery of
        # whatever clip it is given, so swapping the reference is the whole
        # mechanism -- but only together with its transcript, because a clip
        # described by the wrong text degrades the voice rather than colouring
        # it.
        mood = character.voice_emotion(emotion)
        if mood:
            if mood.refer_audio_path and mood.prompt_text:
                ref_audio_path = mood.refer_audio_path
                prompt_text = mood.prompt_text
            if mood.speech_speed is not None:
                speed_factor = mood.speech_speed
        # `text_lang` selects the phonetics for these words, not the character's
        # identity: that stays in the weights and the reference clip above. A
        # line written in another language is read as words, not spelled out in
        # the selected language's readings.
        text_lang = character.voice_text_lang(self._config.tts.text_lang)
        return {
            "ref_audio_path": self._resolve_config_path(ref_audio_path),
            "prompt_text": prompt_text or "",
            "prompt_lang": prompt_lang or self._config.tts.prompt_lang,
            "text": text,
            "text_lang": spoken_language_override(text_lang, text) or text_lang,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": speed_factor,
            "seed": int(getattr(self._config.tts, "gpt_sovits_seed", -1)),
            "top_k": int(getattr(self._config.tts, "gpt_sovits_top_k", 15)),
            "top_p": float(getattr(self._config.tts, "gpt_sovits_top_p", 1.0)),
            "temperature": float(getattr(self._config.tts, "gpt_sovits_temperature", 1.0)),
            "repetition_penalty": float(getattr(self._config.tts, "gpt_sovits_repetition_penalty", 1.35)),
        }

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
            # Already up, but possibly ours from a previous run: Joi stops the
            # service when it quits, and a force-quit skips that, leaving the
            # process behind. Reclaiming it is what makes the next quit able to
            # stop it and the next 500 able to restart it -- otherwise an
            # orphan is permanently unowned, which is the state that left the
            # character silent for a day.
            self._adopt_orphan()
            return
        work_path = (self._config.tts.gpt_sovits_work_path or "").strip()
        if not work_path:
            raise RuntimeError("GPT-SoVITS is not installed: no work path is configured")
        base = Path(work_path).expanduser()
        if base.suffix.lower() == ".py":
            base = base.parent
        api_script = base / "api_v2.py"
        if not api_script.is_file():
            raise RuntimeError(f"GPT-SoVITS is not installed at the configured path: {base}")
        interpreter = self._interpreter(base)
        if interpreter is None:
            raise RuntimeError(
                "GPT-SoVITS is not installed with an interpreter Joi can find; "
                "start api_v2.py yourself or set tts.gpt_sovits_python"
            )
        self._server_process = subprocess.Popen([str(interpreter), str(api_script)], cwd=str(base))
        self._remember_started_pid(self._server_process.pid)
        # Loading the models takes far longer than an HTTP round trip, and
        # synthesising against a server that has not finished starting fails in
        # a way that reads as "GPT-SoVITS is broken" rather than "wait".
        self._wait_for_server(START_TIMEOUT_SECONDS)

    def _pid_file(self) -> Path:
        return self._config.base_dir / "data" / "agent_companion" / "gpt-sovits.pid"

    def _remember_started_pid(self, pid: int) -> None:
        try:
            path = self._pid_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(pid), encoding="utf-8")
        except OSError:
            # Only costs the ability to reclaim an orphan later; never worth
            # failing a synthesis over.
            pass

    def _adopt_orphan(self) -> None:
        """Take back a service this workspace started and did not get to stop."""

        if self._server_process is not None or self._adopted_pid:
            return
        try:
            pid = int(self._pid_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        # A pid alone is not proof: the number is recycled, and killing whatever
        # inherited it would be far worse than leaving a service running. Only
        # a process that is still GPT-SoVITS counts.
        if pid > 1 and _process_is_gpt_sovits(pid):
            self._adopted_pid = pid

    def stop_owned_server(self) -> None:
        """Stop the service if this workspace is the one that started it.

        A service the user runs themselves is left alone -- Joi starting it on
        launch does not make every running instance Joi's to kill.
        """

        pid = self._adopted_pid
        self._adopted_pid = 0
        self.shutdown()
        if pid and _process_is_gpt_sovits(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
        try:
            self._pid_file().unlink(missing_ok=True)
        except OSError:
            pass

    def _interpreter(self, base: Path) -> Path | None:
        """The Python that has GPT-SoVITS' dependencies, or nothing.

        Never Joi's own interpreter. Joi does not depend on torch, so falling
        back to `sys.executable` would replace a clear "not installed" with an
        ImportError from a subprocess the user never asked to run.
        """

        declared = (self._config.tts.gpt_sovits_python or "").strip()
        if declared:
            candidate = Path(declared).expanduser()
            return candidate if candidate.is_file() else None
        for parts in INTERPRETER_CANDIDATES:
            candidate = base.joinpath(*parts)
            if candidate.is_file():
                return candidate
        return None

    def _wait_for_server(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server_alive():
                return
            # A crashed launch will never answer, and waiting out the whole
            # timeout for it only delays the fallback to the system voice.
            if self._server_process is not None and self._server_process.poll() is not None:
                raise RuntimeError("GPT-SoVITS exited while starting up")
            time.sleep(START_POLL_SECONDS)
        raise RuntimeError("GPT-SoVITS did not finish starting up in time")

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
        try:
            return self._post_once(endpoint, payload, timeout)
        except _UnhealthyService:
            # A 5xx is the service failing at inference, not the request being
            # wrong -- the same call succeeds against a freshly started one.
            # Worth exactly one restart-and-retry; a second failure is real.
            if not self._recover_unhealthy_server():
                raise RuntimeError(
                    "GPT-SoVITS is answering but cannot synthesize; restart the GPT-SoVITS service"
                ) from None
            return self._post_once(endpoint, payload, timeout)

    def _post_once(self, endpoint: str, payload: dict, timeout: int) -> bytes:
        req = self._post_request(endpoint, payload)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code >= 500:
                raise _UnhealthyService(f"GPT-SoVITS HTTP {exc.code}: {body[:240]}") from exc
            raise RuntimeError(f"GPT-SoVITS HTTP {exc.code}: {body[:240]}") from exc

    def _post_request(self, endpoint: str, payload: dict) -> urllib.request.Request:
        return urllib.request.Request(
            self._server_url + endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

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


def _stream_wav_sample_rate(header: bytes) -> int:
    """Validate the standard 44-byte header emitted by GPT-SoVITS streaming."""

    if len(header) < 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise RuntimeError("GPT-SoVITS streaming returned invalid WAV header")
    channels = int.from_bytes(header[22:24], "little")
    sample_rate = int.from_bytes(header[24:28], "little")
    sample_width = int.from_bytes(header[34:36], "little")
    if channels != 1 or sample_width != 16 or not 8_000 <= sample_rate <= 96_000:
        raise RuntimeError("GPT-SoVITS streaming returned unsupported PCM format")
    return sample_rate


def _warmup_text(language: str) -> str:
    """A short frontend-valid phrase used only to compile local inference."""

    key = str(language or "").strip().casefold()
    if key.startswith("ja"):
        return "準備できました。"
    if key.startswith("en"):
        return "Ready."
    return "准备好了。"
