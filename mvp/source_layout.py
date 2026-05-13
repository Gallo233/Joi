from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mvp.config import AppConfig
from mvp.template import build_system_prompt


def _dump_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def export_source_like_layout(config: AppConfig) -> None:
    """Write a small Shinsekai-like data/config layout for this MVP."""
    root = config.base_dir
    provider = "Deepseek" if "deepseek" in config.llm.base_url.lower() else config.llm.provider

    _dump_yaml(
        root / "data" / "config" / "api.yaml",
        {
            "llm_provider": provider,
            "llm_base_url": config.llm.base_url,
            "llm_model": {provider: config.llm.model},
            "llm_api_key": {provider: config.llm.api_key},
            "is_streaming": False,
            "temperature": config.llm.temperature,
            "repetition_penalty": 1.0,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "max_context_tokens": 128000,
            "tts_provider": config.tts.provider if config.tts.enabled else "none",
            "tts_speed": config.primary_character.voice_speech_speed(config.tts.speed_factor),
            "gpt_sovits_url": config.tts.server_url,
            "gpt_sovits_api_path": config.tts.gpt_sovits_work_path,
            "t2i_provider": "none",
            "t2i_api_url": "",
        },
    )

    _dump_yaml(
        root / "data" / "config" / "system_config.yaml",
        {
            "ui_language": "zh_CN",
            "voice_language": config.primary_voice_text_lang(),
            "asr_provider": "none",
            "theme_color": config.app.accent_color,
            "background_path": config.scene.background_image_path if config.app.show_background else "",
            "base_font_size_px": 32,
            "music_volumn": 30,
            "chat_window_geometry_b64": "",
        },
    )

    _dump_yaml(
        root / "data" / "config" / "characters.yaml",
        [
            {
                "name": character.name,
                "color": character.color,
                "sprite_prefix": character.name,
                "character_setting": character.setting,
                "sprite_scale": 1.0,
                "active_voice_profile": character.active_voice_profile,
                "voice_profiles": [
                    {
                        "id": profile.id,
                        "label": profile.label,
                        "text_lang": profile.text_lang,
                        "prompt_lang": profile.prompt_lang,
                        "gpt_model_path": profile.gpt_model_path,
                        "sovits_model_path": profile.sovits_model_path,
                        "refer_audio_path": profile.refer_audio_path,
                        "prompt_text": profile.prompt_text,
                        "speech_speed": profile.speech_speed,
                        "speech_volume": profile.speech_volume,
                    }
                    for profile in character.voice_profiles
                ],
                "gpt_model_path": character.voice_gpt_model_path(),
                "sovits_model_path": character.voice_sovits_model_path(),
                "refer_audio_path": character.voice_refer_audio_path(),
                "prompt_text": character.voice_prompt_text(),
                "prompt_lang": character.voice_prompt_lang(config.tts.prompt_lang),
                "speech_speed": character.voice_speech_speed(config.tts.speed_factor),
                "speech_volume": character.voice_speech_volume(config.tts.volume),
                "emotion_tags": "\n".join(
                    f"{sprite.id}: {sprite.label}" for sprite in character.sprites
                ),
                "sprites": [
                    {
                        "path": sprite.image_path,
                        "voice_path": sprite.voice_path,
                        "voice_text": sprite.voice_text,
                    }
                    for sprite in character.sprites
                ],
            }
            for character in config.characters
        ],
    )

    _dump_yaml(
        root / "data" / "config" / "background.yaml",
        [
            {
                "name": scene.name,
                "sprite_prefix": scene.name,
                "bg_tags": scene.setting,
                "bgm_tags": "",
                "bgm_list": [],
                "sprites": [{"path": scene.background_image_path}],
            }
            for scene in config.scenes
        ],
    )

    _dump_yaml(root / "data" / "config" / "plugins.yaml", {"plugins": []})
    _dump_yaml(root / "data" / "config" / "mcp.yaml", {"servers": {}})

    template_dir = root / "data" / "character_templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "default.txt").write_text(build_system_prompt(config), encoding="utf-8")
