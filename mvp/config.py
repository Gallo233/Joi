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
    prompt_lang: str = "ja"
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
        return (profile.text_lang if profile and profile.text_lang else fallback) or "ja"

    def voice_prompt_lang(self, fallback: str) -> str:
        profile = self.active_voice
        return (profile.prompt_lang if profile and profile.prompt_lang else self.prompt_lang or fallback) or "ja"

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
        return float(profile.speech_speed if profile and profile.speech_speed is not None else self.speech_speed or fallback)

    def voice_speech_volume(self, fallback: float) -> float:
        profile = self.active_voice
        return float(profile.speech_volume if profile and profile.speech_volume is not None else self.speech_volume or fallback)

    def sprite_label(self, sprite_id: str | int | None) -> str:
        sid = str(sprite_id or "1")
        for sprite in self.sprites:
            if sprite.id == sid:
                return sprite.label
        return self.sprites[0].label if self.sprites else "默认"

    def sprite_image_path(self, sprite_id: str | int | None) -> str:
        sid = str(sprite_id or "1")
        for sprite in self.sprites:
            if sprite.id == sid and sprite.image_path:
                return sprite.image_path
        for sprite in self.sprites:
            if sprite.image_path:
                return sprite.image_path
        return ""


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


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = False
    provider: str = "system"
    volume: float = 0.85
    rate: float = 0.0
    server_url: str = "http://127.0.0.1:9880/"
    gpt_sovits_work_path: str = ""
    text_lang: str = "ja"
    prompt_lang: str = "ja"
    speed_factor: float = 1.2
    fallback_to_system: bool = False


@dataclass(frozen=True)
class AppMetaConfig:
    title: str
    background_color: str = "#15171d"
    accent_color: str = "#7dd3fc"
    window_mode: str = "desktop_assistant"
    show_background: bool = False
    always_on_top: bool = True
    enable_choices: bool = False


