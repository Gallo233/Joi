from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from agent_companion.core.language_policy import CHAT_LANGUAGE_CHOICES, CHAT_LANGUAGE_FOLLOW
from agent_companion.core.secret_store import LLM_API_KEY_ENV, QWEN_REALTIME_API_KEY_ENV, managed_secret


_ENV_REFERENCE_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^%([A-Za-z_][A-Za-z0-9_]*)%$")


_QWEN_WORKSPACE_HOST = re.compile(
    r"^[a-z0-9][a-z0-9-]{0,62}\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com$"
)
QWEN_REALTIME_MODELS = frozenset(
    {"qwen-audio-3.0-realtime-flash", "qwen-audio-3.0-realtime-plus"}
)


def is_safe_qwen_realtime_url(value: str) -> bool:
    """Accept only the reviewed TLS Qwen Realtime WebSocket endpoints.

    The model is appended by Core after validation. Rejecting a pre-existing
    query prevents credentials or caller-controlled routing from hiding there.
    """

    try:
        parsed = urlsplit(str(value or "").strip())
        # Accessing port validates malformed or out-of-range port text.
        _ = parsed.port
    except ValueError:
        return False
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    host = parsed.hostname.casefold()
    return bool(
        parsed.scheme.casefold() == "wss"
        and parsed.port in {None, 443}
        and parsed.path == "/api-ws/v1/realtime"
        and (host == "dashscope.aliyuncs.com" or _QWEN_WORKSPACE_HOST.fullmatch(host))
    )


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_REFERENCE_RE.fullmatch(value.strip())
        if match:
            name = match.group(1) or match.group(2) or ""
            resolved = os.environ.get(name, "").strip()
            if not resolved:
                resolved = managed_secret(name)
            return resolved or value
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


MODEL_ROUTES: tuple[str, ...] = ("fast", "reasoning", "vision", "code", "summarize", "voice_style")
MODEL_ROUTE_LABELS: dict[str, str] = {
    "fast": "Fast Model",
    "reasoning": "Reasoning Model",
    "vision": "Vision Model",
    "code": "Code Model",
    "summarize": "Summarize Model",
    "voice_style": "Voice Style Model",
}
MODEL_ROUTE_ALIASES: dict[str, str] = {
    "": "fast",
    "text": "fast",
    "chat": "fast",
    "companion_chat": "fast",
    "planner": "reasoning",
    "plan": "reasoning",
    "expression": "voice_style",
}


@dataclass(frozen=True)
class SpriteConfig:
    id: str
    label: str
    image_path: str = ""
    voice_path: str = ""
    voice_text: str = ""


@dataclass(frozen=True)
class VoiceEmotionConfig:
    """How the character sounds in one mood, over its normal speaking voice.

    Every field is optional and falls back to the base voice. A package that
    supplies only a reference clip still gets its own timbre for that mood;
    one that supplies only a pitch still gets a differently delivered line
    from a voice that cannot change timbre at all.
    """

    refer_audio_path: str = ""
    prompt_text: str = ""
    speech_speed: float | None = None
    # -1 lowest, 1 highest, relative to the voice's normal pitch.
    pitch: float | None = None
    # How to read the line, in words, for a hosted model that takes direction.
    instructions: str = ""


@dataclass(frozen=True)
class VoiceProfileConfig:
    id: str
    label: str
    text_lang: str = ""
    prompt_lang: str = ""
    gpt_model_path: str = ""
    sovits_model_path: str = ""
    refer_audio_path: str = ""
    prompt_text: str = ""
    speech_speed: float | None = None
    speech_volume: float | None = None
    emotion_map: dict[str, VoiceEmotionConfig] = field(default_factory=dict)
    # A sentence describing how this character sounds, for a synthesiser that
    # builds a voice from a description rather than from a recording.
    design: str = ""


