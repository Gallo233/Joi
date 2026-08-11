from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

from agent_companion.core.skill_manifest import KNOWN_SKILL_IDS, normalize_skill_id


_SECRET_FIELD_TOKENS = ("api_key", "token", "secret", "password", "server_url", "refer_audio_path", "gpt_sovits_work_path")
_PATH_FIELD_TOKENS = ("path", "file", "dir")
_PATH_VALUE_RE = re.compile(r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/|[A-Za-z]:[\\/]|[/\\]")
_SECRET_VALUE_RE = re.compile(r"sk-[A-Za-z0-9_-]+|\b(?:api[_-]?key|secret|token|bearer)\b", re.IGNORECASE)
_MODEL_FILE_RE = re.compile(r"\.(?:gguf|safetensors|ckpt|pth|onnx|bin|pt|wav|mp3|flac|ogg)\b", re.IGNORECASE)
_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^%[A-Za-z_][A-Za-z0-9_]*%$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
_LANG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")


@dataclass(frozen=True)
class ConfigMutationResult:
    ok: bool
    changed: bool
    dry_run: bool
    summary: str
    changes: list[dict[str, str]]
    errors: list[dict[str, str]]

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "dry_run": self.dry_run,
            "summary": self.summary,
            "changes": self.changes,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class _FieldSpec:
    label: str
    setting_id: str
    value_kind: str
    validator: Callable[[object], tuple[bool, object | None, str]]


_ALLOWED_FIELDS: dict[tuple[str, str], _FieldSpec] = {
    ("asr", "enabled"): _FieldSpec("ASR enabled", "asr_enabled", "boolean", lambda value: _validate_bool(value)),
    ("asr", "provider"): _FieldSpec("ASR provider", "asr_provider", "identifier", lambda value: _validate_identifier(value)),
    ("asr", "base_url"): _FieldSpec("ASR endpoint", "asr_endpoint", "endpoint", lambda value: _validate_endpoint(value)),
    ("asr", "model"): _FieldSpec("ASR model", "asr_model", "model", lambda value: _validate_model(value)),
    ("asr", "language"): _FieldSpec("ASR language", "asr_language", "language", lambda value: _validate_language(value)),
    ("asr", "max_seconds"): _FieldSpec("ASR max duration", "asr_max_seconds", "seconds", lambda value: _validate_int_range(value, 1, 600)),
    ("asr", "max_bytes"): _FieldSpec("ASR max payload", "asr_max_bytes", "bytes", lambda value: _validate_int_range(value, 1024, 200 * 1024 * 1024)),
    ("asr", "timeout_seconds"): _FieldSpec("ASR timeout", "asr_timeout", "seconds", lambda value: _validate_int_range(value, 1, 300)),
    ("tts", "enabled"): _FieldSpec("TTS enabled", "tts_enabled", "boolean", lambda value: _validate_bool(value)),
    ("tts", "provider"): _FieldSpec("TTS provider", "tts_provider", "identifier", lambda value: _validate_identifier(value)),
    ("tts", "volume"): _FieldSpec("TTS volume", "tts_volume", "number", lambda value: _validate_float_range(value, 0.0, 2.0)),
    ("tts", "text_lang"): _FieldSpec("TTS text language", "tts_text_lang", "language", lambda value: _validate_language(value)),
    ("tts", "prompt_lang"): _FieldSpec("TTS prompt language", "tts_prompt_lang", "language", lambda value: _validate_language(value)),
    ("tts", "speed_factor"): _FieldSpec("TTS speed", "tts_speed", "number", lambda value: _validate_float_range(value, 0.5, 2.0)),
    ("tts", "gpt_sovits_streaming_mode"): _FieldSpec("GPT-SoVITS streaming quality", "tts_local_streaming_mode", "integer", lambda value: _validate_int_range(value, 1, 3)),
    # Writable again. Keeping the system voice from ever speaking is the job of
    # this being `false`, not of the field being unreachable: making it
    # unwritable left the checkbox in the settings panel unable to do anything,
    # and silently broke four separate contract assertions that say a user can
    # set it.
    ("tts", "fallback_to_system"): _FieldSpec("TTS system fallback", "tts_system_fallback", "boolean", lambda value: _validate_bool(value)),
    # A hosted voice needs the same three answers a hosted text model does --
    # where, which model, and which voice -- and without them the panel could
    # switch the provider to one it had no way to finish configuring.
    ("tts", "base_url"): _FieldSpec("TTS endpoint", "tts_endpoint", "endpoint", lambda value: _validate_endpoint(value)),
    ("tts", "model"): _FieldSpec("TTS model", "tts_model", "model", lambda value: _validate_model(value)),
    # Preset voice names are the provider's own words, and some are Chinese
    # ("冰糖"), so this is validated as a model name rather than an identifier.
    ("tts", "voice"): _FieldSpec("TTS voice", "tts_voice", "model", lambda value: _validate_model(value)),
    ("tts", "audio_format"): _FieldSpec("TTS audio format", "tts_audio_format", "identifier", lambda value: _validate_identifier(value)),
    ("tts", "optimize_text"): _FieldSpec("TTS text rewriting", "tts_optimize_text", "boolean", lambda value: _validate_bool(value)),
    ("tts", "timeout_seconds"): _FieldSpec("TTS timeout", "tts_timeout", "seconds", lambda value: _validate_int_range(value, 1, 600)),
    ("ocr", "timeout_seconds"): _FieldSpec("OCR timeout", "ocr_timeout", "seconds", lambda value: _validate_int_range(value, 1, 120)),
    ("computer_use", "post_action_settle_ms"): _FieldSpec("Computer Use settle delay", "computer_settle_delay", "milliseconds", lambda value: _validate_int_range(value, 0, 10_000)),
    ("llm", "provider"): _FieldSpec("Text provider", "llm_provider", "identifier", lambda value: _validate_identifier(value)),
    ("llm", "model"): _FieldSpec("Text model", "llm_model", "model", lambda value: _validate_model(value)),
    ("llm", "vision_enabled"): _FieldSpec("Vision model enabled", "vision_enabled", "boolean", lambda value: _validate_bool(value)),
    ("llm", "vision_model"): _FieldSpec("Vision model", "vision_model", "model", lambda value: _validate_model(value)),
    ("llm", "expression_enabled"): _FieldSpec("Expression model enabled", "expression_enabled", "boolean", lambda value: _validate_bool(value)),
    ("llm", "expression_model"): _FieldSpec("Expression model", "expression_model", "model", lambda value: _validate_model(value)),
    ("llm", "temperature"): _FieldSpec("Text temperature", "llm_temperature", "number", lambda value: _validate_float_range(value, 0.0, 2.0)),
    ("llm", "use_mock"): _FieldSpec("Mock text model", "llm_use_mock", "boolean", lambda value: _validate_bool(value)),
}


def preview_runtime_config_update(workspace: Path, updates: dict[str, Any]) -> ConfigMutationResult:
    return update_runtime_config(workspace, updates, dry_run=True)


def update_runtime_config(workspace: Path, updates: dict[str, Any], *, dry_run: bool = False) -> ConfigMutationResult:
    workspace = workspace.resolve()
    config_path = workspace / "config.yaml"
    if not isinstance(updates, dict) or not updates:
        return _failed("没有可更新的运行设置。", [{"code": "empty_update", "setting": "runtime_config"}], dry_run)
    raw_config, read_error = _read_yaml_dict(config_path)
    if read_error:
        return _failed("运行设置没有更新。", [{"code": read_error, "setting": "runtime_config"}], dry_run)
    secrets_path = workspace / "secrets.yaml"
    _, secrets_error = _read_yaml_dict(secrets_path, missing_ok=True)
    if secrets_error:
        return _failed("运行设置没有更新。", [{"code": secrets_error, "setting": "runtime_config"}], dry_run)

    prepared = _prepare_updates(raw_config, updates)
    if prepared.errors:
        return _failed("运行设置没有更新。", prepared.errors, dry_run)

    mutated = copy.deepcopy(raw_config)
    changed = False
    changes: list[dict[str, str]] = []
    for path, value, spec in prepared.rows:
        before = _nested_get(mutated, path)
        action = "unchanged" if before == value else "changed"
        if action == "changed":
            changed = True
            _nested_set(mutated, path, value)
        changes.append({"setting": spec.setting_id, "label": spec.label, "action": action, "value_kind": spec.value_kind})

    if dry_run:
        return ConfigMutationResult(True, changed, True, _summary(changes, changed, preview=True), changes, [])
    if not changed:
        return ConfigMutationResult(True, False, False, "运行设置没有变化。", changes, [])

    write_error = _write_yaml_atomically(config_path, mutated)
    if write_error:
        return _failed("运行设置没有更新。", [{"code": write_error, "setting": "runtime_config"}], dry_run)
    return ConfigMutationResult(True, True, False, _summary(changes, changed, preview=False), changes, [])


@dataclass(frozen=True)
class _PreparedUpdates:
    rows: list[tuple[tuple[str, ...], object, _FieldSpec]]
    errors: list[dict[str, str]]


def _prepare_updates(raw_config: dict[str, Any], updates: dict[str, Any]) -> _PreparedUpdates:
    rows: list[tuple[tuple[str, ...], object, _FieldSpec]] = []
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for path, value in _flatten_updates(updates):
        skill_row = _prepare_skill_update(path, value)
        if skill_row is not None:
            skill_path, normalized, spec_or_error = skill_row
            if isinstance(spec_or_error, dict):
                errors.append(spec_or_error)
                continue
            if skill_path in seen:
                errors.append({"code": "duplicate_field", "setting": spec_or_error.setting_id})
                continue
            seen.add(skill_path)
            rows.append((skill_path, normalized, spec_or_error))
            continue
        if _is_sensitive_path(path):
            errors.append({"code": "sensitive_field_forbidden", "setting": "redacted"})
            continue
        if len(path) != 2 or path not in _ALLOWED_FIELDS:
            errors.append({"code": "field_not_allowed", "setting": "runtime_config"})
            continue
        if path in seen:
            errors.append({"code": "duplicate_field", "setting": _ALLOWED_FIELDS[path].setting_id})
            continue
        seen.add(path)
        spec = _ALLOWED_FIELDS[path]
        ok, normalized, code = spec.validator(value)
        if not ok:
            errors.append({"code": code, "setting": spec.setting_id})
            continue
        rows.append((path, normalized, spec))
    if not rows and not errors:
        errors.append({"code": "empty_update", "setting": "runtime_config"})
    return _PreparedUpdates(rows, errors)


def _prepare_skill_update(path: tuple[str, ...], value: object) -> tuple[tuple[str, str, str], object | None, _FieldSpec | dict[str, str]] | None:
    if len(path) != 3 or path[0] != "skills" or path[2] != "enabled":
        return None
    skill_id = normalize_skill_id(path[1])
    if not skill_id or skill_id not in KNOWN_SKILL_IDS:
        return ("skills", "unknown", "enabled"), None, {"code": "unknown_skill", "setting": "skill_enabled"}
    ok, normalized, code = _validate_bool(value)
    if not ok:
        return ("skills", skill_id, "enabled"), None, {"code": code, "setting": f"{skill_id}_enabled"}
    if skill_id == "joi.runtime_config" and normalized is False:
        return ("skills", skill_id, "enabled"), None, {"code": "protected_skill", "setting": f"{skill_id}_enabled"}
    return (
        "skills",
        skill_id,
        "enabled",
    ), normalized, _FieldSpec(f"{skill_id} enabled", f"{skill_id}_enabled", "boolean", lambda item: _validate_bool(item))


def _flatten_updates(value: object, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
    if isinstance(value, dict):
        rows: list[tuple[tuple[str, ...], object]] = []
        for key, item in value.items():
            rows.extend(_flatten_updates(item, (*prefix, str(key))))
        return rows
    return [(prefix, value)]


def _is_sensitive_path(path: tuple[str, ...]) -> bool:
    lowered = ".".join(path).casefold()
    if any(token in lowered for token in _SECRET_FIELD_TOKENS):
        return True
    if any(token in lowered for token in _PATH_FIELD_TOKENS):
        return True
    return False


def _validate_bool(value: object) -> tuple[bool, object | None, str]:
    return (True, value, "") if isinstance(value, bool) else (False, None, "invalid_type")


def _validate_identifier(value: object) -> tuple[bool, object | None, str]:
    if not isinstance(value, str):
        return False, None, "invalid_type"
    text = value.strip()
    if _is_env_placeholder(text):
        return True, text, ""
    if not _IDENTIFIER_RE.match(text) or _SECRET_VALUE_RE.search(text):
        return False, None, "invalid_value"
    return True, text, ""


def _validate_language(value: object) -> tuple[bool, object | None, str]:
    if not isinstance(value, str):
        return False, None, "invalid_type"
    text = value.strip()
    if not _LANG_RE.match(text) or _SECRET_VALUE_RE.search(text):
        return False, None, "invalid_value"
    return True, text, ""


def _validate_model(value: object) -> tuple[bool, object | None, str]:
    if not isinstance(value, str):
        return False, None, "invalid_type"
    text = value.strip()
    if _is_env_placeholder(text):
        return True, text, ""
    lowered = text.casefold()
    if not text or len(text) > 120:
        return False, None, "invalid_value"
    if _SECRET_VALUE_RE.search(text) or _PATH_VALUE_RE.search(text) or _MODEL_FILE_RE.search(text):
        return False, None, "sensitive_value_forbidden"
    if lowered.startswith(("http://", "https://")) or any(char in text for char in ("{", "}", "\n", "\r")):
        return False, None, "invalid_value"
    return True, text, ""


def _validate_endpoint(value: object) -> tuple[bool, object | None, str]:
    if not isinstance(value, str):
        return False, None, "invalid_type"
    text = value.strip()
    if _is_env_placeholder(text):
        return True, text, ""
    if len(text) > 300 or _SECRET_VALUE_RE.search(text) or any(char in text for char in ("{", "}", "\n", "\r")):
        return False, None, "invalid_value"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return False, None, "invalid_endpoint"
    return True, text, ""


def _validate_int_range(value: object, low: int, high: int) -> tuple[bool, object | None, str]:
    if not isinstance(value, int) or isinstance(value, bool):
        return False, None, "invalid_type"
    if not low <= value <= high:
        return False, None, "out_of_range"
    return True, value, ""


def _validate_float_range(value: object, low: float, high: float) -> tuple[bool, object | None, str]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False, None, "invalid_type"
    number = float(value)
    if not low <= number <= high:
        return False, None, "out_of_range"
    return True, number, ""


def _is_env_placeholder(value: str) -> bool:
    return bool(_ENV_PLACEHOLDER_RE.match(value.strip()))


def _read_yaml_dict(path: Path, *, missing_ok: bool = False) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return ({}, "") if missing_ok else ({}, "config_missing")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}, "config_invalid"
    if not isinstance(data, dict):
        return {}, "config_invalid"
    return data, ""


def _write_yaml_atomically(path: Path, payload: dict[str, Any]) -> str:
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        yaml.safe_load(text)
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return "write_failed"
    return ""


def _nested_get(payload: dict[str, Any], path: tuple[str, ...]) -> object:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _nested_set(payload: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    current = payload
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _summary(changes: list[dict[str, str]], changed: bool, *, preview: bool) -> str:
    changed_count = sum(1 for row in changes if row.get("action") == "changed")
    if preview:
        return f"预览 {changed_count} 项运行设置变更。" if changed else "预览完成，运行设置没有变化。"
    return f"已更新 {changed_count} 项运行设置。" if changed else "运行设置没有变化。"


def _failed(summary: str, errors: list[dict[str, str]], dry_run: bool) -> ConfigMutationResult:
    return ConfigMutationResult(False, False, dry_run, summary, [], errors)