@dataclass(frozen=True)
class SceneConfig:
    name: str
    setting: str
    background_image_path: str = ""


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    app: AppMetaConfig
    llm: LlmConfig
    tts: TtsConfig
    characters: list[CharacterConfig]
    scene: SceneConfig
    scenes: list[SceneConfig] = field(default_factory=list)

    def resolve_path(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.base_dir / p

    def character_by_name(self, name: str) -> CharacterConfig | None:
        target = (name or "").strip().casefold()
        for character in self.characters:
            if character.name.casefold() == target:
                return character
        return None

    def scene_by_name(self, name: str) -> SceneConfig | None:
        target = (name or "").strip().casefold()
        if self.scene.name.casefold() == target:
            return self.scene
        for scene in self.scenes:
            if scene.name.casefold() == target:
                return scene
        return None

    @property
    def primary_character(self) -> CharacterConfig:
        if not self.characters:
            raise ValueError("config.yaml must define at least one character")
        return self.characters[0]

    def primary_voice_text_lang(self) -> str:
        return self.primary_character.voice_text_lang(self.tts.text_lang)


def load_app_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    secrets_path = path.resolve().parent / "secrets.yaml"
    if secrets_path.is_file():
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        if isinstance(secrets, dict):
            raw = _deep_merge(raw, secrets)
    raw = _expand_env(raw)

    app_raw = raw.get("app") or {}
    llm_raw = raw.get("llm") or {}
    tts_raw = raw.get("tts") or {}
    scene_raw = raw.get("scene") or {}
    character_rows = raw.get("characters") or []

    characters: list[CharacterConfig] = []
    for row in character_rows:
        voice_profiles = [
            VoiceProfileConfig(
                id=str(profile.get("id") or profile.get("name") or index + 1),
                label=str(profile.get("label") or profile.get("id") or f"语音 {index + 1}"),
                text_lang=str(profile.get("text_lang", "") or ""),
                prompt_lang=str(profile.get("prompt_lang", "") or ""),
                gpt_model_path=str(profile.get("gpt_model_path", "") or ""),
                sovits_model_path=str(profile.get("sovits_model_path", "") or ""),
                refer_audio_path=str(profile.get("refer_audio_path", "") or ""),
                prompt_text=str(profile.get("prompt_text", "") or ""),
                speech_speed=(
                    float(profile["speech_speed"])
                    if profile.get("speech_speed") is not None
                    else None
                ),
                speech_volume=(
                    float(profile["speech_volume"])
                    if profile.get("speech_volume") is not None
                    else None
                ),
            )
            for index, profile in enumerate(row.get("voice_profiles") or [])
            if isinstance(profile, dict)
        ]
        sprites = [
            SpriteConfig(
                id=str(sprite.get("id", index + 1)),
                label=str(sprite.get("label", "默认")),
                image_path=str(sprite.get("image_path", "")),
                voice_path=str(sprite.get("voice_path", "")),
                voice_text=str(sprite.get("voice_text", "")),
            )
            for index, sprite in enumerate(row.get("sprites") or [])
        ]
        characters.append(
            CharacterConfig(
                name=str(row.get("name", "")).strip(),
                color=str(row.get("color", "#ffffff")),
                sprite_color=str(row.get("sprite_color", "#224e66")),
                setting=str(row.get("setting", "")),
                gpt_model_path=str(row.get("gpt_model_path", "")),
                sovits_model_path=str(row.get("sovits_model_path", "")),
                refer_audio_path=str(row.get("refer_audio_path", "")),
                prompt_text=str(row.get("prompt_text", "")),
                prompt_lang=str(row.get("prompt_lang", "ja") or "ja"),
                speech_speed=float(row.get("speech_speed", 1.2)),
                speech_volume=float(row.get("speech_volume", 1.0)),
                active_voice_profile=str(row.get("active_voice_profile", "") or ""),
                voice_profiles=voice_profiles,
                sprites=sprites,
            )
        )

    def scene_from_row(row: dict[str, Any], fallback_name: str) -> SceneConfig:
        return SceneConfig(
            name=str(row.get("name", fallback_name)),
            setting=str(row.get("setting", "")),
            background_image_path=str(row.get("background_image_path", "")),
        )

    active_scene = scene_from_row(scene_raw, "初始场景")
    scene_rows = raw.get("scenes") or []
    scenes = [
        scene_from_row(row, f"场景 {index + 1}")
        for index, row in enumerate(scene_rows)
        if isinstance(row, dict)
    ]
    if not any(scene.name == active_scene.name for scene in scenes):
        scenes.insert(0, active_scene)

    return AppConfig(
        base_dir=path.resolve().parent,
        app=AppMetaConfig(
            title=str(app_raw.get("title", "Shinsekai MVP")),
            background_color=str(app_raw.get("background_color", "#15171d")),
            accent_color=str(app_raw.get("accent_color", "#7dd3fc")),
            window_mode=str(app_raw.get("window_mode", "desktop_assistant")),
            show_background=bool(app_raw.get("show_background", False)),
            always_on_top=bool(app_raw.get("always_on_top", True)),
            enable_choices=bool(app_raw.get("enable_choices", False)),
        ),
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
        ),
        tts=TtsConfig(
            enabled=bool(tts_raw.get("enabled", False)),
            provider=str(tts_raw.get("provider", "system")),
            volume=float(tts_raw.get("volume", 0.85)),
            rate=float(tts_raw.get("rate", 0.0)),
            server_url=str(tts_raw.get("server_url", "http://127.0.0.1:9880/")),
            gpt_sovits_work_path=str(tts_raw.get("gpt_sovits_work_path", "")),
            text_lang=str(tts_raw.get("text_lang", "ja") or "ja"),
            prompt_lang=str(tts_raw.get("prompt_lang", "ja") or "ja"),
            speed_factor=float(tts_raw.get("speed_factor", 1.2)),
            fallback_to_system=bool(tts_raw.get("fallback_to_system", False)),
        ),
        characters=characters,
        scene=active_scene,
        scenes=scenes,
    )
