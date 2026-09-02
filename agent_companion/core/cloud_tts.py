"""A hosted voice, for proving the chain works before the real one exists.

GPT-SoVITS gives a character its own voice, and installing it means conda, a
Python it does not share with Joi, and several gigabytes of weights. Until that
is done the only audio available is `say`, which cannot act and cannot be
steered -- so everything the voice path does around the audio stays unverified:
barge-in, generation epochs, the mouth reading vowels out of the signal, the
character's emotion reaching the synthesiser at all.

This is the middle rung. `/v1/audio/speech` is implemented by OpenAI, 硅基流动,
Fish Audio and most proxies alike, so one client covers whichever the user
already has a key for, and it takes direction -- a mood is a sentence of
instructions rather than a table of pitch offsets.

It is not private. Every line spoken this way leaves the machine, which is why
it is neither the default nor the destination: GPT-SoVITS remains the answer
for a character's actual voice, and this is how you find out the rest of the
path is right before spending an afternoon installing it.
"""

from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request
import uuid

from agent_companion.core.config import AppConfig, CharacterConfig


# What each mood asks of a model that can be told how to read a line, rather
# than only how fast and how high. Written in Chinese because that is what
# these characters speak, and a direction in the wrong language reads as part
# of the text to some providers.
EMOTION_INSTRUCTIONS: dict[str, str] = {
    "neutral": "",
    "happy": "语气轻快上扬，带着笑意，节奏比平时略快。",
    "thinking": "语速放慢，语气平缓，像在一边想一边说。",
    "serious": "语气沉稳克制，音调偏低，不带情绪起伏。",
    "alert": "语气紧一些，语速加快，重音明显，像在提醒对方注意。",
    "worried": "语气低一些，语速偏慢，尾音收住，透出迟疑。",
}

# WAV rather than the mp3 these APIs default to. The shell has to decode it,
# and the lip sync analyser reads the waveform -- the same reason the system
# voice does not ship AIFF.
DEFAULT_FORMAT = "wav"

# Long enough for a slow provider on a long line, short enough that a hung
# request still falls through to the system voice while the user is waiting.
DEFAULT_TIMEOUT_SECONDS = 60


class CloudTtsClient:
    """OpenAI-compatible `/v1/audio/speech`, over plain HTTP."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._base_url = (config.tts.base_url or "").strip().rstrip("/")
        # Providers that reject an unknown field do so on every request, so the
        # answer is remembered rather than rediscovered per line.
        self._instructions_supported = True

    def synthesize(self, text: str, character: CharacterConfig, output_dir: Path, emotion: str = "neutral") -> Path:
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS text is empty")
        if not self._base_url or not self._config.tts.api_key.strip():
            raise RuntimeError("Cloud TTS is not configured: base_url or api_key is empty")
        if not self._config.tts.model.strip():
            raise RuntimeError("Cloud TTS is not configured: no model was named")

        speed = character.voice_speech_speed(self._config.tts.speed_factor)
        instructions = self._instructions(character, emotion)
        mood = character.voice_emotion(emotion)
        if mood and mood.speech_speed is not None:
            speed = float(mood.speech_speed)

        payload: dict[str, object] = {
            "model": self._config.tts.model.strip(),
            "input": text,
            "voice": self._config.tts.voice.strip() or "alloy",
            "response_format": (self._config.tts.audio_format or DEFAULT_FORMAT).strip() or DEFAULT_FORMAT,
            "speed": round(max(0.25, min(4.0, speed)), 2),
        }
        audio = self._request(payload, instructions)
        if not audio:
            raise RuntimeError("Cloud TTS returned empty audio")
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"cloud-{uuid.uuid4().hex[:12]}.{payload['response_format']}"
        destination.write_bytes(audio)
        return destination

    def _instructions(self, character: CharacterConfig, emotion: str) -> str:
        """How to read this line, preferring what the character's author wrote."""

        from agent_companion.core.voice import normalize_emotion

        mood = character.voice_emotion(emotion)
        if mood and mood.instructions:
            return mood.instructions
        return EMOTION_INSTRUCTIONS.get(normalize_emotion(emotion), "")

    def _request(self, payload: dict[str, object], instructions: str) -> bytes:
        """POST once with the direction, and again without it if it is refused.

        Only some models accept `instructions`; the rest answer 400 on the
        extra field. Retrying without it keeps the character speaking in a flat
        voice rather than not speaking at all, and costs one wasted request per
        process rather than per line.
        """

        if instructions and self._instructions_supported:
            try:
                return self._post({**payload, "instructions": instructions})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                self._instructions_supported = False
        return self._post(payload)

    def _post(self, payload: dict[str, object]) -> bytes:
        request = urllib.request.Request(
            f"{self._base_url}/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._config.tts.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        timeout = float(self._config.tts.timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def shutdown(self) -> None:
        """Nothing to tear down: every line is one stateless request."""
