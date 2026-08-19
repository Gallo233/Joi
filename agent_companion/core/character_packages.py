from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import struct
import tempfile
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from agent_companion.core.character import CharacterHarness
from agent_companion.core.voice import EMOTION_ALIASES


PACKAGE_SCHEMA = "joi.character.v1"
CHARACTER_CARD_V2 = "chara_card_v2"
JOI_CHARACTER_VERSION = "0.1.0"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_FILES = 2_000

# VRM Animation clips. Declarative bone/expression tracks retargeted onto the
# humanoid rig -- data, not code, so they carry no execution risk.
ANIMATION_SUFFIXES = {".vrma"}

# How a character may be drawn. `procedural3d` carries no model file: it is the
# built-in renderer for a character with no authored body. `spine` is named so a
# package can declare it and be told why it will not run -- the Spine runtimes
# are proprietary and need their own licence, so nothing loads one.
MODEL_TYPES = {"static", "live2d", "vrm", "procedural3d", "tachie", "mmd", "spine"}
MODEL_TYPES_WITHOUT_MODEL_FILE = {"static", "procedural3d"}
MMD_SUFFIXES = {".pmx", ".pmd"}
TACHIE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}

# Language tags a package may declare, as `zh`, `ja`, `zh-CN`, `zh-Hant`.
LOCALE_PATTERN = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
DEFAULT_LOCALE = "zh"
MAX_LOCALES = 16

SECRET_FIELD_TOKENS = ("api_key", "apikey", "access_token", "secret", "password")

# Fields that describe a user rather than a character. `post_history_
# instructions` and `example_dialogue` are deliberately absent: both are
# authored Character Card V2 content, not a record of anyone's conversation.
USER_STATE_FIELDS = frozenset(
    {
        "approved_skills",
        "access_grants",
        "affinity",
        "capability_grants",
        "chat_history",
        "chat_log",
        "conversation_history",
        "conversations",
        "granted_permissions",
        "memories",
        "memory_records",
        "memory_vault",
        "message_history",
        "messages",
        "permission_grants",
        "user_memories",
        "user_notes",
        "user_profile",
    }
)

EXECUTABLE_SUFFIXES = {
    ".app",
    ".bat",
    ".cmd",
    ".command",
    ".dll",
    ".dylib",
    ".exe",
    ".jar",
    ".js",
    ".mjs",
    ".cjs",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".so",
    ".wasm",
}


class CharacterPackageError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class CharacterPackageLocation:
    character_id: str
    root: Path
    manifest_path: Path


