from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mvp.config import load_app_config
from mvp.source_layout import export_source_like_layout


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
MODEL_SUFFIXES = {".ckpt", ".pth", ".pt", ".bin", ".safetensors", ".index"}


@dataclass(frozen=True)
class ImportedCharacter:
    name: str
    sprite_count: int
    gpt_model_path: str
    sovits_model_path: str
    refer_audio_path: str


@dataclass(frozen=True)
class ImportSummary:
    package_path: Path
    characters: list[ImportedCharacter]


def import_char_package(package_path: Path, config_path: Path) -> ImportSummary:
    """Import a Shinsekai .char package into the MVP config and data layout."""
    package_path = package_path.resolve()
    config_path = config_path.resolve()
    root = config_path.parent
    if not package_path.is_file():
        raise FileNotFoundError(f"角色包不存在: {package_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("characters", [])
    existing_rows = raw.get("characters") if isinstance(raw.get("characters"), list) else []

    with tempfile.TemporaryDirectory(prefix="shinsekai_char_") as temp_name:
        temp_dir = Path(temp_name)
        _safe_extract_zip(package_path, temp_dir)
        character_rows = _read_character_yaml(temp_dir)
        imported_rows: list[dict[str, Any]] = []
        imported_summaries: list[ImportedCharacter] = []

        for index, row in enumerate(character_rows):
            imported = _normalize_character_row(row, root, temp_dir, index)
            if not imported["setting"] and existing_rows:
                imported["setting"] = str(existing_rows[0].get("setting", ""))
            imported_rows.append(imported)
            imported_summaries.append(
                ImportedCharacter(
                    name=str(imported.get("name", "")),
                    sprite_count=len(imported.get("sprites") or []),
                    gpt_model_path=str(imported.get("gpt_model_path", "")),
                    sovits_model_path=str(imported.get("sovits_model_path", "")),
                    refer_audio_path=str(imported.get("refer_audio_path", "")),
                )
            )

    if not imported_rows:
        raise ValueError("角色包没有可导入的角色")

    imported_names = {str(row.get("name", "")).casefold() for row in imported_rows}
    remaining_rows = [
        row for row in existing_rows if str(row.get("name", "")).casefold() not in imported_names
    ]
    raw["characters"] = imported_rows + remaining_rows
    raw.setdefault("tts", {})
    raw["tts"].update(
        {
            "enabled": True,
            "provider": "gpt-sovits",
            "server_url": str(raw["tts"].get("server_url") or "http://127.0.0.1:9880/"),
            "text_lang": imported_rows[0].get("text_lang")
            or imported_rows[0].get("prompt_lang")
            or raw["tts"].get("text_lang")
            or "ja",
            "prompt_lang": imported_rows[0].get("prompt_lang") or "ja",
            "speed_factor": float(imported_rows[0].get("speech_speed") or 1.2),
            "fallback_to_system": False,
        }
    )
    raw.setdefault("app", {})
    raw["app"]["enable_choices"] = False

    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    config = load_app_config(config_path)
    export_source_like_layout(config)
    return ImportSummary(package_path=package_path, characters=imported_summaries)


def _safe_extract_zip(package_path: Path, target_dir: Path) -> None:
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_name = member.filename.replace("\\", "/")
                member_path = Path(member_name)
                if member_path.is_absolute() or member_name.startswith("/") or ".." in member_path.parts:
                    raise ValueError(f"角色包包含不安全路径: {member.filename}")
                target = (target_dir / member_name).resolve()
                try:
                    target.relative_to(target_dir.resolve())
                except ValueError as exc:
                    raise ValueError(f"角色包包含越界路径: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
    except zipfile.BadZipFile as exc:
        raise ValueError("角色包不是有效的 zip/.char 文件") from exc


def _read_character_yaml(temp_dir: Path) -> list[dict[str, Any]]:
    yaml_path = next(iter(temp_dir.rglob("character.yaml")), None)
    if yaml_path is None:
        raise ValueError("角色包缺少 character.yaml")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get("characters") or [data]
    if not isinstance(data, list):
        raise ValueError("character.yaml 格式不正确")
    return [row for row in data if isinstance(row, dict)]


def _normalize_character_row(
    row: dict[str, Any],
    root: Path,
    temp_dir: Path,
    index: int,
) -> dict[str, Any]:
    name = str(row.get("name") or f"导入角色{index + 1}").strip()
    sprite_prefix = str(row.get("sprite_prefix") or _safe_name(name)).strip() or _safe_name(name)
    safe_prefix = _safe_name(sprite_prefix)

    dest_sprite_dir = root / "data" / "sprite" / safe_prefix
    dest_speech_dir = root / "data" / "speech" / safe_prefix
    dest_model_dir = root / "data" / "models" / safe_prefix

    sprite_source_dir = _find_pack_dir(temp_dir, "sprites", sprite_prefix)
    if sprite_source_dir is not None:
        _copy_tree(sprite_source_dir, dest_sprite_dir)
    speech_source_dir = _find_pack_dir(temp_dir, "speech", sprite_prefix) or _find_pack_dir(
        temp_dir, "voices", sprite_prefix
    )
    if speech_source_dir is not None:
        _copy_tree(speech_source_dir, dest_speech_dir)

    sprites = _normalize_sprites(
        row.get("sprites") or [],
        temp_dir,
        sprite_source_dir,
        speech_source_dir,
        dest_sprite_dir,
        dest_speech_dir,
        root,
        row.get("emotion_tags") or "",
    )

    if not sprites and dest_sprite_dir.is_dir():
        labels = _parse_emotion_tags(row.get("emotion_tags") or "")
        for sprite_index, sprite_path in enumerate(sorted(dest_sprite_dir.iterdir())):
            if sprite_path.suffix.lower() in IMAGE_SUFFIXES:
                sprites.append(
                    {
                        "id": str(sprite_index + 1),
                        "label": labels.get(sprite_index + 1, f"立绘 {sprite_index + 1}"),
                        "image_path": _relative_to_root(sprite_path, root),
                        "voice_path": "",
                        "voice_text": "",
                    }
                )

    gpt_model_path = _copy_package_file(
        row.get("gpt_model_path"),
        temp_dir,
        dest_model_dir,
        root,
        allowed_suffixes=MODEL_SUFFIXES,
    )
    sovits_model_path = _copy_package_file(
        row.get("sovits_model_path"),
        temp_dir,
        dest_model_dir,
        root,
        allowed_suffixes=MODEL_SUFFIXES,
    )
    refer_audio_path = _copy_package_file(
        row.get("refer_audio_path"),
        temp_dir,
        dest_model_dir,
        root,
        allowed_suffixes=AUDIO_SUFFIXES | MODEL_SUFFIXES,
    )

    prompt_lang = str(row.get("prompt_lang") or "ja").strip() or "ja"
    text_lang = str(row.get("text_lang") or prompt_lang).strip() or prompt_lang
    prompt_text = str(row.get("prompt_text") or "").strip()
    if not prompt_text:
        for sprite in sprites:
            prompt_text = str(sprite.get("voice_text") or "").strip()
            if prompt_text:
                break

    return {
        "name": name,
        "color": str(row.get("color") or "#f0a08f"),
        "sprite_color": str(row.get("sprite_color") or "#224e66"),
        "gpt_model_path": gpt_model_path,
        "sovits_model_path": sovits_model_path,
        "refer_audio_path": refer_audio_path,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang,
        "text_lang": text_lang,
        "speech_speed": float(row.get("speech_speed") or 1.2),
        "speech_volume": float(row.get("speech_volume") or 1.0),
        "active_voice_profile": text_lang,
        "voice_profiles": [
            {
                "id": text_lang,
                "label": _language_label(text_lang),
                "text_lang": text_lang,
                "prompt_lang": prompt_lang,
                "gpt_model_path": gpt_model_path,
                "sovits_model_path": sovits_model_path,
                "refer_audio_path": refer_audio_path,
                "prompt_text": prompt_text,
                "speech_speed": float(row.get("speech_speed") or 1.2),
                "speech_volume": float(row.get("speech_volume") or 1.0),
            }
        ],
        "setting": str(row.get("character_setting") or row.get("setting") or ""),
        "sprites": sprites,
    }


def _normalize_sprites(
    sprite_rows: list[Any],
    temp_dir: Path,
    sprite_source_dir: Path | None,
    speech_source_dir: Path | None,
    dest_sprite_dir: Path,
    dest_speech_dir: Path,
    root: Path,
    emotion_tags: str,
) -> list[dict[str, str]]:
    labels = _parse_emotion_tags(emotion_tags)
    normalized: list[dict[str, str]] = []
    for index, sprite in enumerate(sprite_rows):
        if isinstance(sprite, str):
            sprite = {"path": sprite}
        if not isinstance(sprite, dict):
            continue
        image_path = _copy_sprite_or_voice(
            sprite.get("image_path") or sprite.get("path"),
            temp_dir,
            sprite_source_dir,
            dest_sprite_dir,
            root,
            IMAGE_SUFFIXES,
        )
        voice_path = _copy_sprite_or_voice(
            sprite.get("voice_path"),
            temp_dir,
            speech_source_dir,
            dest_speech_dir,
            root,
            AUDIO_SUFFIXES,
        )
        normalized.append(
            {
                "id": str(sprite.get("id") or index + 1),
                "label": str(sprite.get("label") or labels.get(index + 1) or f"立绘 {index + 1}"),
                "image_path": image_path,
                "voice_path": voice_path,
                "voice_text": str(sprite.get("voice_text") or ""),
            }
        )
    return normalized


def _parse_emotion_tags(raw: str) -> dict[int, str]:
    labels: dict[int, str] = {}
    for line in str(raw or "").splitlines():
        match = re.search(r"(?:立绘\s*)?(\d+)\s*[：:]\s*(.+)", line.strip())
        if match:
            labels[int(match.group(1))] = match.group(2).strip()
    return labels


def _find_pack_dir(temp_dir: Path, dirname: str, sprite_prefix: str) -> Path | None:
    direct = temp_dir / dirname / sprite_prefix
    if direct.is_dir():
        return direct
    base = temp_dir / dirname
    if not base.is_dir():
        return None
    target = _safe_name(sprite_prefix).casefold()
    for candidate in base.iterdir():
        if candidate.is_dir() and _safe_name(candidate.name).casefold() == target:
            return candidate
    directories = [candidate for candidate in base.iterdir() if candidate.is_dir()]
    return directories[0] if len(directories) == 1 else None


def _copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _copy_sprite_or_voice(
    value: Any,
    temp_dir: Path,
    source_dir: Path | None,
    destination_dir: Path,
    root: Path,
    allowed_suffixes: set[str],
) -> str:
    source = _locate_package_file(value, temp_dir, source_dir, allowed_suffixes)
    if source is None:
        return ""
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return _relative_to_root(destination, root)


def _copy_package_file(
    value: Any,
    temp_dir: Path,
    destination_dir: Path,
    root: Path,
    allowed_suffixes: set[str],
) -> str:
    source = _locate_package_file(value, temp_dir, temp_dir / "models", allowed_suffixes)
    if source is None:
        return ""
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copy2(source, destination)
    return _relative_to_root(destination, root)


def _locate_package_file(
    value: Any,
    temp_dir: Path,
    preferred_dir: Path | None,
    allowed_suffixes: set[str],
) -> Path | None:
    raw = str(value or "").strip()
    candidates: list[Path] = []
    if raw:
        raw_path = Path(raw)
        if raw_path.is_absolute() and raw_path.is_file():
            candidates.append(raw_path)
        candidates.append(temp_dir / raw_path)
        if preferred_dir is not None:
            candidates.append(preferred_dir / raw_path.name)
    if preferred_dir is not None and preferred_dir.is_dir() and raw:
        matches = list(preferred_dir.rglob(Path(raw).name))
        candidates.extend(matches)
    if raw:
        candidates.extend(temp_dir.rglob(Path(raw).name))
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in allowed_suffixes:
            return candidate
    return None


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or ""), flags=re.UNICODE)
    return cleaned.strip("._") or "character"


def _language_label(value: str) -> str:
    labels = {
        "zh": "中文",
        "zh-cn": "中文",
        "ja": "日语",
        "jp": "日语",
        "en": "英语",
    }
    return labels.get(value.strip().casefold(), value.strip() or "默认")