@dataclass(frozen=True)
class CharacterConfig:
    name: str
    color: str
    setting: str
    sprite_color: str = "#224e66"
    gpt_model_path: str = ""
    sovits_model_path: str = ""
    refer_audio_path: str = ""
    prompt_text: str = ""
    prompt_lang: str = "zh"
    speech_speed: float = 1.2
    speech_volume: float = 1.0
    active_voice_profile: str = ""
    voice_profiles: list[VoiceProfileConfig] = field(default_factory=list)
    sprites: list[SpriteConfig] = field(default_factory=list)
    # What the character says while performing each semantic motion. Empty
    # falls back to Joi's shared table, which is a default mascot's voice
    # rather than this character's.
    motion_lines: dict[str, str] = field(default_factory=dict)
    # The same lines in every language the package wrote them in, so the reply
    # can follow the language of the user's message. Which language she
    # *speaks* is a separate setting and does not decide this.
    motion_lines_by_locale: dict[str, dict[str, str]] = field(default_factory=dict)

    def motion_line(self, motion: str, reply_language: str = "") -> str:
        """Her line for a motion, in the language the user just wrote in.

        Falls back to her active locale, then to any language she does have it
        in, and finally to nothing so the caller can use the shared table. A
        line in the wrong language is worse than a generic one in the right
        language: the first reads as the app malfunctioning, the second merely
        as the character being brief.
        """

        wanted = str(reply_language or "").strip().casefold().split("-")[0]
        tables = self.motion_lines_by_locale or {}
        if not wanted:
            return str(self.motion_lines.get(motion) or "").strip()
        for locale in (wanted, *[key for key in tables if key.split("-")[0] == wanted]):
            line = str((tables.get(locale) or {}).get(motion) or "").strip()
            if line:
                return line
        # No line in the user's language. Deliberately *not* falling back to
        # the language she is voiced in -- that is precisely the coupling this
        # exists to break, and it is what made a Chinese "跳个舞" answer in
        # Japanese. The caller's shared table is the neutral answer.
        return ""

    @property
    def active_voice(self) -> VoiceProfileConfig | None:
        if not self.voice_profiles:
            return None
        active = (self.active_voice_profile or "").strip().casefold()
        for profile in self.voice_profiles:
            if profile.id.casefold() == active:
                return profile
        return self.voice_profiles[0]

    def voice_text_lang(self, fallback: str) -> str:
        profile = self.active_voice
        return (profile.text_lang if profile and profile.text_lang else fallback) or "zh"

    def voice_prompt_lang(self, fallback: str) -> str:
        profile = self.active_voice
        return (profile.prompt_lang if profile and profile.prompt_lang else self.prompt_lang or fallback) or "zh"

    def voice_gpt_model_path(self) -> str:
        profile = self.active_voice
        return profile.gpt_model_path if profile and profile.gpt_model_path else self.gpt_model_path

    def voice_sovits_model_path(self) -> str:
        profile = self.active_voice
        return profile.sovits_model_path if profile and profile.sovits_model_path else self.sovits_model_path

    def voice_refer_audio_path(self) -> str:
        profile = self.active_voice
        return profile.refer_audio_path if profile and profile.refer_audio_path else self.refer_audio_path

    def voice_prompt_text(self) -> str:
        profile = self.active_voice
        return profile.prompt_text if profile and profile.prompt_text else self.prompt_text

    def voice_speech_speed(self, fallback: float) -> float:
        profile = self.active_voice
        value = profile.speech_speed if profile and profile.speech_speed is not None else self.speech_speed
        return float(value or fallback)

    def voice_speech_volume(self, fallback: float) -> float:
        profile = self.active_voice
        value = profile.speech_volume if profile and profile.speech_volume is not None else self.speech_volume
        return float(value or fallback)

    def voice_design(self) -> str:
        profile = self.active_voice
        return profile.design if profile and profile.design else ""

    def voice_emotion(self, emotion: str) -> VoiceEmotionConfig | None:
        """What this character declared for one mood, or nothing.

        Nothing is the ordinary answer: a package that never wrote an
        `emotion_map` speaks every line in its base voice, exactly as before.
        """

        from agent_companion.core.voice import normalize_emotion

        profile = self.active_voice
        if not profile or not profile.emotion_map:
            return None
        return profile.emotion_map.get(normalize_emotion(emotion))


