"""Xiaomi MiMo speech synthesis, including voices built from a description.

This is shaped like a chat completion rather than like `/v1/audio/speech`: the
voice goes in a `user` turn, the line to speak goes in an `assistant` turn, and
the audio comes back base64 inside the message. So it cannot share the
OpenAI-compatible client, even though both are "cloud TTS".

What makes it worth a second client is `mimo-v2.5-tts-voicedesign`, which
builds a voice out of a sentence describing it. A character gets its own voice
with no reference recording, no training and no local service -- which is the
one thing the system voice could never do and the one thing GPT-SoVITS charges
an afternoon of installation for.

Lines spoken this way leave the machine.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Iterator
import urllib.request
import uuid

from agent_companion.core.config import AppConfig, CharacterConfig


# Models differ in where the voice comes from, which changes what the `user`
# turn means -- see `_voice_turn`.
VOICE_DESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
PRESET_MODEL = "mimo-v2.5-tts"

# Natural-language control is supported by every MiMo V2.5 TTS model and gives
# much finer delivery than a single emotion noun. These directions deliberately
# describe audible performance only: no body actions, no scene invention, and
# no melodramatic sighing that would become tiring in a desktop companion.
EMOTION_DIRECTIONS: dict[str, str] = {
    "neutral": "自然、亲近地说，吐字清楚但不要有播音腔；语速平稳，停顿短而自然。",
    "happy": "带着克制的欣喜和笑意，音调轻微上扬，语速略快；亲切有活力，但不要夸张。",
    "thinking": "像一边思考一边说，语速略慢，在语义转折处短暂停顿；专注但不要拖沓。",
    "serious": "沉稳、克制、清晰地说，音调稍低，重音落在关键信息上；认真但不要冷硬。",
    "alert": "专注而明确地提醒，语速稍快，重音清楚；有紧迫感但不要惊慌或喊叫。",
    "worried": "带着轻微担忧和关切，音量稍低，语速略慢，尾音收住；不要哭腔或夸张叹息。",
}

DELIVERY_PHRASES: dict[str, dict[str, str]] = {
    "pace": {"slow": "语速放慢", "measured": "语速从容、略慢", "steady": "语速自然平稳", "quick": "语速稍快但不赶"},
    "energy": {"soft": "声音柔和", "balanced": "能量自然", "bright": "声音明亮、有生气", "firm": "声音坚定但不生硬"},
    "pause": {"light": "停顿轻巧", "natural": "按语义自然停顿", "reflective": "在思路转折处稍作停顿", "deliberate": "在关键信息前后留出克制停顿", "short": "停顿短而清楚", "gentle": "停顿柔和，不拖长尾音"},
    "emphasis": {"light": "重音轻", "warm": "用温暖、肯定的重音落在关键词上", "keywords": "只在关键词上落重音", "urgent": "用清楚的重音说出需要立即注意的信息", "caring": "用轻柔重音表达对对方感受的关切"},
    "relation": {"close": "像熟悉的伙伴在近距离说话", "supportive": "像在身边支持对方", "professional": "保持可信而克制", "protective": "像及时保护性提醒"},
}

DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_TIMEOUT_SECONDS = 120


class MimoTtsClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._base_url = (config.tts.base_url or DEFAULT_BASE_URL).strip().rstrip("/")

    def synthesize(
        self,
        text: str,
        character: CharacterConfig,
        output_dir: Path,
        emotion: str = "neutral",
        delivery: object | None = None,
    ) -> Path:
        payload = self._payload(text, character, emotion, delivery=delivery, stream=False)
        data = self._post(payload)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"mimo-{uuid.uuid4().hex[:12]}.wav"
        destination.write_bytes(data)
        return destination

    def stream_pcm16(
        self,
        text: str,
        character: CharacterConfig,
        emotion: str = "neutral",
        delivery: object | None = None,
    ) -> Iterator[bytes]:
        """Yield low-latency 24 kHz mono PCM16 chunks from the preset model."""

        model = (self._config.tts.model or VOICE_DESIGN_MODEL).strip()
        if model != PRESET_MODEL:
            raise RuntimeError("MiMo TTS streaming requires the preset model")
        payload = self._payload(text, character, emotion, delivery=delivery, stream=True)
        request = self._request(payload)
        timeout = float(self._config.tts.timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        received = False
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                encoded_event = line[5:].strip()
                if encoded_event == "[DONE]":
                    break
                try:
                    body = json.loads(encoded_event)
                    audio = body["choices"][0]["delta"]["audio"] or {}
                    encoded_audio = audio.get("data", "")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
                    continue
                if not encoded_audio:
                    continue
                try:
                    chunk = base64.b64decode(encoded_audio, validate=True)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("MiMo TTS returned invalid streaming audio") from exc
                if chunk:
                    received = True
                    yield chunk
        if not received:
            raise RuntimeError("MiMo TTS returned no streaming audio")

    def _payload(
        self,
        text: str,
        character: CharacterConfig,
        emotion: str,
        *,
        delivery: object | None,
        stream: bool,
    ) -> dict[str, object]:
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS text is empty")
        if not self._config.tts.api_key.strip():
            raise RuntimeError("MiMo TTS is not configured: api_key is empty")
        model = (self._config.tts.model or VOICE_DESIGN_MODEL).strip()
        if model == VOICE_DESIGN_MODEL and not character.voice_design().strip():
            raise RuntimeError("MiMo TTS is not configured: voicedesign needs a voice description")
        context = self._voice_turn(character, model, emotion, delivery)

        audio: dict[str, object] = {"format": "pcm16" if stream else "wav"}
        if model == VOICE_DESIGN_MODEL:
            # Off by default. Turning it on measurably changes the reading of
            # an identical line -- the same six characters came back 63%
            # longer -- which means it is rewriting rather than tidying, and
            # what the character says is Joi's decision, not the synthesiser's.
            audio["optimize_text_preview"] = bool(self._config.tts.optimize_text)
        elif self._config.tts.voice.strip():
            audio["voice"] = self._config.tts.voice.strip()

        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "user", "content": context},
                {"role": "assistant", "content": self._perform(text, model)},
            ],
            "audio": audio,
        }
        if stream:
            payload["stream"] = True
        return payload

    def _voice_turn(self, character: CharacterConfig, model: str, emotion: str, delivery: object | None) -> str:
        """What goes in the `user` turn, which is not the same thing per model.

        For `voicedesign` the stable first sentence is the timbre identity. The
        second sentence explicitly locks that identity while varying delivery;
        MiMo documents natural-language context as this model's style-control
        path, while audio tags are unsupported on it.

        For a preset voice the timbre comes from `audio.voice`, so the whole
        turn is delivery direction.
        """

        from agent_companion.core.voice import normalize_emotion, normalize_voice_delivery

        design = character.voice_design().strip()
        mood = character.voice_emotion(emotion)
        normalized_emotion = normalize_emotion(emotion)
        plan = normalize_voice_delivery(delivery, normalized_emotion)
        direction = self._delivery_direction(
            normalized_emotion,
            plan,
            mood.instructions if mood else "",
        )
        if model == VOICE_DESIGN_MODEL:
            return f"{design}\n保持上述同一音色和人物身份，只调整本句的表达：{direction}"
        return direction

    @staticmethod
    def _delivery_direction(emotion: str, delivery: dict[str, float | str], authored: str = "") -> str:
        """Merge character authorship with the complete performance plan.

        A package's two-character mood label used to replace the whole default
        direction.  It now remains a supplement, so `开心` cannot erase pace,
        pauses, emphasis and the relationship tone.
        """

        intensity = float(delivery.get("intensity") or 0.4)
        if intensity < 0.38:
            intensity_phrase = "情绪只轻微流露"
        elif intensity < 0.68:
            intensity_phrase = "情绪清楚但保持克制"
        else:
            intensity_phrase = "情绪明显，但不要喊叫或舞台化"
        details = [intensity_phrase]
        for key in ("pace", "energy", "pause", "emphasis", "relation"):
            value = str(delivery.get(key) or "")
            phrase = DELIVERY_PHRASES.get(key, {}).get(value)
            if phrase:
                details.append(phrase)
        base = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["neutral"])
        authored = " ".join(str(authored or "").split())[:120]
        supplement = f"角色专属补充：{authored}。" if authored else ""
        return f"{base}{supplement}{'；'.join(details)}。不要逐字等时朗读，也不要使用播音腔。"

    @staticmethod
    def _perform(text: str, model: str) -> str:
        """Return only the words to speak; direction has one authoritative channel."""

        # MiMo accepts natural-language direction in the user turn for both
        # V2.5 models.  Repeating a coarse parenthesised tag here made the
        # preset path receive two competing instructions and sometimes read
        # the tag too literally.
        return text

    def _post(self, payload: dict[str, object]) -> bytes:
        request = self._request(payload)
        timeout = float(self._config.tts.timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        try:
            encoded = body["choices"][0]["message"]["audio"]["data"]
        except (KeyError, IndexError, TypeError) as exc:
            # A 200 with no audio in it is a real outcome here -- a refusal, or
            # a model that answered in text -- and must not surface as a crash.
            raise RuntimeError("MiMo TTS returned no audio") from exc
        data = base64.b64decode(encoded)
        if not data:
            raise RuntimeError("MiMo TTS returned empty audio")
        return data

    def _request(self, payload: dict[str, object]) -> urllib.request.Request:
        return urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "api-key": self._config.tts.api_key.strip(),
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def shutdown(self) -> None:
        """Nothing to tear down: every line is one stateless request."""
