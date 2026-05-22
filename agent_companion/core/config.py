from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
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


@dataclass(frozen=True)
class SpriteConfig:
    id: str
    label: str
    image_path: str = ""
    voice_path: str = ""
    voice_text: str = ""


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

    @property
    def is_configured(self) -> bool:
        key = (self.api_key or "").strip()
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


class ModelRouter:
    def __init__(self, llm: LlmConfig) -> None:
        self._llm = llm

    def resolve(self, use: str = "text") -> ModelEndpoint:
        if use == "vision" and self._llm.is_vision_configured:
            return ModelEndpoint(
                base_url=self._llm.vision_base_url or self._llm.base_url,
                model=self._llm.vision_model or self._llm.model,
                api_key=self._llm.vision_api_key or self._llm.api_key,
            )
        if use == "expression" and self._llm.is_expression_configured:
            return ModelEndpoint(
                base_url=self._llm.expression_base_url or self._llm.base_url,
                model=self._llm.expression_model or self._llm.model,
                api_key=self._llm.expression_api_key or self._llm.api_key,
            )
        return ModelEndpoint(
            base_url=self._llm.base_url,
            model=self._llm.model,
            api_key=self._llm.api_key,
        )


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = False
    provider: str = ""
    volume: float = 0.85
    server_url: str = "http://127.0.0.1:9880/"
    gpt_sovits_work_path: str = ""
    text_lang: str = "zh"
    prompt_lang: str = "zh"
    speed_factor: float = 1.2
    fallback_to_system: bool = False


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
class OcrConfig:
    timeout_seconds: int = 5
    language: str = "chi_sim+eng"
    tesseract_cmd: str = ""
    tessdata_dir: str = ""


@dataclass(frozen=True)
class ComputerUseConfig:
    post_action_settle_ms: int = 200


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    llm: LlmConfig
    tts: TtsConfig
    asr: AsrConfig
    ocr: OcrConfig
    computer_use: ComputerUseConfig
    characters: list[CharacterConfig]

    @property
    def primary_character(self) -> CharacterConfig:
        if not self.characters:
            raise ValueError("config.yaml must define at least one character")
        return self.characters[0]

    def resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.base_dir / candidate


def load_app_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    secrets_path = path.resolve().parent / "secrets.yaml"
    if secrets_path.is_file():
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        if isinstance(secrets, dict):
            raw = _deep_merge(raw, secrets)
    raw = _expand_env(raw)

    llm_raw = raw.get("llm") or {}
    tts_raw = raw.get("tts") or {}
    asr_raw = raw.get("asr") or {}
    ocr_raw = raw.get("ocr") or {}
    computer_use_raw = raw.get("computer_use") or {}
    character_rows = raw.get("characters") or []
    characters = [_parse_character(row) for row in character_rows if isinstance(row, dict)]

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
        ),
        tts=TtsConfig(
            enabled=bool(tts_raw.get("enabled", False)),
            provider=str(tts_raw.get("provider", "")),
            volume=float(tts_raw.get("volume", 0.85)),
            server_url=str(tts_raw.get("server_url", "http://127.0.0.1:9880/")),
            gpt_sovits_work_path=str(tts_raw.get("gpt_sovits_work_path", "")),
            text_lang=str(tts_raw.get("text_lang", "zh") or "zh"),
            prompt_lang=str(tts_raw.get("prompt_lang", "zh") or "zh"),
            speed_factor=float(tts_raw.get("speed_factor", 1.2)),
            fallback_to_system=bool(tts_raw.get("fallback_to_system", False)),
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
        ocr=OcrConfig(
            timeout_seconds=max(1, int(ocr_raw.get("timeout_seconds", 5) or 5)),
            language=str(ocr_raw.get("language", "chi_sim+eng") or "chi_sim+eng"),
            tesseract_cmd=str(ocr_raw.get("tesseract_cmd", "") or ""),
            tessdata_dir=str(ocr_raw.get("tessdata_dir", "") or ""),
        ),
        computer_use=ComputerUseConfig(
            post_action_settle_ms=max(0, int(computer_use_raw.get("post_action_settle_ms", 200) or 0)),
        ),
        characters=characters,
    )


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
    )