@dataclass(frozen=True)
class ModelRouteConfig:
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    use_mock: bool
    base_url: str
    model: str
    api_key: str
    temperature: float = 0.8
    mock_when_unconfigured: bool = True
    vision_enabled: bool = False
    vision_base_url: str = ""
    vision_model: str = ""
    vision_api_key: str = ""
    expression_enabled: bool = False
    expression_base_url: str = ""
    expression_model: str = ""
    expression_api_key: str = ""
    routes: dict[str, ModelRouteConfig] = field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        key = (self.api_key or "").strip()
        if self.provider.strip().casefold() == "ollama":
            return bool(self.base_url.strip() and self.model.strip())
        return bool(key and not key.startswith("${") and not key.startswith("%"))

    @property
    def is_vision_configured(self) -> bool:
        if not self.vision_enabled:
            return False
        key = (self.vision_api_key or self.api_key or "").strip()
        model = (self.vision_model or self.model or "").strip()
        return bool(key and model and not key.startswith("${") and not key.startswith("%"))

    @property
    def is_expression_configured(self) -> bool:
        if not self.expression_enabled:
            return False
        key = (self.expression_api_key or self.api_key or "").strip()
        model = (self.expression_model or self.model or "").strip()
        return bool(key and model and not key.startswith("${") and not key.startswith("%"))


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    model: str
    api_key: str
    provider: str = "openai_compatible"
    route: str = "fast"
    configured: bool = False
    fallback_reason: str = ""
    use_mock: bool = False

    def to_agent_state(self, *, latency_ms: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "route": normalize_model_route(self.route),
            "provider": _safe_public_identifier(self.provider),
            "model": _safe_public_model(self.model),
            "configured": bool(self.configured),
            "fallback_reason": _safe_public_identifier(self.fallback_reason),
            "mock": bool(self.use_mock),
        }
        if latency_ms is not None:
            payload["latency_ms"] = max(0, int(round(latency_ms)))
        return payload


class ModelRouter:
    def __init__(self, llm: LlmConfig) -> None:
        self._llm = llm

    @staticmethod
    def stable_routes() -> tuple[str, ...]:
        return MODEL_ROUTES

    @staticmethod
    def normalize_route(use: str = "fast") -> str:
        return normalize_model_route(use)

    def resolve(self, use: str = "text") -> ModelEndpoint:
        route = normalize_model_route(use)
        override = self._llm.routes.get(route)
        fallback_reason = ""
        if override is not None:
            if override.enabled:
                endpoint = self._endpoint(
                    route,
                    provider=override.provider or self._llm.provider,
                    base_url=override.base_url or self._llm.base_url,
                    model=override.model or self._llm.model,
                    api_key=override.api_key or self._llm.api_key,
                    fallback_reason="",
                )
                if endpoint.configured or self._llm.use_mock:
                    return endpoint
                fallback_reason = f"{route}_route_unconfigured"
            else:
                fallback_reason = f"{route}_route_disabled"
        if route == "vision" and self._llm.is_vision_configured:
            return self._endpoint(
                route,
                provider=self._llm.provider,
                base_url=self._llm.vision_base_url or self._llm.base_url,
                model=self._llm.vision_model or self._llm.model,
                api_key=self._llm.vision_api_key or self._llm.api_key,
                fallback_reason=fallback_reason,
            )
        if route == "voice_style" and self._llm.is_expression_configured:
            return self._endpoint(
                route,
                provider=self._llm.provider,
                base_url=self._llm.expression_base_url or self._llm.base_url,
                model=self._llm.expression_model or self._llm.model,
                api_key=self._llm.expression_api_key or self._llm.api_key,
                fallback_reason=fallback_reason,
            )
        if not fallback_reason and route != "fast":
            fallback_reason = f"{route}_fallback_base"
        return self._endpoint(
            route,
            provider=self._llm.provider,
            base_url=self._llm.base_url,
            model=self._llm.model,
            api_key=self._llm.api_key,
            fallback_reason=fallback_reason,
        )

    def _endpoint(self, route: str, *, provider: str, base_url: str, model: str, api_key: str, fallback_reason: str) -> ModelEndpoint:
        configured = bool(self._llm.use_mock or _model_endpoint_configured(provider, base_url, model, api_key))
        return ModelEndpoint(
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider=provider,
            route=route,
            configured=configured,
            fallback_reason="mock" if self._llm.use_mock and not _configured_secret(api_key) else fallback_reason,
            use_mock=bool(self._llm.use_mock),
        )


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = False
    provider: str = ""
    volume: float = 0.85
    server_url: str = "http://127.0.0.1:9880/"
    gpt_sovits_work_path: str = ""
    # Which Python runs GPT-SoVITS. The Windows bundle ships its own and needs
    # no answer here; a macOS or Linux install is whatever environment the user
    # built it in, and Joi's own interpreter is not it.
    gpt_sovits_python: str = ""
    # Official API v2: 2 balances quality/latency; 3 is faster but rougher.
    # Joi never selects 3 implicitly because that changes the character voice.
    gpt_sovits_streaming_mode: int = 2
    # Sampling is explicit so a character does not pronounce the same line
    # differently after every restart. -1 retains GPT-SoVITS' random default.
    gpt_sovits_seed: int = -1
    gpt_sovits_top_k: int = 15
    gpt_sovits_top_p: float = 1.0
    gpt_sovits_temperature: float = 1.0
    gpt_sovits_repetition_penalty: float = 1.35
    text_lang: str = "zh"
    prompt_lang: str = "zh"
    speed_factor: float = 1.2
    # Deprecated compatibility field. The bridge intentionally ignores it:
    # Joi never substitutes an operating-system announcer for a character.
    fallback_to_system: bool = False
    # A hosted `/v1/audio/speech` endpoint, for exercising the voice path
    # before GPT-SoVITS exists. Lines spoken this way leave the machine.
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str = ""
    audio_format: str = "wav"
    timeout_seconds: int = 60
    # MiMo can rewrite a line before reading it -- punctuation and numbers,
    # but measurably more than that. Joi decides what the character says, so
    # this is off unless the user turns it on.
    optimize_text: bool = False

    @property
    def is_cloud_configured(self) -> bool:
        key = self.api_key.strip()
        return bool(
            self.base_url.strip()
            and self.model.strip()
            and key
            # An unexpanded `${VAR}` or `%VAR%` is a placeholder the user never
            # filled in, not a key.
            and not key.startswith("${")
            and not key.startswith("%")
        )