class CharacterPackageManager:
    """Installs portable, declarative Joi character packages.

    Packages contain character-owned assets and settings only. User chat,
    memories, affinity and provider secrets live outside the package directory.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "data" / "agent_companion" / "characters"
        self.packages_dir = self.root / "packages"
        self.runtime_dir = self.root / "runtime"
        self.state_path = self.root / "state.json"
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_builtin()

    def list(self) -> dict[str, Any]:
        active_id = self.active_id()
        rows: list[dict[str, Any]] = []
        for manifest_path in sorted(self.packages_dir.glob("*/manifest.json")):
            try:
                manifest = self._read_manifest(manifest_path)
                rows.append(self._summary(manifest, manifest_path.parent, active_id=active_id))
            except CharacterPackageError:
                continue
        rows.sort(key=lambda row: (not bool(row.get("active")), str(row.get("name") or "").casefold()))
        return {"ok": True, "active_id": active_id, "characters": rows}

    def detail(self, character_id: str) -> dict[str, Any]:
        manifest, location = self._load(character_id)
        return {
            "ok": True,
            "character": self._public_manifest(
                manifest, location.root, include_content=True, locale=self.active_locale(str(manifest["id"]))
            ),
        }

    def active_id(self) -> str:
        state = self._read_json(self.state_path, fallback={})
        requested = _safe_character_id(state.get("active_id"))
        if requested and self._manifest_path(requested).is_file():
            return requested
        if self._manifest_path("builtin-hikari").is_file():
            return "builtin-hikari"
        first = next(iter(sorted(self.packages_dir.glob("*/manifest.json"))), None)
        return first.parent.name if first else ""

    def active_manifest(self) -> dict[str, Any]:
        character_id = self.active_id()
        if not character_id:
            raise CharacterPackageError("character_not_found", "没有可用的角色包。")
        return self._load(character_id)[0]

    def active_character_row(self) -> dict[str, Any]:
        manifest, location = self._load(self.active_id())
        # Kept before localization: the overlay overwrites the base language's
        # motion lines, so asking the localized copy which languages exist
        # answers with only the one it was just narrowed to.
        authored = manifest
        manifest = self._localized(manifest)
        identity = manifest["identity"]
        voice = manifest.get("voice") or {}
        appearance = manifest.get("appearance") or {}
        expressions = appearance.get("expressions") or []
        sprites = []
        for index, row in enumerate(expressions if isinstance(expressions, list) else []):
            if not isinstance(row, dict):
                continue
            image = self._resolve_asset(location.root, row.get("image"))
            sprites.append(
                {
                    "id": str(row.get("id") or index + 1),
                    "label": str(row.get("label") or row.get("emotion") or "default"),
                    "image_path": str(image) if image else "",
                    "voice_path": str(self._resolve_asset(location.root, row.get("voice_reference")) or ""),
                    "voice_text": str(row.get("voice_text") or ""),
                }
            )
        if not sprites:
            portrait = self._resolve_asset(location.root, appearance.get("portrait"))
            if portrait:
                sprites.append({"id": "1", "label": "neutral", "image_path": str(portrait)})
        profiles = []
        reference_audio = self._resolve_asset(location.root, voice.get("reference_audio"))
        profiles.append(
            {
                "id": str(voice.get("id") or "default"),
                "label": str(voice.get("label") or "默认音色"),
                "text_lang": str(voice.get("language") or "zh"),
                "prompt_lang": str(voice.get("prompt_language") or voice.get("language") or "zh"),
                "refer_audio_path": str(reference_audio) if reference_audio else "",
                "prompt_text": str(voice.get("prompt_text") or ""),
                "gpt_model_path": str(self._resolve_asset(location.root, voice.get("gpt_model")) or ""),
                "sovits_model_path": str(self._resolve_asset(location.root, voice.get("sovits_model")) or ""),
                "speech_speed": _safe_float(voice.get("speed"), 1.0, 0.5, 2.0),
                "speech_volume": _safe_float(voice.get("volume"), 1.0, 0.0, 2.0),
                "emotion_map": self._emotion_map_with_assets(voice.get("emotion_map"), location.root),
                "design": str(voice.get("design") or ""),
            }
        )
        return {
            "name": identity["name"],
            "color": str(appearance.get("accent_color") or "#5b7ff5"),
            "sprite_color": str(appearance.get("sprite_color") or "#224e66"),
            "setting": self._setting_text(manifest),
            "motion_lines": dict(voice.get("motion_lines") or {}),
            # Every language she has lines in, not only the one she is set to
            # speak: what she *says on screen* follows the language the user
            # wrote in, which is independent of the voice they picked.
            "motion_lines_by_locale": self._motion_lines_by_locale(authored),
            "active_voice_profile": profiles[0]["id"],
            "prompt_lang": profiles[0]["prompt_lang"],
            "speech_speed": profiles[0]["speech_speed"],
            "speech_volume": profiles[0]["speech_volume"],
            "voice_profiles": profiles,
            "sprites": sprites,
        }

    def active_harness(self) -> CharacterHarness:
        manifest = self._localized(self.active_manifest())
        identity = manifest["identity"]
        voice = manifest.get("voice") or {}
        return CharacterHarness(
            id=str(manifest["id"]),
            name=str(identity["name"]),
            persona=self._setting_text(manifest),
            tone=str(identity.get("tone") or ""),
            boundaries=[str(item) for item in identity.get("boundaries") or []],
            locale=str(manifest.get("locale") or DEFAULT_LOCALE),
            voice={
                "default_lang": str(voice.get("language") or "zh"),
                "start": str(voice.get("start") or "我开始处理了。"),
                "progress": str(voice.get("progress") or "我正在处理。"),
                "done": str(voice.get("done") or "已经完成了。"),
                "failed": str(voice.get("failed") or "这次没有成功。"),
            },
        )

    def active_runtime_payload(self) -> dict[str, Any]:
        manifest, location = self._load(self.active_id())
        # `_public_manifest` reports both the language in use and the ones on
        # offer, so a language picker needs no second round trip.
        payload = self._public_manifest(
            manifest, location.root, include_content=False, locale=self.active_locale(str(manifest["id"]))
        )
        manifest = self._localized(manifest)
        appearance = manifest.get("appearance") or {}
        payload["portrait_path"] = str(self._resolve_asset(location.root, appearance.get("portrait")) or "")
        payload["background_path"] = str(self._resolve_asset(location.root, appearance.get("background")) or "")
        payload["model_path"] = str(self._resolve_asset(location.root, appearance.get("model")) or "")
        payload["model_type"] = str(appearance.get("model_type") or "static")
        payload["expression_mappings"] = copy.deepcopy(appearance.get("expressions") or [])
        payload["motion_mappings"] = self._motion_mappings_with_assets(manifest, location.root)
        payload["lip_sync"] = copy.deepcopy(appearance.get("lip_sync") or {})
        payload["sprites"] = self.active_character_row().get("sprites") or []
        payload["memory_namespace"] = self.memory_namespace(manifest)
        payload["runtime_state"] = self.runtime_state(str(manifest["id"]))
        return payload

    def _motion_lines_by_locale(self, manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
        """Motion lines in every language the package declares them in.

        Keyed by locale so the caller can answer in whichever language the user
        wrote, rather than in whichever one the character is currently voiced
        in. Reads the raw manifest, not a localized copy, because the point is
        to see all of them at once.
        """

        rows: dict[str, dict[str, str]] = {}
        base = _clean_locale(manifest.get("locale")) or DEFAULT_LOCALE
        base_lines = dict(((manifest.get("voice") or {}).get("motion_lines")) or {})
        if base_lines:
            rows[base] = base_lines
        for locale, overlay in (manifest.get("localizations") or {}).items():
            lines = dict((((overlay or {}).get("voice")) or {}).get("motion_lines") or {})
            if lines:
                rows[str(locale)] = lines
        return rows

    def _emotion_map_with_assets(self, emotion_map: Any, root: Path) -> dict[str, Any]:
        """Resolve each emotion's reference clip to an absolute path.

        An emotion whose clip does not resolve inside the package keeps its
        text and prosody: a missing file should cost the character its happy
        *timbre*, not its happy delivery, and the base voice still speaks the
        line. An entry left with nothing to say is dropped.
        """

        rows: dict[str, Any] = {}
        for emotion, entry in (emotion_map or {}).items() if isinstance(emotion_map, dict) else ():
            if not isinstance(entry, dict):
                continue
            resolved = self._resolve_asset(root, entry.get("reference_audio"))
            row = {
                "refer_audio_path": str(resolved) if resolved else "",
                "prompt_text": str(entry.get("prompt_text") or ""),
                "speech_speed": entry.get("speed"),
                "pitch": entry.get("pitch"),
                "instructions": str(entry.get("instructions") or ""),
            }
            if any(field not in ("", None) for field in row.values()):
                rows[str(emotion)] = row
        return rows

    def _motion_mappings_with_assets(self, manifest: dict[str, Any], root: Path) -> list[dict[str, Any]]:
        """Resolve each motion's authored animation file to an absolute path.

        A VRM motion may name a `.vrma` clip inside the package. The clip is
        the authored version of a semantic motion; a package without one still
        works, because the shell falls back to procedural motion. Anything that
        does not resolve inside the package is dropped rather than passed on.
        """

        rows: list[dict[str, Any]] = []
        for entry in copy.deepcopy((manifest.get("appearance") or {}).get("motions") or []):
            if not isinstance(entry, dict):
                continue
            declared = entry.get("animation")
            entry.pop("animation_path", None)
            if declared:
                resolved = self._resolve_asset(root, declared)
                if resolved and resolved.suffix.casefold() in ANIMATION_SUFFIXES:
                    entry["animation_path"] = str(resolved)
            rows.append(entry)
        return rows

    def runtime_state(self, character_id: str) -> dict[str, Any]:
        safe_id = _safe_character_id(character_id)
        self._load(safe_id)
        path = self.runtime_dir / safe_id / "state.json"
        state = self._read_json(path, fallback={})
        if not isinstance(state, dict) or str(state.get("schema") or "") != "joi.character_runtime.v1":
            state = {
                "schema": "joi.character_runtime.v1",
                "character_id": safe_id,
                "mood": "neutral",
                "affinity": 0,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self._atomic_json(path, state)
        return {
            "mood": _clean_text(state.get("mood") or "neutral", 40),
            "affinity": _safe_float(state.get("affinity"), 0, 0, 100),
            "locale": self.active_locale(safe_id),
        }

    def active_locale(self, character_id: str = "") -> str:
        """Which language this character is currently speaking.

        Held in runtime state rather than in the manifest: it is a thing the
        user switches, and writing it back into the package would rewrite the
        author's file and invalidate its hashes to record a UI preference.
        """

        safe_id = _safe_character_id(character_id or self.active_id())
        manifest, _ = self._load(safe_id)
        allowed = available_locales(manifest)
        path = self.runtime_dir / safe_id / "state.json"
        state = self._read_json(path, fallback={})
        chosen = _clean_locale((state or {}).get("locale")) if isinstance(state, dict) else ""
        # A locale the package has since dropped -- an update that removed a
        # translation -- falls back rather than leaving the character speaking
        # a language it no longer has.
        return chosen if chosen in allowed else allowed[0]

    def set_locale(self, character_id: str, locale: str) -> dict[str, Any]:
        safe_id = _safe_character_id(character_id or self.active_id())
        manifest, _ = self._load(safe_id)
        wanted = _clean_locale(locale)
        allowed = available_locales(manifest)
        if wanted not in allowed:
            raise CharacterPackageError("locale_not_available", "这个角色没有提供该语言。")
        path = self.runtime_dir / safe_id / "state.json"
        state = self._read_json(path, fallback={})
        if not isinstance(state, dict):
            state = {}
        state.update({"schema": "joi.character_runtime.v1", "character_id": safe_id, "locale": wanted, "updated_at": time.time()})
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_json(path, state)
        # `ok` is what every caller checks to tell success from a returned
        # error; without it a switch that fully worked is reported as failed.
        return {"ok": True, "character_id": safe_id, "locale": wanted, "available_locales": allowed}

    def _localized(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """The manifest as the character currently speaks."""

        return _apply_localization(manifest, self.active_locale(str(manifest.get("id") or "")))

    def memory_namespace(self, manifest: dict[str, Any] | None = None) -> str:
        manifest = manifest or self.active_manifest()
        namespace = str((manifest.get("knowledge") or {}).get("memory_namespace") or "isolated").casefold()
        return namespace if namespace in {"isolated", "shared", "disabled"} else "isolated"

    def memory_path(self, manifest: dict[str, Any] | None = None) -> Path:
        manifest = manifest or self.active_manifest()
        if self.memory_namespace(manifest) == "shared":
            return self.workspace / "data" / "agent_companion" / "memory.sqlite3"
        return self.runtime_dir / str(manifest["id"]) / "memory.sqlite3"

    def lore_context(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return only lore entries activated by the current user message."""

        manifest = self.active_manifest()
        lorebook = (manifest.get("knowledge") or {}).get("lorebook") or {}
        entries = lorebook.get("entries") if isinstance(lorebook, dict) else []
        normalized_query = " ".join(str(query or "").casefold().split())
        matches: list[dict[str, Any]] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not bool(entry.get("enabled", True)):
                continue
            keys = [str(key).casefold().strip() for key in entry.get("keys") or [] if str(key).strip()]
            if not bool(entry.get("constant")) and not any(key in normalized_query for key in keys):
                continue
            content = _clean_text(entry.get("content"), 2_000)
            if content:
                matches.append({"name": _clean_text(entry.get("name"), 120), "content": content, "keys": keys})
            if len(matches) >= max(1, min(int(limit), 20)):
                break
        return matches

    def activate(self, character_id: str) -> dict[str, Any]:
        manifest, location = self._load(character_id)
        self._atomic_json(self.state_path, {"active_id": manifest["id"], "updated_at": time.time()})
        return {
            "ok": True,
            "active_id": manifest["id"],
            "character": self._public_manifest(manifest, location.root, include_content=False),
        }

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        character_id = _safe_character_id(payload.get("id")) or f"character-{uuid.uuid4().hex[:10]}"
        if self._manifest_path(character_id).exists():
            raise CharacterPackageError("character_exists", "已经存在同名角色包，请更换名称或复制已有角色。")
        manifest = self._normalize_manifest({**payload, "id": character_id, "schema": PACKAGE_SCHEMA})
        location = self._install_from_form(manifest, payload, replace=False)
        return {"ok": True, "character": self._public_manifest(manifest, location.root, include_content=True)}

    def update(self, character_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current, location = self._load(character_id)
        if bool((current.get("security") or {}).get("built_in")):
            raise CharacterPackageError("builtin_character_readonly", "内置角色不可直接修改，请先复制后再编辑。")
        merged = _deep_merge(copy.deepcopy(current), payload)
        merged["id"] = current["id"]
        merged["schema"] = PACKAGE_SCHEMA
        manifest = self._normalize_manifest(merged)
        updated = self._install_from_form(manifest, payload, replace=True)
        return {"ok": True, "character": self._public_manifest(manifest, updated.root, include_content=True)}

    def duplicate(self, character_id: str, name: str = "") -> dict[str, Any]:
        manifest, location = self._load(character_id)
        duplicate = copy.deepcopy(manifest)
        new_id = f"{_safe_character_id(name) or manifest['id']}-copy-{uuid.uuid4().hex[:6]}"
        duplicate["id"] = new_id
        duplicate["version"] = "1.0.0"
        duplicate["identity"]["name"] = str(name or f"{manifest['identity']['name']} 副本").strip()[:80]
        duplicate.setdefault("security", {})["built_in"] = False
        duplicate["source"] = {"type": "duplicate", "character_id": manifest["id"]}
        target = self.packages_dir / new_id
        shutil.copytree(location.root, target)
        self._write_manifest(target, self._with_hashes(duplicate, target))
        installed = self._read_manifest(target / "manifest.json")
        return {"ok": True, "character": self._public_manifest(installed, target, include_content=True)}

    def uninstall(self, character_id: str) -> dict[str, Any]:
        manifest, location = self._load(character_id)
        if bool((manifest.get("security") or {}).get("built_in")):
            raise CharacterPackageError("builtin_character_required", "内置角色不能卸载。")
        if character_id == self.active_id():
            raise CharacterPackageError("active_character_required", "请先切换到其他角色，再卸载当前角色。")
        shutil.rmtree(location.root)
        return {"ok": True, "uninstalled": character_id}

    def import_package(self, source: str | Path) -> dict[str, Any]:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise CharacterPackageError("package_not_found", "没有找到要导入的角色包文件。")
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise CharacterPackageError("package_too_large", "角色包超过 128 MB，已停止导入。")
        suffix = path.suffix.casefold()
        if suffix in {".joi-character", ".zip"}:
            return self._import_archive(path)
        if suffix == ".json":
            raw = self._read_json(path, fallback=None)
            if not isinstance(raw, dict):
                raise CharacterPackageError("invalid_character_json", "角色 JSON 无法读取。")
            if self._is_character_card(raw):
                manifest = self._from_character_card(raw, source_name=path.name)
            else:
                manifest = self._normalize_manifest(raw)
            manifest["provenance"] = self._observed_provenance(path, package_format="character_card_json" if self._is_character_card(raw) else "character_json")
            location = self._install_manifest(manifest, None, replace=False)
            return self._import_result(manifest, location, warnings=[])
        if suffix == ".png":
            card = self._character_card_from_png(path)
            manifest = self._from_character_card(card, source_name=path.name)
            manifest["provenance"] = self._observed_provenance(path, package_format="character_card_png")
            location = self._install_manifest(manifest, None, replace=False, extra_assets={"appearance.portrait": path})
            return self._import_result(manifest, location, warnings=[])
        raise CharacterPackageError("unsupported_character_format", "仅支持 .joi-character、ZIP、Character Card V2 PNG/JSON。")

    def inspect_package(self, source: str | Path) -> dict[str, Any]:
        """Validate an import and return a safe installation preview."""

        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise CharacterPackageError("package_not_found", "没有找到要预览的角色包文件。")
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise CharacterPackageError("package_too_large", "角色包超过 128 MB，已停止读取。")
        suffix = path.suffix.casefold()
        if suffix in {".joi-character", ".zip"}:
            with tempfile.TemporaryDirectory(prefix="joi-character-preview-") as temp_dir:
                root = Path(temp_dir)
                usability: list[str] = []
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    self._validate_archive_members(members)
                    usability = self._archive_usability_report(members)
                    for member in members:
                        if member.is_dir() or _is_archive_junk(member.filename):
                            continue
                        target = root / PurePosixPath(member.filename)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source_file, target.open("wb") as output:
                            shutil.copyfileobj(source_file, output)
                manifest_path = root / "manifest.json"
                if not manifest_path.is_file():
                    candidates = list(root.glob("*/manifest.json"))
                    if len(candidates) == 1:
                        manifest_path = candidates[0]
                if not manifest_path.is_file():
                    raise CharacterPackageError("missing_character_manifest", "角色包中缺少 manifest.json。")
                manifest = self._normalize_manifest(self._read_json(manifest_path, fallback={}))
                self._scan_package_tree(manifest_path.parent)
                return self._inspect_result(manifest, manifest_path.parent, path, usability=usability)
        if suffix == ".json":
            raw = self._read_json(path, fallback=None)
            if not isinstance(raw, dict):
                raise CharacterPackageError("invalid_character_json", "角色 JSON 无法读取。")
            manifest = self._from_character_card(raw, source_name=path.name) if self._is_character_card(raw) else self._normalize_manifest(raw)
            return self._inspect_result(manifest, None, path)
        if suffix == ".png":
            manifest = self._from_character_card(self._character_card_from_png(path), source_name=path.name)
            return self._inspect_result(manifest, None, path, portrait=path)
        raise CharacterPackageError("unsupported_character_format", "仅支持 .joi-character、ZIP、Character Card V2 PNG/JSON。")

    def export_package(self, character_id: str, destination: str | Path) -> dict[str, Any]:
        """Write a shareable package: the character, and nothing about the user.

        The installed manifest carries provenance Joi observed on *this*
        machine -- a file name and an import time. That describes how this user
        got the character, not the character, so the exported copy is rewritten
        without it. Memory, affinity and chat live outside the package
        directory already, so copying the tree cannot pick them up.
        """

        manifest, location = self._load(character_id)
        destination_path = Path(destination).expanduser().resolve()
        if destination_path.suffix.casefold() == ".joi-character":
            output = destination_path
            output.parent.mkdir(parents=True, exist_ok=True)
        else:
            destination_path.mkdir(parents=True, exist_ok=True)
            output = destination_path / f"{manifest['id']}-{manifest['version']}.joi-character"
        shareable = copy.deepcopy(manifest)
        shareable["provenance"] = _clean_provenance(None)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(location.root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(location.root).as_posix()
                if relative == "manifest.json":
                    archive.writestr(relative, json.dumps(shareable, ensure_ascii=False, indent=2, sort_keys=True))
                    continue
                archive.write(path, relative)
        return {"ok": True, "path": str(output), "character_id": manifest["id"]}

    def check_updates(self, character_id: str) -> dict[str, Any]:
        manifest, _ = self._load(character_id)
        update_url = str((manifest.get("source") or {}).get("update_url") or "").strip()
        if not update_url:
            return {"ok": True, "available": False, "status": "no_update_source", "current_version": manifest["version"]}
        if not _safe_update_url(update_url):
            raise CharacterPackageError("unsafe_update_source", "更新地址必须使用 HTTPS 或本机开发地址。")
        request = urllib.request.Request(update_url, headers={"User-Agent": "Joi/0.1 character-updater"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = response.read(1024 * 1024 + 1)
        except Exception as exc:
            raise CharacterPackageError("update_check_failed", "无法连接角色包更新源。", details={"detail": type(exc).__name__}) from exc
        if len(payload) > 1024 * 1024:
            raise CharacterPackageError("update_manifest_too_large", "更新清单超过安全大小限制。")
        try:
            remote = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise CharacterPackageError("invalid_update_manifest", "更新源没有返回有效 JSON 清单。") from exc
        remote_version = str(remote.get("version") or "") if isinstance(remote, dict) else ""
        package_url = str(remote.get("package_url") or "") if isinstance(remote, dict) else ""
        available = bool(remote_version and _version_tuple(remote_version) > _version_tuple(str(manifest["version"])))
        return {
            "ok": True,
            "available": available,
            "status": "update_available" if available else "up_to_date",
            "current_version": manifest["version"],
            "latest_version": remote_version or manifest["version"],
            "package_url": package_url if available and package_url.startswith(("https://", "http://")) else "",
        }

    def install_update(self, character_id: str, package_url: str = "") -> dict[str, Any]:
        current, _ = self._load(character_id)
        update = self.check_updates(character_id)
        if not update.get("available"):
            return {**update, "updated": False}
        url = str(package_url or update.get("package_url") or "").strip()
        if not _safe_update_url(url):
            raise CharacterPackageError("unsafe_update_package_url", "更新包必须来自 HTTPS 地址或本机开发地址。")
        request = urllib.request.Request(url, headers={"User-Agent": "Joi/0.1 character-updater"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response, tempfile.NamedTemporaryFile(suffix=".joi-character", delete=False) as handle:
                final_url = str(response.geturl() or "")
                if not _safe_update_url(final_url):
                    raise CharacterPackageError("unsafe_update_redirect", "更新地址重定向到了不安全来源。")
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise CharacterPackageError("package_too_large", "更新包超过 128 MB，已停止下载。")
                    handle.write(chunk)
                downloaded = Path(handle.name)
        except CharacterPackageError:
            raise
        except Exception as exc:
            raise CharacterPackageError("update_download_failed", "角色更新包下载失败。", details={"detail": type(exc).__name__}) from exc
        try:
            preview = self.inspect_package(downloaded)
            preview_character = preview.get("preview") if isinstance(preview.get("preview"), dict) else {}
            if str(preview_character.get("id") or "") != character_id:
                raise CharacterPackageError("character_update_id_mismatch", "更新包角色 ID 与当前角色不一致。")
            preview_version = str(preview_character.get("version") or "")
            if _version_tuple(preview_version) <= _version_tuple(str(current["version"])):
                raise CharacterPackageError("character_update_not_newer", "下载的角色包版本没有高于当前版本。")
            result = self._import_archive(downloaded, replace=True, expected_id=character_id)
        finally:
            downloaded.unlink(missing_ok=True)
        installed_version = str((result.get("character") or {}).get("version") or update.get("latest_version") or "")
        return {**result, "updated": True, "previous_version": current["version"], "version": installed_version}

    def _import_archive(self, path: Path, *, replace: bool = False, expected_id: str = "") -> dict[str, Any]:
        warnings: list[str] = []
        with tempfile.TemporaryDirectory(prefix="joi-character-") as temp_dir:
            extract_root = Path(temp_dir)
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                self._validate_archive_members(members)
                for member in members:
                    if member.is_dir():
                        continue
                    target = extract_root / PurePosixPath(member.filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
            manifest_path = extract_root / "manifest.json"
            if not manifest_path.is_file():
                candidates = list(extract_root.glob("*/manifest.json"))
                if len(candidates) == 1:
                    manifest_path = candidates[0]
            if not manifest_path.is_file():
                raise CharacterPackageError("missing_character_manifest", "角色包中缺少 manifest.json。")
            manifest = self._normalize_manifest(self._read_json(manifest_path, fallback={}))
            if expected_id and manifest["id"] != expected_id:
                raise CharacterPackageError("character_update_id_mismatch", "更新包角色 ID 与当前角色不一致。")
            manifest["provenance"] = self._observed_provenance(path, package_format="joi_character_archive")
            source_root = manifest_path.parent
            self._scan_package_tree(source_root)
            location = self._install_manifest(manifest, source_root, replace=replace)
            if not str((manifest.get("security") or {}).get("license") or "").strip():
                warnings.append("角色包没有声明许可证，请确认素材使用权限。")
            result = self._import_result(manifest, location, warnings=warnings)
            result["character"]["version"] = manifest["version"]
            return result

    def _install_from_form(self, manifest: dict[str, Any], payload: dict[str, Any], *, replace: bool) -> CharacterPackageLocation:
        extra_assets: dict[str, Path] = {}
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        appearance = payload.get("appearance") if isinstance(payload.get("appearance"), dict) else {}
        voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else {}
        for key, raw in (
            ("identity.avatar", identity.get("avatar_path")),
            ("appearance.portrait", appearance.get("portrait_path")),
            ("appearance.background", appearance.get("background_path")),
            ("appearance.model", appearance.get("model_path")),
            ("voice.reference_audio", voice.get("reference_audio_path")),
            # Trained GPT-SoVITS weights, so a user who fine-tuned a voice can
            # bind it to the character rather than editing the manifest by
            # hand. Zero-shot needs only the reference clip above; these are
            # the step past it.
            ("voice.gpt_model", voice.get("gpt_model_path")),
            ("voice.sovits_model", voice.get("sovits_model_path")),
        ):
            if str(raw or "").strip():
                extra_assets[key] = Path(str(raw)).expanduser().resolve()
        expressions = appearance.get("expressions") if isinstance(appearance.get("expressions"), list) else []
        for index, row in enumerate(expressions):
            if not isinstance(row, dict):
                continue
            for field in ("image_path", "motion_path"):
                raw = str(row.get(field) or "").strip()
                if raw:
                    extra_assets[f"appearance.expressions.{index}.{field}"] = Path(raw).expanduser().resolve()
        return self._install_manifest(manifest, None, replace=replace, extra_assets=extra_assets)

    def _install_manifest(
        self,
        manifest: dict[str, Any],
        source_root: Path | None,
        *,
        replace: bool,
        extra_assets: dict[str, Path] | None = None,
    ) -> CharacterPackageLocation:
        character_id = str(manifest["id"])
        target = self.packages_dir / character_id
        if target.exists() and not replace:
            raise CharacterPackageError("character_exists", "这个角色已经安装，可以选择复制或更新。")
        staging = self.packages_dir / f".{character_id}-{uuid.uuid4().hex[:8]}.installing"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            if replace and target.is_dir():
                shutil.copytree(target, staging, dirs_exist_ok=True)
            if source_root is not None:
                self._copy_package_tree(source_root, staging)
            self._copy_form_assets(manifest, staging, extra_assets or {})
            normalized_manifest = self._normalize_manifest(manifest)
            asset_report = self._appearance_report(normalized_manifest, staging)
            if asset_report["errors"]:
                raise CharacterPackageError(
                    "invalid_character_assets",
                    str(asset_report["errors"][0]),
                    details={"asset_report": asset_report},
                )
            normalized = self._with_hashes(normalized_manifest, staging)
            self._write_manifest(staging, normalized)
            backup = self.packages_dir / f".{character_id}.backup"
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.rename(backup)
            staging.rename(target)
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        installed = self._read_manifest(target / "manifest.json")
        manifest.clear()
        manifest.update(installed)
        return CharacterPackageLocation(character_id, target, target / "manifest.json")

    def _copy_form_assets(self, manifest: dict[str, Any], staging: Path, assets: dict[str, Path]) -> None:
        for dotted_key, source in assets.items():
            if not source.is_file():
                raise CharacterPackageError("character_asset_not_found", f"没有找到角色素材：{source.name}")
            self._validate_asset_file(source)
            if dotted_key.startswith("appearance.expressions."):
                _, _, index_text, source_field = dotted_key.split(".", 3)
                try:
                    index = int(index_text)
                    row = manifest.setdefault("appearance", {}).setdefault("expressions", [])[index]
                except (ValueError, IndexError, TypeError):
                    continue
                destination = staging / "assets" / "expressions"
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / f"{index + 1}-{_safe_filename(source.name)}"
                shutil.copy2(source, target)
                row["image" if source_field == "image_path" else "motion"] = target.relative_to(staging).as_posix()
                row.pop(source_field, None)
                continue
            section_name, field_name = dotted_key.split(".", 1)
            section = manifest.setdefault(section_name, {})
            if dotted_key == "appearance.model" and str(section.get("model_type") or "").casefold() == "live2d":
                destination = staging / "assets" / "live2d"
                if destination.exists():
                    shutil.rmtree(destination)
                self._copy_package_tree(source.parent, destination)
                section[field_name] = (Path("assets") / "live2d" / source.name).as_posix()
                continue
            folder = {
                "identity.avatar": "avatar",
                "appearance.portrait": "portrait",
                "appearance.background": "background",
                "appearance.model": "model",
                "voice.reference_audio": "voice",
                "voice.gpt_model": "voice",
                "voice.sovits_model": "voice",
            }[dotted_key]
            destination = staging / "assets" / folder
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / _safe_filename(source.name)
            shutil.copy2(source, target)
            section[field_name] = target.relative_to(staging).as_posix()

    def _copy_package_tree(self, source: Path, destination: Path) -> None:
        files = [path for path in source.rglob("*") if path.is_file()]
        if len(files) > MAX_FILES:
            raise CharacterPackageError("package_file_limit", "角色包文件数量超过安全限制。")
        total = 0
        for path in files:
            if path.name == "manifest.json" and path.parent == source:
                continue
            relative = path.relative_to(source)
            if path.is_symlink() or any(part in {"..", ""} for part in relative.parts):
                raise CharacterPackageError("unsafe_package_path", "角色包包含不安全路径。")
            self._validate_asset_file(path)
            total += path.stat().st_size
            if total > MAX_UNPACKED_BYTES:
                raise CharacterPackageError("package_unpacked_limit", "角色包解压后超过安全大小限制。")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def _normalize_manifest(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise CharacterPackageError("invalid_character_manifest", "角色包清单格式不正确。")
        self._reject_secrets(raw)
        identity_raw = raw.get("identity") if isinstance(raw.get("identity"), dict) else raw
        name = _clean_text(identity_raw.get("name"), 80)
        if not name:
            raise CharacterPackageError("missing_character_name", "角色包必须填写角色名称。")
        character_id = _safe_character_id(raw.get("id") or name)
        if not character_id:
            character_id = f"character-{uuid.uuid4().hex[:10]}"
        appearance_raw = raw.get("appearance") if isinstance(raw.get("appearance"), dict) else {}
        voice_raw = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
        knowledge_raw = raw.get("knowledge") if isinstance(raw.get("knowledge"), dict) else {}
        security_raw = raw.get("security") if isinstance(raw.get("security"), dict) else {}
        source_raw = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        capabilities_raw = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
        memory_namespace = str(knowledge_raw.get("memory_namespace") or "isolated").casefold()
        if memory_namespace not in {"isolated", "shared", "disabled"}:
            memory_namespace = "isolated"
        model_type = str(appearance_raw.get("model_type") or "static").casefold()
        if model_type not in MODEL_TYPES:
            model_type = "static"
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "id": character_id,
            "version": _clean_version(raw.get("version") or "1.0.0"),
            "identity": {
                "name": name,
                "avatar": _safe_relative_asset(identity_raw.get("avatar")),
                "persona": _clean_text(identity_raw.get("persona") or identity_raw.get("description"), 12_000),
                "personality": _clean_text(identity_raw.get("personality"), 6_000),
                "scenario": _clean_text(identity_raw.get("scenario"), 6_000),
                "tone": _clean_text(identity_raw.get("tone"), 1_000),
                "boundaries": _clean_string_list(identity_raw.get("boundaries"), 40, 500),
                "system_prompt": _clean_text(identity_raw.get("system_prompt"), 8_000),
                "post_history_instructions": _clean_text(identity_raw.get("post_history_instructions"), 4_000),
                "greeting": _clean_text(identity_raw.get("greeting") or identity_raw.get("first_message"), 4_000),
                "alternate_greetings": _clean_string_list(identity_raw.get("alternate_greetings"), 30, 4_000),
                "example_dialogue": _clean_text(identity_raw.get("example_dialogue") or identity_raw.get("mes_example"), 12_000),
            },
            "appearance": {
                "model_type": model_type,
                "portrait": _safe_relative_asset(appearance_raw.get("portrait")),
                "model": _safe_relative_asset(appearance_raw.get("model")),
                "background": _safe_relative_asset(appearance_raw.get("background")),
                "accent_color": _safe_color(appearance_raw.get("accent_color"), "#5b7ff5"),
                "sprite_color": _safe_color(appearance_raw.get("sprite_color"), "#224e66"),
                "expressions": _clean_mapping_list(appearance_raw.get("expressions"), 80),
                "motions": _clean_mapping_list(appearance_raw.get("motions"), 80),
                "lip_sync": _clean_mapping(appearance_raw.get("lip_sync"), 80),
            },
            "voice": {
                "id": _clean_text(voice_raw.get("id") or "default", 80),
                "label": _clean_text(voice_raw.get("label") or "默认音色", 80),
                "language": _clean_text(voice_raw.get("language") or "zh", 20),
                "prompt_language": _clean_text(voice_raw.get("prompt_language") or voice_raw.get("language") or "zh", 20),
                "speed": _safe_float(voice_raw.get("speed"), 1.0, 0.5, 2.0),
                "volume": _safe_float(voice_raw.get("volume"), 1.0, 0.0, 2.0),
                "reference_audio": _safe_relative_asset(voice_raw.get("reference_audio")),
                "prompt_text": _clean_text(voice_raw.get("prompt_text"), 2_000),
                # How the character sounds, in words. A synthesiser that builds
                # a voice from a description needs no recording at all, which
                # is the only way a package can carry a voice it has no rights
                # to distribute a sample of.
                "design": _clean_text(voice_raw.get("design"), 2_000),
                "gpt_model": _safe_relative_asset(voice_raw.get("gpt_model")),
                "sovits_model": _safe_relative_asset(voice_raw.get("sovits_model")),
                "emotion_map": _clean_emotion_map(voice_raw.get("emotion_map")),
                # What she says while performing each motion. Without these a
                # character speaks Joi's default mascot lines, which is jarring
                # for one written as quiet or as formal.
                "motion_lines": _clean_motion_lines(voice_raw.get("motion_lines")),
            },
            "knowledge": {
                "lorebook": _clean_lorebook(knowledge_raw.get("lorebook")),
                "memory_namespace": memory_namespace,
            },
            "capabilities": {
                "requested_skills": _clean_string_list(capabilities_raw.get("requested_skills"), 80, 120),
                # A portable package may request capabilities but can never
                # approve them on the user's behalf.
                "approved_skills": [],
            },
            "creator": {
                "name": _clean_text((raw.get("creator") or {}).get("name") if isinstance(raw.get("creator"), dict) else raw.get("creator"), 100),
                "notes": _clean_text((raw.get("creator") or {}).get("notes") if isinstance(raw.get("creator"), dict) else raw.get("creator_notes"), 4_000),
            },
            "source": {
                # Author-declared. Kept because it is how updates are found,
                # but never treated as evidence of where the bytes came from.
                "type": _clean_text(source_raw.get("type") or "local", 40),
                "url": _clean_url(source_raw.get("url")),
                "update_url": _clean_url(source_raw.get("update_url")),
            },
            # Observed by Joi at import. Preserved across normalization so a
            # reinstall does not quietly relabel where a character came from.
            "provenance": _clean_provenance(raw.get("provenance")),
            "security": {
                "license": _clean_text(security_raw.get("license") or "Unknown", 160),
                "compatibility": _clean_text(security_raw.get("compatibility") or f">={JOI_CHARACTER_VERSION}", 80),
                "built_in": bool(security_raw.get("built_in", False)),
                "file_hashes": _clean_mapping(security_raw.get("file_hashes"), MAX_FILES),
                "package_hash": _clean_text(security_raw.get("package_hash"), 128),
            },
            # The language the sections above are written in. A package that
            # never says gets the default rather than being treated as
            # language-less, because every other locale is defined as an
            # overlay onto this one.
            "locale": _clean_locale(raw.get("locale")) or DEFAULT_LOCALE,
            "localizations": _clean_localizations(raw.get("localizations")),
            "extensions": _clean_mapping(raw.get("extensions"), 200),
        }
        return manifest

    def _from_character_card(self, card: dict[str, Any], *, source_name: str) -> dict[str, Any]:
        data = card.get("data") if isinstance(card.get("data"), dict) else card
        if not isinstance(data, dict):
            raise CharacterPackageError("invalid_character_card", "Character Card V2 数据格式不正确。")
        name = _clean_text(data.get("name"), 80)
        if not name:
            raise CharacterPackageError("missing_character_name", "Character Card 没有角色名称。")
        extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
        joi_extension = extensions.get("joi") if isinstance(extensions.get("joi"), dict) else {}
        voice = joi_extension.get("voice") if isinstance(joi_extension.get("voice"), dict) else {}
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "id": f"{_safe_character_id(name)}-{uuid.uuid4().hex[:6]}",
            "version": _clean_version(data.get("character_version") or "1.0.0"),
            "identity": {
                "name": name,
                "persona": data.get("description") or "",
                "personality": data.get("personality") or "",
                "scenario": data.get("scenario") or "",
                "system_prompt": data.get("system_prompt") or "",
                "post_history_instructions": data.get("post_history_instructions") or "",
                "greeting": data.get("first_mes") or "",
                "alternate_greetings": data.get("alternate_greetings") or [],
                "example_dialogue": data.get("mes_example") or "",
                "tone": joi_extension.get("tone") or "",
                "boundaries": joi_extension.get("boundaries") or [],
            },
            "appearance": {
                "model_type": "static",
                "accent_color": joi_extension.get("accent_color") or "#5b7ff5",
            },
            "voice": voice,
            "knowledge": {
                "lorebook": data.get("character_book") or {},
                "memory_namespace": "isolated",
            },
            "creator": {"name": data.get("creator") or "", "notes": data.get("creator_notes") or ""},
            "source": {"type": "character_card_v2", "url": "", "update_url": ""},
            "security": {"license": "Unknown", "compatibility": f">={JOI_CHARACTER_VERSION}"},
            "extensions": {"character_card_v2": extensions, "source_file": source_name},
        }
        return self._normalize_manifest(manifest)

    @staticmethod
    def _is_character_card(raw: dict[str, Any]) -> bool:
        spec = str(raw.get("spec") or "").casefold()
        data = raw.get("data")
        return spec == CHARACTER_CARD_V2 or (isinstance(data, dict) and "first_mes" in data and "description" in data)

    @staticmethod
    def _character_card_from_png(path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CharacterPackageError("invalid_character_png", "文件不是有效 PNG Character Card。")
        cursor = 8
        fields: dict[str, str] = {}
        while cursor + 12 <= len(data):
            length = struct.unpack(">I", data[cursor : cursor + 4])[0]
            chunk_type = data[cursor + 4 : cursor + 8]
            chunk = data[cursor + 8 : cursor + 8 + length]
            cursor += 12 + length
            if chunk_type == b"tEXt" and b"\x00" in chunk:
                key, value = chunk.split(b"\x00", 1)
                fields[key.decode("latin1", "ignore")] = value.decode("latin1", "ignore")
            elif chunk_type == b"iTXt":
                parts = chunk.split(b"\x00", 5)
                if len(parts) == 6 and parts[1] == b"\x00":
                    fields[parts[0].decode("utf-8", "ignore")] = parts[5].decode("utf-8", "ignore")
            if chunk_type == b"IEND":
                break
        encoded = fields.get("chara") or fields.get("ccv3") or ""
        if not encoded:
            raise CharacterPackageError("missing_character_card_metadata", "PNG 中没有 Character Card V2 元数据。")
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            card = json.loads(decoded)
        except Exception as exc:
            raise CharacterPackageError("invalid_character_card_metadata", "PNG 角色卡元数据无法解析。") from exc
        if not isinstance(card, dict):
            raise CharacterPackageError("invalid_character_card", "PNG 角色卡内容格式不正确。")
        return card

    def _validate_archive_members(self, members: list[zipfile.ZipInfo]) -> None:
        if len(members) > MAX_FILES:
            raise CharacterPackageError("package_file_limit", "角色包文件数量超过安全限制。")
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or member.filename.startswith(("/", "\\")):
                raise CharacterPackageError("unsafe_package_path", "角色包包含目录穿越路径。")
            mode = member.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise CharacterPackageError("unsafe_package_symlink", "角色包不能包含符号链接。")
            if member.file_size > MAX_MEMBER_BYTES:
                raise CharacterPackageError("package_member_too_large", "角色包中的单个文件超过安全限制。")
            total += member.file_size
            if total > MAX_UNPACKED_BYTES:
                raise CharacterPackageError("package_unpacked_limit", "角色包解压后超过安全大小限制。")
            self._validate_asset_name(path.name)

    @staticmethod
    def _archive_usability_report(members: list[zipfile.ZipInfo]) -> list[str]:
        """Why a structurally safe package would still look broken once installed.

        The trust checks above decide whether a package may be opened at all.
        These decide whether it will work, and they exist because real archives
        fail in ways that look like a renderer bug to whoever imported one:

        - macOS zips carry `__MACOSX/` and `._` resource forks;
        - two files with the same basename in different folders collide in the
          loaders that key assets by name, and one silently wins;
        - a model file referencing `Texture.png` while the archive holds
          `texture.png` loads on a case-insensitive filesystem and fails
          everywhere else.

        These are reported, never raised: a package that is merely awkward is
        still the user's to install.
        """

        notes: list[str] = []
        names = [member.filename for member in members if not member.is_dir()]
        junk = sum(1 for name in names if _is_archive_junk(name))
        if junk:
            notes.append(f"包内有 {junk} 个 macOS 打包残留文件（__MACOSX / ._），导入时会被忽略。")

        by_basename: dict[str, list[str]] = {}
        for name in names:
            if _is_archive_junk(name):
                continue
            by_basename.setdefault(PurePosixPath(name).name.casefold(), []).append(name)
        collisions = sorted(base for base, paths in by_basename.items() if len(paths) > 1)
        if collisions:
            shown = "、".join(collisions[:3])
            notes.append(f"有 {len(collisions)} 组同名文件分散在不同目录（例如 {shown}），按文件名加载素材时只有一个会生效。")

        # A name that decoded as mojibake usually means a legacy-codepage zip,
        # which is what VTube Studio and older tools produce.
        if any(_looks_like_mojibake(name) for name in names):
            notes.append("包内有文件名疑似使用非 UTF-8 编码（常见于 VTube Studio 导出），素材可能找不到。")
        return notes

    def _scan_package_tree(self, root: Path) -> None:
        files = [path for path in root.rglob("*") if path.is_file()]
        if len(files) > MAX_FILES:
            raise CharacterPackageError("package_file_limit", "角色包文件数量超过安全限制。")
        for path in files:
            if path.name != "manifest.json":
                self._validate_asset_file(path)

    def _infer_live2d_settings(self, root: Path) -> str:
        """Write a minimal model3.json for a package that shipped only a .moc3.

        Returns the package-relative path to the settings file, or "" when there
        is nothing to infer from. Exactly one .moc3 is required: with several,
        guessing which one is the character would be worse than saying no.
        """

        existing = sorted(root.rglob("*.model3.json"))
        if existing:
            try:
                return existing[0].relative_to(root).as_posix()
            except ValueError:
                return ""
        moc_files = [path for path in sorted(root.rglob("*.moc3")) if path.is_file()]
        if len(moc_files) != 1:
            return ""
        moc = moc_files[0]
        try:
            header = moc.read_bytes()[:4]
        except OSError:
            return ""
        # A file named .moc3 that does not start with MOC3 is not a model, and
        # writing settings for it would turn a clear failure into a puzzling one.
        if header != b"MOC3":
            return ""
        textures = [
            path.relative_to(moc.parent).as_posix()
            for path in sorted(moc.parent.rglob("*.png"))
            if path.is_file()
        ]
        settings = {
            "Version": 3,
            "FileReferences": {"Moc": moc.name, "Textures": textures},
            "Groups": [
                {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
                {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
            ],
        }
        target = moc.with_suffix("").with_suffix(".model3.json")
        if target.exists():
            return ""
        try:
            target.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return ""
        try:
            return target.relative_to(root).as_posix()
        except ValueError:
            return ""

    def _appearance_report(
        self,
        manifest: dict[str, Any],
        root: Path | None,
        *,
        portrait_override: Path | None = None,
    ) -> dict[str, Any]:
        """Validate render assets without ever executing package content."""

        appearance = manifest.get("appearance") if isinstance(manifest.get("appearance"), dict) else {}
        model_type = str((appearance or {}).get("model_type") or "static").casefold()
        # A manifest can be hand-edited after install, so an unknown format is
        # reported as what it will actually be rendered as rather than repeated
        # back as if the stage knew it.
        if model_type not in MODEL_TYPES:
            model_type = "static"
        errors: list[str] = []
        warnings: list[str] = []
        checks: list[dict[str, Any]] = []

        def record(name: str, ok: bool, detail: str, *, required: bool = False) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail, "required": required})
            if ok:
                return
            (errors if required else warnings).append(detail)

        compatibility = str((manifest.get("security") or {}).get("compatibility") or f">={JOI_CHARACTER_VERSION}")
        record(
            "compatibility",
            _compatibility_supported(compatibility),
            f"角色包要求 Joi 兼容版本 {compatibility}，当前为 {JOI_CHARACTER_VERSION}。",
            required=True,
        )

        portrait_ref = str((appearance or {}).get("portrait") or "").strip()
        portrait = portrait_override if portrait_override and portrait_override.is_file() else None
        if portrait is None and root is not None and portrait_ref:
            portrait = self._resolve_asset(root, portrait_ref)
        if portrait_ref or portrait_override is not None:
            record("portrait", bool(portrait and portrait.is_file()), "角色立绘文件不存在。", required=bool(portrait_ref))
        elif model_type == "static":
            record("portrait", False, "静态角色没有立绘，将使用文字占位。")

        model_ref = str((appearance or {}).get("model") or "").strip()
        model_path = self._resolve_asset(root, model_ref) if root is not None and model_ref else None
        if model_type == "live2d" and not model_path and root is not None:
            # A settings file is how a Live2D package is supposed to describe
            # itself, but plenty of them are shared as a bare .moc3 plus a
            # texture folder. Inferring settings from what is actually there
            # beats refusing a model the renderer could have drawn.
            inferred = self._infer_live2d_settings(root)
            if inferred:
                model_ref = inferred
                model_path = self._resolve_asset(root, inferred)
                appearance = {**(appearance or {}), "model": inferred}
                manifest.setdefault("appearance", {})["model"] = inferred
                record("live2d.inferred", True, "包内没有 .model3.json，已按目录里的 .moc3 推断出模型设置。")
        if model_type == "live2d":
            record("live2d.model", bool(model_ref), "Live2D 角色必须选择 .model3.json。", required=True)
            if model_ref:
                record("live2d.file", bool(model_path), "Live2D model3.json 文件不存在。", required=True)
            if model_path is not None:
                valid_suffix = model_path.name.casefold().endswith(".model3.json")
                record("live2d.format", valid_suffix, "当前运行时只支持 Cubism .model3.json。", required=True)
                if valid_suffix:
                    model_json = self._read_json(model_path, fallback=None)
                    valid_json = isinstance(model_json, dict)
                    record("live2d.json", valid_json, "Live2D model3.json 无法解析。", required=True)
                    if valid_json:
                        references = model_json.get("FileReferences") if isinstance(model_json.get("FileReferences"), dict) else {}
                        moc = str((references or {}).get("Moc") or "").strip()
                        textures = (references or {}).get("Textures") if isinstance((references or {}).get("Textures"), list) else []
                        record("live2d.moc", bool(moc), "Live2D 模型缺少 Moc 引用。", required=True)
                        record("live2d.textures", bool(textures), "Live2D 模型没有声明纹理。", required=True)
                        for label, relative in [("Moc", moc), *[(f"Texture {index + 1}", value) for index, value in enumerate(textures)]]:
                            if not str(relative or "").strip():
                                continue
                            asset = self._model_child_asset(root, model_path, relative)
                            record(
                                f"live2d.reference.{label}",
                                bool(asset),
                                f"Live2D 缺少引用文件：{relative}",
                                required=True,
                            )
        elif model_type == "vrm":
            record("vrm.model", bool(model_ref), "VRM 角色必须选择 .vrm 模型。", required=True)
            if model_ref:
                record("vrm.file", bool(model_path), "VRM 模型文件不存在。", required=True)
            if model_path is not None:
                valid_suffix = model_path.suffix.casefold() == ".vrm"
                record("vrm.format", valid_suffix, "VRM 模型必须使用 .vrm 扩展名。", required=True)
                valid_glb = False
                if valid_suffix:
                    try:
                        header = model_path.read_bytes()[:12]
                        valid_glb = len(header) == 12 and header[:4] == b"glTF" and struct.unpack("<I", header[4:8])[0] == 2
                    except OSError:
                        valid_glb = False
                record("vrm.glb", valid_glb, "VRM 文件不是有效的 glTF 2.0 二进制模型。", required=True)

        elif model_type == "mmd":
            record("mmd.model", bool(model_ref), "MMD 角色必须选择 .pmx 或 .pmd 模型。", required=True)
            if model_ref:
                record("mmd.file", bool(model_path), "MMD 模型文件不存在。", required=True)
            if model_path is not None:
                record(
                    "mmd.format",
                    model_path.suffix.casefold() in MMD_SUFFIXES,
                    "MMD 模型必须使用 .pmx 或 .pmd 扩展名。",
                    required=True,
                )
            # Physics is not shipped, so a model authored around skirt or hair
            # simulation will stand stiffer here than in MMD itself. Saying so
            # is better than letting it look broken.
            record("mmd.physics", False, "MMD 物理未启用：裙摆与头发不会摆动。")
        elif model_type == "tachie":
            record("tachie.model", bool(model_ref), "立绘角色必须选择一张基础图片。", required=True)
            if model_ref:
                record("tachie.file", bool(model_path), "立绘图片文件不存在。", required=True)
            if model_path is not None:
                record(
                    "tachie.format",
                    model_path.suffix.casefold() in TACHIE_SUFFIXES,
                    "立绘图片必须是 PNG / WebP / JPEG。",
                    required=True,
                )
        elif model_type == "spine":
            # Declarable, never runnable: the runtime licence is not ours to
            # grant, so this fails at validation rather than at mount.
            record(
                "spine.licence",
                False,
                "Spine 运行时是专有软件，需要单独授权，当前版本不支持加载 Spine 角色。",
                required=True,
            )

        background_ref = str((appearance or {}).get("background") or "").strip()
        if background_ref and root is not None:
            record("background", bool(self._resolve_asset(root, background_ref)), "角色背景文件不存在。")

        return {
            "model_type": model_type,
            "status": "invalid" if errors else "ready" if not warnings else "warning",
            "installable": not errors,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def _model_child_asset(root: Path | None, model_path: Path, value: Any) -> Path | None:
        relative = _safe_relative_asset(value)
        if root is None or not relative:
            return None
        candidate = (model_path.parent / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _validate_asset_file(self, path: Path) -> None:
        if path.is_symlink():
            raise CharacterPackageError("unsafe_package_symlink", "角色素材不能使用符号链接。")
        if path.stat().st_size > MAX_MEMBER_BYTES:
            raise CharacterPackageError("package_member_too_large", f"素材 {path.name} 超过安全大小限制。")
        self._validate_asset_name(path.name)

    @staticmethod
    def _validate_asset_name(name: str) -> None:
        lowered = name.casefold()
        if any(lowered.endswith(suffix) for suffix in EXECUTABLE_SUFFIXES):
            raise CharacterPackageError("executable_content_blocked", f"角色包包含不允许执行的文件：{name}")

    def _load(self, character_id: str) -> tuple[dict[str, Any], CharacterPackageLocation]:
        safe_id = _safe_character_id(character_id)
        path = self._manifest_path(safe_id)
        if not safe_id or not path.is_file():
            raise CharacterPackageError("character_not_found", "没有找到这个角色。")
        manifest = self._read_manifest(path)
        return manifest, CharacterPackageLocation(safe_id, path.parent, path)

    def _read_manifest(self, path: Path) -> dict[str, Any]:
        raw = self._read_json(path, fallback=None)
        if not isinstance(raw, dict):
            raise CharacterPackageError("invalid_character_manifest", "角色包清单无法读取。")
        return self._normalize_manifest(raw)

    def _summary(self, manifest: dict[str, Any], root: Path, *, active_id: str) -> dict[str, Any]:
        # Each character remembers its own language, so the library lists every
        # one as it currently speaks rather than as its manifest was written.
        payload = self._public_manifest(manifest, root, include_content=False, locale=self.active_locale(str(manifest["id"])))
        payload["active"] = manifest["id"] == active_id
        return payload

    def _public_manifest(self, manifest: dict[str, Any], root: Path, *, include_content: bool, locale: str = "") -> dict[str, Any]:
        # Shown as the character currently speaks, but `available_locales` is
        # read from the manifest as authored: the overlay rewrites `locale` to
        # the chosen one, so asking the localized copy what else it offers
        # would report only the language it is already in.
        active = _clean_locale(locale) or _clean_locale(manifest.get("locale")) or DEFAULT_LOCALE
        choices = available_locales(manifest)
        display = _apply_localization(manifest, active)
        identity = display["identity"]
        appearance = display.get("appearance") or {}
        portrait = self._resolve_asset(root, appearance.get("portrait"))
        avatar = self._resolve_asset(root, identity.get("avatar")) or portrait
        payload: dict[str, Any] = {
            "id": manifest["id"],
            "name": identity["name"],
            "version": manifest["version"],
            "creator": copy.deepcopy(manifest.get("creator") or {}),
            "model_type": appearance.get("model_type") or "static",
            "avatar_path": str(avatar) if avatar else "",
            "portrait_path": str(portrait) if portrait else "",
            "accent_color": appearance.get("accent_color") or "#5b7ff5",
            "memory_namespace": self.memory_namespace(manifest),
            "license": str((manifest.get("security") or {}).get("license") or "Unknown"),
            "built_in": bool((manifest.get("security") or {}).get("built_in")),
            "requested_skills": list((manifest.get("capabilities") or {}).get("requested_skills") or []),
            "package_hash": str((manifest.get("security") or {}).get("package_hash") or ""),
            "provenance": copy.deepcopy(manifest.get("provenance") or {}),
            "has_update_source": bool((manifest.get("source") or {}).get("update_url")),
            "greeting": str(identity.get("greeting") or ""),
            "tone": str(identity.get("tone") or ""),
            "locale": active,
            "available_locales": choices,
        }
        if include_content:
            payload["manifest"] = copy.deepcopy(manifest)
        return payload

    def _with_hashes(self, manifest: dict[str, Any], root: Path) -> dict[str, Any]:
        file_hashes: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            relative = path.relative_to(root).as_posix()
            file_hashes[relative] = _sha256_file(path)
        manifest = copy.deepcopy(manifest)
        security = manifest.setdefault("security", {})
        security["file_hashes"] = file_hashes
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        security["package_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return manifest

    def _setting_text(self, manifest: dict[str, Any]) -> str:
        identity = manifest["identity"]
        sections = [
            str(identity.get("persona") or ""),
            f"性格：{identity.get('personality')}" if identity.get("personality") else "",
            f"场景：{identity.get('scenario')}" if identity.get("scenario") else "",
            f"语气：{identity.get('tone')}" if identity.get("tone") else "",
            f"边界：{'；'.join(identity.get('boundaries') or [])}" if identity.get("boundaries") else "",
            str(identity.get("system_prompt") or ""),
            str(identity.get("post_history_instructions") or ""),
        ]
        return "\n".join(section for section in sections if section).strip()[:24_000]

    def _resolve_asset(self, root: Path, value: Any) -> Path | None:
        relative = _safe_relative_asset(value)
        if not relative:
            return None
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _bootstrap_builtin(self) -> None:
        target = self.packages_dir / "builtin-hikari"
        manifest_path = target / "manifest.json"
        if manifest_path.is_file():
            self._ensure_builtin_avatar(target, manifest_path)
            if not self.state_path.is_file():
                self._atomic_json(self.state_path, {"active_id": "builtin-hikari", "updated_at": time.time()})
            return
        character_yaml = self.workspace / "agent_companion" / "config" / "default_character.yaml"
        config_yaml = self.workspace / "config.yaml"
        character_raw = yaml.safe_load(character_yaml.read_text(encoding="utf-8")) if character_yaml.is_file() else {}
        config_raw = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) if config_yaml.is_file() else {}
        config_character = ((config_raw or {}).get("characters") or [{}])[0]
        target.mkdir(parents=True, exist_ok=True)
        portrait_source = self.workspace / "agent_companion" / "web_widget" / "assets" / "joi-body.png"
        avatar_source = self.workspace / "agent_companion" / "web_widget" / "assets" / "joi-front-head.png"
        portrait = ""
        avatar = ""
        if portrait_source.is_file():
            (target / "assets" / "portrait").mkdir(parents=True, exist_ok=True)
            shutil.copy2(portrait_source, target / "assets" / "portrait" / portrait_source.name)
            portrait = "assets/portrait/joi-body.png"
        if avatar_source.is_file():
            (target / "assets" / "avatar").mkdir(parents=True, exist_ok=True)
            shutil.copy2(avatar_source, target / "assets" / "avatar" / avatar_source.name)
            avatar = "assets/avatar/joi-front-head.png"
        model_source = self.workspace / "agent_companion" / "shell" / "public" / "live2d" / "hiyori"
        model = ""
        if (model_source / "hiyori.model3.json").is_file():
            shutil.copytree(model_source, target / "assets" / "live2d", dirs_exist_ok=True)
            model = "assets/live2d/hiyori.model3.json"
        style = character_raw.get("style") if isinstance(character_raw, dict) else {}
        voice = character_raw.get("voice") if isinstance(character_raw, dict) else {}
        manifest = self._normalize_manifest(
            {
                "schema": PACKAGE_SCHEMA,
                "id": "builtin-hikari",
                "version": "1.0.0",
                "identity": {
                    "name": str(character_raw.get("name") or config_character.get("name") or "Joi"),
                    "avatar": avatar,
                    "persona": str(character_raw.get("persona") or config_character.get("setting") or ""),
                    "tone": str((style or {}).get("tone") or "温和、清醒、自然"),
                    "boundaries": list((style or {}).get("boundaries") or []),
                    "greeting": "我在。今天想一起做点什么？",
                },
                "appearance": {
                    "model_type": "live2d" if model else "static",
                    "portrait": portrait,
                    "model": model,
                    "accent_color": "#5b7ff5",
                },
                "voice": {**(voice or {}), "language": str((voice or {}).get("default_lang") or "zh")},
                # Preserve the user's existing Joi memory database for the
                # built-in character; newly created/imported roles default to
                # isolated namespaces.
                "knowledge": {"lorebook": {"entries": []}, "memory_namespace": "shared"},
                "creator": {"name": "Joi", "notes": "Joi 原创默认角色"},
                "source": {"type": "builtin"},
                "security": {"license": "Joi Original", "compatibility": f">={JOI_CHARACTER_VERSION}", "built_in": True},
            }
        )
        self._write_manifest(target, self._with_hashes(manifest, target))
        if not self.state_path.is_file():
            self._atomic_json(self.state_path, {"active_id": "builtin-hikari", "updated_at": time.time()})

    def _ensure_builtin_avatar(self, target: Path, manifest_path: Path) -> None:
        """Add the dedicated head portrait to installations created before v0.1."""

        source = self.workspace / "agent_companion" / "web_widget" / "assets" / "joi-front-head.png"
        if not source.is_file():
            return
        relative = Path("assets/avatar/joi-front-head.png")
        destination = target / relative
        raw = self._read_json(manifest_path, fallback={})
        if not isinstance(raw, dict):
            return
        current = str((raw.get("identity") or {}).get("avatar") or "")
        if current == relative.as_posix() and destination.is_file():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        raw.setdefault("identity", {})["avatar"] = relative.as_posix()
        manifest = self._normalize_manifest(raw)
        self._write_manifest(target, self._with_hashes(manifest, target))

    def _manifest_path(self, character_id: str) -> Path:
        return self.packages_dir / character_id / "manifest.json"

    @staticmethod
    def _read_json(path: Path, *, fallback: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return fallback

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def _write_manifest(self, root: Path, manifest: dict[str, Any]) -> None:
        self._atomic_json(root / "manifest.json", manifest)

    def _reject_secrets(self, raw: Any, path: str = "") -> None:
        """Refuse a package carrying anything that belongs to a person.

        A character package is a portable description of a character. Secrets,
        someone's conversation history, their memory vault and their permission
        decisions are all properties of a user and a machine, not of a
        character, so a package offering them is either mistaken or trying to
        arrive pre-authorised (TDD §11.2). Both are refused by name rather than
        stripped, so the user finds out an import tried it.
        """

        if isinstance(raw, dict):
            for key, value in raw.items():
                next_path = f"{path}.{key}" if path else str(key)
                lowered = str(key).casefold().replace("-", "_")
                if any(token in lowered for token in SECRET_FIELD_TOKENS) and str(value or "").strip():
                    raise CharacterPackageError("package_secret_blocked", "角色包不能携带 API Key、密码或访问令牌。", details={"field": next_path})
                if lowered in USER_STATE_FIELDS and _has_content(value):
                    raise CharacterPackageError(
                        "package_user_state_blocked",
                        "角色包不能携带聊天记录、记忆或权限授权。",
                        details={"field": next_path},
                    )
                self._reject_secrets(value, next_path)
        elif isinstance(raw, list):
            for index, value in enumerate(raw):
                self._reject_secrets(value, f"{path}[{index}]")

    def _observed_provenance(self, source: Path | None, *, package_format: str) -> dict[str, Any]:
        """What Joi saw when it read this package, not what the package says.

        `source.url` below is whatever the author typed into their own
        manifest. These fields are measured at import instead, so a package
        cannot describe itself as having come from somewhere it did not. Only
        the file's name is kept -- a full path would say more about this
        machine than about the character.
        """

        if source is None:
            return {"format": package_format, "file_name": "", "archive_sha256": "", "imported_at": round(time.time(), 3)}
        return {
            "format": package_format,
            "file_name": source.name[:120],
            "archive_sha256": _sha256_file(source) if source.is_file() else "",
            "imported_at": round(time.time(), 3),
        }

    @staticmethod
    def _import_result(manifest: dict[str, Any], location: CharacterPackageLocation, *, warnings: list[str]) -> dict[str, Any]:
        return {
            "ok": True,
            "installed": manifest["id"],
            "warnings": warnings,
            "security": {
                "executable_content": False,
                "secret_content": False,
                "package_hash": str((manifest.get("security") or {}).get("package_hash") or ""),
                "license": str((manifest.get("security") or {}).get("license") or "Unknown"),
            },
            "character": {"id": manifest["id"], "name": manifest["identity"]["name"]},
        }

    def _inspect_result(
        self,
        manifest: dict[str, Any],
        root: Path | None,
        source: Path,
        *,
        portrait: Path | None = None,
        usability: list[str] | None = None,
    ) -> dict[str, Any]:
        if portrait is None and root is not None:
            portrait = self._resolve_asset(root, (manifest.get("appearance") or {}).get("portrait"))
        preview_image = ""
        if portrait and portrait.is_file() and portrait.stat().st_size <= 8 * 1024 * 1024:
            mime = mimetypes.guess_type(portrait.name)[0] or "image/png"
            preview_image = f"data:{mime};base64,{base64.b64encode(portrait.read_bytes()).decode('ascii')}"
        warnings = []
        license_name = str((manifest.get("security") or {}).get("license") or "Unknown")
        if license_name.casefold() in {"", "unknown"}:
            warnings.append("角色包没有声明许可证，请确认立绘、模型与声音素材的使用权限。")
        requested = list((manifest.get("capabilities") or {}).get("requested_skills") or [])
        if requested:
            warnings.append("该角色请求技能权限；安装后仍保持关闭，需要你逐项授权。")
        asset_report = self._appearance_report(manifest, root, portrait_override=portrait)
        warnings.extend(str(item) for item in asset_report.get("warnings") or [] if str(item) not in warnings)
        # Whether it will work, reported beside whether it may be trusted. Both
        # belong on the card the user confirms against.
        warnings.extend(str(item) for item in usability or [] if str(item) not in warnings)
        public = self._public_manifest(manifest, root or source.parent, include_content=True)
        public["portrait_data_url"] = preview_image
        return {
            "ok": True,
            "source": str(source),
            "preview": public,
            "warnings": warnings,
            "security": {
                "executable_content": False,
                "secret_content": False,
                "user_state_content": False,
                "license": license_name,
                "requested_skills": requested,
                "compatibility": str((manifest.get("security") or {}).get("compatibility") or ""),
                "installable": bool(asset_report.get("installable")),
                "asset_report": asset_report,
            },
            "provenance": {
                **self._observed_provenance(source if source.is_file() else None, package_format="preview"),
                "declared_source": copy.deepcopy(manifest.get("source") or {}),
            },
        }


def _is_archive_junk(name: str) -> bool:
    """macOS packaging leftovers, which every loader has to skip."""

    parts = PurePosixPath(name).parts
    return any(part == "__MACOSX" or part.startswith("._") for part in parts)


def _looks_like_mojibake(name: str) -> bool:
    """Whether a zip entry name decoded as replacement characters or CP437 noise.

    Python decodes a zip entry without the UTF-8 flag as CP437, so a Japanese or
    Chinese filename arrives as accented Latin. Detecting it exactly is not
    possible; detecting that it is not plausible text is enough to warn.
    """

    if "\ufffd" in name:
        return True
    suspicious = sum(1 for char in name if "\u0080" <= char <= "\u00ff")
    return suspicious >= 4


def _safe_character_id(value: Any) -> str:
    text = str(value or "").strip().casefold().replace(" ", "-").replace("_", "-")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff.-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(".-")
    return text[:80]


def _safe_filename(value: str) -> str:
    name = Path(value).name
    stem = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff._-]+", "-", Path(name).stem).strip(".-") or "asset"
    return f"{stem[:80]}{Path(name).suffix.casefold()[:16]}"


def _safe_relative_asset(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or ":" in text:
        return ""
    return path.as_posix()[:500]


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _clean_string_list(value: Any, max_items: int, item_limit: int) -> list[str]:
    if isinstance(value, str):
        value = [line.strip(" -") for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value[:max_items]:
        text = _clean_text(item, item_limit)
        if text and text not in rows:
            rows.append(text)
    return rows


def _clean_mapping(value: Any, max_items: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in list(value.items())[:max_items]:
        safe_key = _clean_text(key, 100)
        if not safe_key:
            continue
        if isinstance(item, dict):
            cleaned[safe_key] = _clean_mapping(item, max_items)
        elif isinstance(item, list):
            cleaned[safe_key] = _clean_string_list(item, max_items, 1_000)
        elif isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[safe_key] = _clean_text(item, 2_000) if isinstance(item, str) else item
    return cleaned


def _clean_locale(value: Any) -> str:
    """A language tag, or nothing if it is not one."""

    text = str(value or "").strip().replace("_", "-")
    return text if LOCALE_PATTERN.match(text) else ""


def _clean_localizations(value: Any) -> dict[str, Any]:
    """The same character, said in another language.

    Only what changes between languages is overlaid: how the character speaks,
    how its voice is described, and the package-local recordings that teach the
    synthesiser that language. Everything else -- the model, the expressions,
    the hashes, the security block -- is deliberately not overridable, because
    a translation is a different wording of one character, not a second
    character that happens to share an id.

    A Japanese persona wants a Japanese voice description rather than a
    translated Chinese one, which is why `voice.design` is per locale: the
    synthesiser is being told about a Japanese speaker, and saying so in
    Chinese describes someone reading Japanese with an accent.
    """

    if not isinstance(value, dict):
        return {}
    rows: dict[str, Any] = {}
    for tag, entry in list(value.items())[:MAX_LOCALES]:
        locale = _clean_locale(tag)
        if not locale or not isinstance(entry, dict):
            continue
        identity_raw = entry.get("identity") if isinstance(entry.get("identity"), dict) else {}
        voice_raw = entry.get("voice") if isinstance(entry.get("voice"), dict) else {}
        identity = {
            "name": _clean_text(identity_raw.get("name"), 80),
            "persona": _clean_text(identity_raw.get("persona"), 12_000),
            "personality": _clean_text(identity_raw.get("personality"), 6_000),
            "scenario": _clean_text(identity_raw.get("scenario"), 6_000),
            "tone": _clean_text(identity_raw.get("tone"), 1_000),
            "boundaries": _clean_string_list(identity_raw.get("boundaries"), 40, 500),
            "greeting": _clean_text(identity_raw.get("greeting"), 4_000),
            "example_dialogue": _clean_text(identity_raw.get("example_dialogue"), 12_000),
        }
        voice = {
            "language": _clean_text(voice_raw.get("language"), 20),
            "prompt_language": _clean_text(voice_raw.get("prompt_language"), 20),
            "design": _clean_text(voice_raw.get("design"), 2_000),
            "label": _clean_text(voice_raw.get("label"), 80),
            # A Chinese reference clip cannot teach the Japanese frontend the
            # same phonemes. These remain package-relative and pass through the
            # same path containment checks as the base voice; model weights are
            # intentionally still shared and cannot be swapped by a locale.
            "reference_audio": _safe_relative_asset(voice_raw.get("reference_audio")),
            "prompt_text": _clean_text(voice_raw.get("prompt_text"), 2_000),
            "speed": _optional_float(voice_raw.get("speed"), 0.5, 2.0),
            "volume": _optional_float(voice_raw.get("volume"), 0.0, 2.0),
            "emotion_map": _clean_emotion_map(voice_raw.get("emotion_map")),
            # Per language, because these are lines she speaks: a Japanese
            # persona greeting in Chinese is the same mismatch as the voice
            # description being in the wrong language.
            "motion_lines": _clean_motion_lines(voice_raw.get("motion_lines")),
        }
        identity = {key: item for key, item in identity.items() if item not in ("", [])}
        voice = {key: item for key, item in voice.items() if item not in ("", None, {})}
        if identity or voice:
            rows[locale] = {"identity": identity, "voice": voice}
    return rows


def _apply_localization(manifest: dict[str, Any], locale: str) -> dict[str, Any]:
    """A copy of the manifest as this character sounds in one language.

    The base manifest is itself a locale -- whichever one the author wrote it
    in -- so asking for that locale, or for one the package never declared,
    returns the manifest unchanged rather than an empty character.
    """

    wanted = _clean_locale(locale)
    overlay = (manifest.get("localizations") or {}).get(wanted)
    if not wanted or not isinstance(overlay, dict):
        return manifest
    localized = copy.deepcopy(manifest)
    for section in ("identity", "voice"):
        values = overlay.get(section)
        if isinstance(values, dict):
            localized.setdefault(section, {}).update(values)
    localized["locale"] = wanted
    return localized


def available_locales(manifest: dict[str, Any]) -> list[str]:
    """Every language this character can speak, base language first."""

    base = _clean_locale(manifest.get("locale")) or DEFAULT_LOCALE
    others = sorted(tag for tag in (manifest.get("localizations") or {}) if tag != base)
    return [base, *others]


def _clean_emotion_map(value: Any) -> dict[str, Any]:
    """Per-emotion voice settings, keyed by the emotion names Joi already uses.

    The short form is `{"happy": "assets/voice/happy.wav"}`: handing GPT-SoVITS
    a reference clip of the character sounding happy is how it is told to sound
    happy, so a bare path is the common case and is read as one. The long form
    spells out the clip's transcript and prosody alongside it.

    Emotion names Joi does not recognise are dropped rather than folded into
    neutral. `normalize_emotion` answers "neutral" for anything unknown, and
    honouring that here would let one typo silently replace the character's
    normal speaking voice.
    """

    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in list(value.items())[: len(EMOTION_ALIASES)]:
        alias = str(key or "").strip().casefold().replace(" ", "_")
        if alias not in EMOTION_ALIASES:
            continue
        entry = item if isinstance(item, dict) else {"reference_audio": item}
        row = {
            "reference_audio": _safe_relative_asset(entry.get("reference_audio")),
            "prompt_text": _clean_text(entry.get("prompt_text"), 2_000),
            "speed": _optional_float(entry.get("speed"), 0.5, 2.0),
            # -1 lowest, 1 highest. A hint for voices that can be pitched at
            # synthesis time rather than re-recorded, which is how the system
            # voice gets any emotion at all.
            "pitch": _optional_float(entry.get("pitch"), -1.0, 1.0),
            # For a hosted model that can be told how to read a line, the
            # author's own sentence about it beats any table Joi could write.
            "instructions": _clean_text(entry.get("instructions"), 1_000),
        }
        if any(field not in ("", None) for field in row.values()):
            cleaned[EMOTION_ALIASES[alias]] = row
    return cleaned


def _clean_motion_lines(value: Any) -> dict[str, str]:
    """Per-motion lines, keyed by the motions Joi actually knows.

    An unknown motion name is dropped rather than kept: it can never be played,
    and keeping it would suggest the package covers a motion it does not.
    """

    from agent_companion.core.character_motion import MOTION_SPECS

    if not isinstance(value, dict):
        return {}
    lines: dict[str, str] = {}
    for motion, line in list(value.items())[: len(MOTION_SPECS)]:
        name = str(motion or "").strip().casefold()
        text = _clean_text(line, 140)
        if name in MOTION_SPECS and text:
            lines[name] = text
    return lines


def _optional_float(value: Any, minimum: float, maximum: float) -> float | None:
    """A clamped number, or nothing when the author did not declare one."""

    if value is None or value == "":
        return None
    try:
        return min(maximum, max(minimum, float(value)))
    except (TypeError, ValueError):
        return None


def _clean_mapping_list(value: Any, max_items: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_clean_mapping(item, 40) for item in value[:max_items] if isinstance(item, dict)]


def _clean_lorebook(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"entries": []}
    entries = value.get("entries")
    if isinstance(entries, dict):
        entries = list(entries.values())
    cleaned_entries = []
    for index, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        keys = entry.get("keys") or entry.get("key") or []
        cleaned_entries.append(
            {
                "id": _clean_text(entry.get("id") or index + 1, 80),
                "name": _clean_text(entry.get("name") or entry.get("comment") or f"条目 {index + 1}", 120),
                "keys": _clean_string_list(keys if isinstance(keys, list) else [keys], 40, 120),
                "content": _clean_text(entry.get("content"), 6_000),
                "enabled": bool(entry.get("enabled", True)),
                "constant": bool(entry.get("constant", False)),
            }
        )
    return {"name": _clean_text(value.get("name"), 120), "description": _clean_text(value.get("description"), 1_000), "entries": cleaned_entries[:300]}


def _clean_version(value: Any) -> str:
    text = _clean_text(value, 40)
    match = re.search(r"\d+(?:\.\d+){0,3}(?:[-+][a-zA-Z0-9.-]+)?", text)
    return match.group(0) if match else "1.0.0"


def _clean_url(value: Any) -> str:
    text = _clean_text(value, 500)
    return text if text.startswith(("https://", "http://")) else ""


def _clean_provenance(value: Any) -> dict[str, Any]:
    """Keep only the shape Joi writes, so a package cannot forge this block."""

    raw = value if isinstance(value, dict) else {}
    imported_at = _safe_float(raw.get("imported_at"), 0.0, 0.0, 4_102_444_800.0)
    return {
        "format": _clean_text(raw.get("format"), 40),
        "file_name": _clean_text(raw.get("file_name"), 120),
        "archive_sha256": _clean_text(raw.get("archive_sha256"), 64) if _is_sha256(raw.get("archive_sha256")) else "",
        "imported_at": round(imported_at, 3),
    }


def _is_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _has_content(value: Any) -> bool:
    """Whether a field carries anything, for containers as well as scalars."""

    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return bool(str(value or "").strip())


def _safe_color(value: Any, fallback: str) -> str:
    text = _clean_text(value, 16)
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else fallback


def _safe_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        return min(maximum, max(minimum, float(value)))
    except (TypeError, ValueError):
        return fallback


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        elif key not in {"portrait_path", "background_path", "model_path", "reference_audio_path"}:
            base[key] = copy.deepcopy(value)
    return base


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_tuple(value: str) -> tuple[int, ...]:
    rows = [int(item) for item in re.findall(r"\d+", value)[:4]]
    return tuple(rows + [0] * (4 - len(rows)))


def _compatibility_supported(value: str) -> bool:
    expression = str(value or "").strip()
    match = re.search(r"(>=|<=|==|=|>|<|\^|~)?\s*(\d+(?:\.\d+){0,3})", expression)
    if not match:
        return False
    operator = match.group(1) or ">="
    required = _version_tuple(match.group(2))
    current = _version_tuple(JOI_CHARACTER_VERSION)
    if operator == ">=":
        return current >= required
    if operator == "<=":
        return current <= required
    if operator in {"=", "=="}:
        return current == required
    if operator == ">":
        return current > required
    if operator == "<":
        return current < required
    if operator == "^":
        return current >= required and current[0] == required[0]
    if operator == "~":
        return current >= required and current[:2] == required[:2]
    return False


def _safe_update_url(value: str) -> bool:
    text = str(value or "").strip().casefold()
    return text.startswith("https://") or text.startswith("http://127.0.0.1") or text.startswith("http://localhost")