@dataclass(frozen=True)
class AsrConfig:
    enabled: bool = False
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    language: str = "zh"
    max_seconds: int = 30
    max_bytes: int = 12 * 1024 * 1024
    timeout_seconds: int = 30

    @property
    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        provider = self.provider.strip().casefold()
        if provider in {"", "mock"}:
            return provider == "mock"
        key = self.api_key.strip()
        return bool(self.base_url.strip() and self.model.strip() and key and not key.startswith("${") and not key.startswith("%"))


@dataclass(frozen=True)
class RealtimeVoiceConfig:
    """Core-owned Qwen Audio Realtime configuration.

    Qwen is fixed to text output so the selected local GPT-SoVITS character
    remains Joi's only voice. The long-lived key never enters the WebView.
    """

    enabled: bool = False
    provider: str = "qwen_audio"
    url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    model: str = "qwen-audio-3.0-realtime-flash"
    api_key: str = ""
    turn_detection: str = "server_vad"
    threshold: float = 0.5
    silence_duration_ms: int = 500
    max_history_turns: int = 8
    timeout_seconds: int = 15

    @property
    def is_configured(self) -> bool:
        key = self.api_key.strip()
        return bool(
            self.enabled
            and self.provider.strip().casefold() == "qwen_audio"
            and is_safe_qwen_realtime_url(self.url)
            and self.model.strip() in QWEN_REALTIME_MODELS
            and self.turn_detection.strip() in {"server_vad", "smart_turn"}
            and key
            and not key.startswith("${")
            and not key.startswith("%")
        )


@dataclass(frozen=True)
class OcrConfig:
    timeout_seconds: int = 5
    language: str = "chi_sim+eng"
    tesseract_cmd: str = ""
    tessdata_dir: str = ""


@dataclass(frozen=True)
class LanguageConfig:
    """What Joi shows and writes -- never what she says.

    The language she speaks belongs to the character package, so a Japanese
    voice can answer a Chinese message on screen in Chinese. `interface` is the
    shell's own language; only Chinese is localized today, so any other value
    would promise a translation that does not exist and is read as Chinese.
    """

    interface: str = "zh"
    chat: str = "zh"

    @property
    def chat_follows_user(self) -> bool:
        return self.chat == CHAT_LANGUAGE_FOLLOW


@dataclass(frozen=True)
class ComputerUseConfig:
    post_action_settle_ms: int = 200


@dataclass(frozen=True)
class SkillSettingConfig:
    enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    llm: LlmConfig
    tts: TtsConfig
    asr: AsrConfig
    realtime_voice: RealtimeVoiceConfig
    ocr: OcrConfig
    computer_use: ComputerUseConfig
    language: LanguageConfig
    skills: dict[str, SkillSettingConfig]
    characters: list[CharacterConfig]

    @property
    def primary_character(self) -> CharacterConfig:
        if not self.characters:
            raise ValueError("config.yaml must define at least one character")
        return self.characters[0]

    def resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.base_dir / candidate

    def skill_enabled(self, skill_id: str) -> bool:
        setting = self.skills.get(normalize_skill_setting_id(skill_id))
        return bool(setting.enabled) if setting is not None else True


def load_app_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    secrets_path = path.resolve().parent / "secrets.yaml"
    if secrets_path.is_file():
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        if isinstance(secrets, dict):
            raw = _deep_merge(raw, secrets)
    raw = _expand_env(raw)
    managed_llm_key = managed_secret(LLM_API_KEY_ENV)
    if managed_llm_key:
        llm_section = raw.setdefault("llm", {})
        if isinstance(llm_section, dict):
            llm_section["api_key"] = managed_llm_key
    managed_realtime_key = managed_secret(QWEN_REALTIME_API_KEY_ENV)
    if managed_realtime_key:
        realtime_section = raw.setdefault("realtime_voice", {})
        if isinstance(realtime_section, dict):
            # The native Core's managed secret is authoritative. A stale
            # secrets.yaml entry must never silently select another account.
            realtime_section["api_key"] = managed_realtime_key

    llm_raw = raw.get("llm") or {}
    tts_raw = raw.get("tts") or {}
    asr_raw = raw.get("asr") or {}
    realtime_voice_raw = raw.get("realtime_voice") or {}
    ocr_raw = raw.get("ocr") or {}
    computer_use_raw = raw.get("computer_use") or {}
    language_raw = raw.get("language") or {}
    skills_raw = raw.get("skills") or {}
    character_rows = raw.get("characters") or []
    characters = [_parse_character(row) for row in character_rows if isinstance(row, dict)]
    # Character packages are the source of truth for the active identity. Keep
    # legacy config.yaml characters as a fallback for older workspaces.
    try:
        from agent_companion.core.character_packages import CharacterPackageManager

        active_character = _parse_character(CharacterPackageManager(path.resolve().parent).active_character_row())
        characters = [active_character, *[row for row in characters if row.name != active_character.name]]
    except Exception:
        pass

    return AppConfig(
        base_dir=path.resolve().parent,
        llm=LlmConfig(
            provider=str(llm_raw.get("provider", "openai_compatible")),
            use_mock=bool(llm_raw.get("use_mock", True)),
            base_url=str(llm_raw.get("base_url", "https://api.openai.com/v1")),
            model=str(llm_raw.get("model", "gpt-4.1-mini")),
            api_key=str(llm_raw.get("api_key", "")),
            temperature=float(llm_raw.get("temperature", 0.8)),
            mock_when_unconfigured=bool(llm_raw.get("mock_when_unconfigured", True)),
            vision_enabled=bool(llm_raw.get("vision_enabled", False)),
            vision_base_url=str(llm_raw.get("vision_base_url", "") or ""),
            vision_model=str(llm_raw.get("vision_model", "") or ""),
            vision_api_key=str(llm_raw.get("vision_api_key", "") or ""),
            expression_enabled=bool(llm_raw.get("expression_enabled", False)),
            expression_base_url=str(llm_raw.get("expression_base_url", "") or ""),
            expression_model=str(llm_raw.get("expression_model", "") or ""),
            expression_api_key=str(llm_raw.get("expression_api_key", "") or ""),
            routes=_parse_model_routes(llm_raw),
        ),
        tts=TtsConfig(
            enabled=bool(tts_raw.get("enabled", False)),
            provider=str(tts_raw.get("provider", "")),
            volume=float(tts_raw.get("volume", 0.85)),
            server_url=str(tts_raw.get("server_url", "http://127.0.0.1:9880/")),
            gpt_sovits_work_path=str(tts_raw.get("gpt_sovits_work_path", "")),
            gpt_sovits_python=str(tts_raw.get("gpt_sovits_python", "") or ""),
            gpt_sovits_streaming_mode=max(1, min(3, int(tts_raw.get("gpt_sovits_streaming_mode", 2) or 2))),
            gpt_sovits_seed=max(-1, min(2**32 - 1, int(tts_raw.get("gpt_sovits_seed", -1)))),
            gpt_sovits_top_k=max(1, min(100, int(tts_raw.get("gpt_sovits_top_k", 15) or 15))),
            gpt_sovits_top_p=max(0.05, min(1.0, float(tts_raw.get("gpt_sovits_top_p", 1.0) or 1.0))),
            gpt_sovits_temperature=max(0.1, min(2.0, float(tts_raw.get("gpt_sovits_temperature", 1.0) or 1.0))),
            gpt_sovits_repetition_penalty=max(
                0.5, min(2.0, float(tts_raw.get("gpt_sovits_repetition_penalty", 1.35) or 1.35))
            ),
            text_lang=str(tts_raw.get("text_lang", "zh") or "zh"),
            prompt_lang=str(tts_raw.get("prompt_lang", "zh") or "zh"),
            speed_factor=float(tts_raw.get("speed_factor", 1.2)),
            fallback_to_system=bool(tts_raw.get("fallback_to_system", False)),
            base_url=str(tts_raw.get("base_url", "") or ""),
            api_key=str(tts_raw.get("api_key", "") or ""),
            model=str(tts_raw.get("model", "") or ""),
            voice=str(tts_raw.get("voice", "") or ""),
            audio_format=str(tts_raw.get("audio_format", "wav") or "wav"),
            timeout_seconds=int(tts_raw.get("timeout_seconds", 60) or 60),
            optimize_text=bool(tts_raw.get("optimize_text", False)),
        ),
        asr=AsrConfig(
            enabled=bool(asr_raw.get("enabled", False)),
            provider=str(asr_raw.get("provider", "") or ""),
            base_url=str(asr_raw.get("base_url", "") or ""),
            model=str(asr_raw.get("model", "") or ""),
            api_key=str(asr_raw.get("api_key", "") or ""),
            language=str(asr_raw.get("language", "zh") or "zh"),
            max_seconds=max(1, int(asr_raw.get("max_seconds", 30) or 30)),
            max_bytes=max(1024, int(asr_raw.get("max_bytes", 12 * 1024 * 1024) or 12 * 1024 * 1024)),
            timeout_seconds=max(1, int(asr_raw.get("timeout_seconds", 30) or 30)),
        ),
        realtime_voice=RealtimeVoiceConfig(
            enabled=bool(realtime_voice_raw.get("enabled", False)),
            provider=str(realtime_voice_raw.get("provider", "qwen_audio") or "qwen_audio"),
            url=str(
                realtime_voice_raw.get("url", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
                or "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
            ),
            model=str(
                realtime_voice_raw.get("model", "qwen-audio-3.0-realtime-flash")
                or "qwen-audio-3.0-realtime-flash"
            ),
            api_key=str(realtime_voice_raw.get("api_key", "") or ""),
            turn_detection=str(realtime_voice_raw.get("turn_detection", "server_vad") or "server_vad"),
            threshold=max(-1.0, min(1.0, float(realtime_voice_raw.get("threshold", 0.5) or 0.5))),
            silence_duration_ms=max(
                200, min(6000, int(realtime_voice_raw.get("silence_duration_ms", 500) or 500))
            ),
            max_history_turns=max(
                1, min(50, int(realtime_voice_raw.get("max_history_turns", 8) or 8))
            ),
            timeout_seconds=min(120, max(1, int(realtime_voice_raw.get("timeout_seconds", 15) or 15))),
        ),
        ocr=OcrConfig(
            timeout_seconds=max(1, int(ocr_raw.get("timeout_seconds", 5) or 5)),
            language=str(ocr_raw.get("language", "chi_sim+eng") or "chi_sim+eng"),
            tesseract_cmd=str(ocr_raw.get("tesseract_cmd", "") or ""),
            tessdata_dir=str(ocr_raw.get("tessdata_dir", "") or ""),
        ),
        computer_use=ComputerUseConfig(
            post_action_settle_ms=max(0, int(computer_use_raw.get("post_action_settle_ms", 200) or 0)),
        ),
        language=_parse_language(language_raw),
        skills=_parse_skill_settings(skills_raw),
        characters=characters,
    )


def load_workspace_config(workspace: Path) -> AppConfig | None:
    """Best-effort config loading for optional runtime consumers."""

    config_path = workspace.resolve() / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        return load_app_config(config_path)
    except Exception:
        return None


def normalize_skill_setting_id(value: str) -> str:
    text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^a-z0-9_.]+", "", text)[:80]


def _parse_language(raw: Any) -> LanguageConfig:
    source = raw if isinstance(raw, dict) else {}
    chat = str(source.get("chat", "zh") or "zh").strip().replace("_", "-").casefold().split("-")[0]
    if chat == "auto":
        chat = CHAT_LANGUAGE_FOLLOW
    return LanguageConfig(
        # The shell is written in Chinese and nothing else is translated yet, so
        # storing another value here would promise a UI that does not exist.
        interface="zh",
        chat=chat if chat in CHAT_LANGUAGE_CHOICES else "zh",
    )


def _parse_skill_settings(raw: Any) -> dict[str, SkillSettingConfig]:
    if not isinstance(raw, dict):
        return {}
    rows: dict[str, SkillSettingConfig] = {}
    for raw_id, raw_value in raw.items():
        skill_id = normalize_skill_setting_id(str(raw_id))
        if not skill_id.startswith("joi.") or not isinstance(raw_value, dict):
            continue
        rows[skill_id] = SkillSettingConfig(enabled=bool(raw_value.get("enabled", True)))
    return rows


def normalize_model_route(value: str = "fast") -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return MODEL_ROUTE_ALIASES.get(normalized, normalized if normalized in MODEL_ROUTES else "fast")


def _parse_model_routes(llm_raw: dict[str, Any]) -> dict[str, ModelRouteConfig]:
    routes: dict[str, ModelRouteConfig] = {}
    raw_routes = llm_raw.get("routes") or llm_raw.get("model_routes") or {}
    if isinstance(raw_routes, dict):
        for raw_name, raw_config in raw_routes.items():
            route = normalize_model_route(str(raw_name))
            if route not in MODEL_ROUTES or not isinstance(raw_config, dict):
                continue
            routes[route] = ModelRouteConfig(
                provider=str(raw_config.get("provider", "") or ""),
                base_url=str(raw_config.get("base_url", "") or ""),
                model=str(raw_config.get("model", "") or ""),
                api_key=str(raw_config.get("api_key", "") or ""),
                enabled=bool(raw_config.get("enabled", True)),
            )
    for route in MODEL_ROUTES:
        model = str(llm_raw.get(f"{route}_model", "") or "")
        base_url = str(llm_raw.get(f"{route}_base_url", "") or "")
        api_key = str(llm_raw.get(f"{route}_api_key", "") or "")
        provider = str(llm_raw.get(f"{route}_provider", "") or "")
        enabled_key = f"{route}_enabled"
        has_flat_route = any((model, base_url, api_key, provider)) or enabled_key in llm_raw
        if has_flat_route:
            routes[route] = ModelRouteConfig(
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
                enabled=bool(llm_raw.get(enabled_key, True)),
            )
    return routes


def _parse_emotion_map(value: Any) -> dict[str, VoiceEmotionConfig]:
    if not isinstance(value, dict):
        return {}
    rows: dict[str, VoiceEmotionConfig] = {}
    for emotion, entry in value.items():
        if not isinstance(entry, dict):
            continue
        rows[str(emotion)] = VoiceEmotionConfig(
            refer_audio_path=str(entry.get("refer_audio_path", "") or ""),
            prompt_text=str(entry.get("prompt_text", "") or ""),
            speech_speed=_optional_number(entry.get("speech_speed")),
            pitch=_optional_number(entry.get("pitch")),
            instructions=str(entry.get("instructions", "") or ""),
        )
    return rows


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_character(row: dict[str, Any]) -> CharacterConfig:
    voice_profiles = [
        VoiceProfileConfig(
            id=str(profile.get("id") or profile.get("name") or index + 1),
            label=str(profile.get("label") or profile.get("id") or f"voice {index + 1}"),
            text_lang=str(profile.get("text_lang", "") or ""),
            prompt_lang=str(profile.get("prompt_lang", "") or ""),
            gpt_model_path=str(profile.get("gpt_model_path", "") or ""),
            sovits_model_path=str(profile.get("sovits_model_path", "") or ""),
            refer_audio_path=str(profile.get("refer_audio_path", "") or ""),
            prompt_text=str(profile.get("prompt_text", "") or ""),
            speech_speed=float(profile["speech_speed"]) if profile.get("speech_speed") is not None else None,
            speech_volume=float(profile["speech_volume"]) if profile.get("speech_volume") is not None else None,
            emotion_map=_parse_emotion_map(profile.get("emotion_map")),
            design=str(profile.get("design", "") or ""),
        )
        for index, profile in enumerate(row.get("voice_profiles") or [])
        if isinstance(profile, dict)
    ]
    sprites = [
        SpriteConfig(
            id=str(sprite.get("id", index + 1)),
            label=str(sprite.get("label", "default")),
            image_path=str(sprite.get("image_path", "")),
            voice_path=str(sprite.get("voice_path", "")),
            voice_text=str(sprite.get("voice_text", "")),
        )
        for index, sprite in enumerate(row.get("sprites") or [])
        if isinstance(sprite, dict)
    ]
    return CharacterConfig(
        name=str(row.get("name", "Joi")).strip(),
        color=str(row.get("color", "#d76f8f")),
        sprite_color=str(row.get("sprite_color", "#224e66")),
        setting=str(row.get("setting", "")),
        gpt_model_path=str(row.get("gpt_model_path", "")),
        sovits_model_path=str(row.get("sovits_model_path", "")),
        refer_audio_path=str(row.get("refer_audio_path", "")),
        prompt_text=str(row.get("prompt_text", "")),
        prompt_lang=str(row.get("prompt_lang", "zh") or "zh"),
        speech_speed=float(row.get("speech_speed", 1.2)),
        speech_volume=float(row.get("speech_volume", 1.0)),
        active_voice_profile=str(row.get("active_voice_profile", "") or ""),
        voice_profiles=voice_profiles,
        sprites=sprites,
        motion_lines={
            str(motion): str(line)
            for motion, line in (row.get("motion_lines") or {}).items()
            if isinstance(row.get("motion_lines"), dict) and str(line or "").strip()
        },
        motion_lines_by_locale={
            str(locale): {str(motion): str(line) for motion, line in (lines or {}).items() if str(line or "").strip()}
            for locale, lines in (row.get("motion_lines_by_locale") or {}).items()
            if isinstance(lines, dict)
        },
    )


def _configured_secret(value: str) -> bool:
    key = (value or "").strip()
    return bool(key and not key.startswith("${") and not key.startswith("%"))


def _model_endpoint_configured(provider: str, base_url: str, model: str, api_key: str) -> bool:
    if not (model or "").strip() or not (base_url or "").strip():
        return False
    if (provider or "").strip().casefold() == "ollama":
        return True
    return _configured_secret(api_key)


def _safe_public_identifier(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    if any(token in text for token in ("sk-", "token", "secret", "api_key", "key=")):
        return "redacted"
    if any(char in text for char in ("/", "\\", ":", "{", "}", "$", "%")):
        return "redacted"
    return re.sub(r"[^a-z0-9_.-]+", "_", text)[:64]


def _safe_public_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered.startswith(("sk-", "${", "%")) or any(token in lowered for token in ("token", "secret", "api_key")):
        return "redacted"
    if any(char in text for char in ("/", "\\", "{", "}")):
        return "redacted"
    return text[:96]
