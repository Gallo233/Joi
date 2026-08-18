from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

from agent_companion.core.config import load_workspace_config
from agent_companion.core.secret_store import (
    LLM_API_KEY_ENV,
    delete_managed_secret,
    managed_secret,
    managed_secret_status,
    store_managed_secret,
)


ReloadCallback = Callable[[], None]
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "openai",
        "label": "OpenAI",
        "description": "官方 API · 默认选择高性价比模型",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6-luna",
        "requires_key": True,
        "cost_hint": "适合日常高频对话",
    },
    {
        "id": "openai_compatible",
        "label": "兼容 API",
        "description": "支持 OpenAI Chat Completions 的供应商",
        "base_url": "",
        "model": "",
        "requires_key": True,
        "cost_hint": "价格由供应商决定",
    },
    {
        "id": "ollama",
        "label": "Ollama 本地",
        "description": "使用本机模型 · 不消耗云端额度",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "",
        "requires_key": False,
        "cost_hint": "无 API 调用费用",
    },
)


class ByokService:
    def __init__(self, workspace: Path, reload_callback: ReloadCallback) -> None:
        self.workspace = workspace.resolve()
        self.reload_callback = reload_callback
        self.last_test: dict[str, Any] | None = None

    def status(self, *, probe_secret: bool = True) -> dict[str, Any]:
        config = load_workspace_config(self.workspace)
        secret = managed_secret_status(LLM_API_KEY_ENV) if probe_secret else _unprobed_secret_status(config)
        if config is None:
            return {
                "ok": True,
                "configured": False,
                "state": "not_configured",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6-luna",
                "requires_key": True,
                "secret": secret,
                "presets": list(_PRESETS),
                "last_test": self.last_test,
            }
        provider = _normalized_provider(config.llm.provider)
        requires_key = provider != "ollama"
        configured = bool(config.llm.is_configured and not config.llm.use_mock)
        if requires_key and configured and not secret.get("stored"):
            secret = {**secret, "stored": True, "source": "legacy"}
        return {
            "ok": True,
            "configured": configured,
            "state": "ready" if configured else "mock" if config.llm.use_mock else "incomplete",
            "provider": provider,
            "base_url": _safe_endpoint_for_display(config.llm.base_url),
            "model": _safe_model(config.llm.model),
            "temperature": max(0.0, min(2.0, float(config.llm.temperature))),
            "requires_key": requires_key,
            "secret": secret if requires_key else {"stored": True, "source": "not_required", "secure_store_available": True},
            "presets": list(_PRESETS),
            "last_test": self.last_test,
        }

    def connect(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        prepared, error = _prepare_connection(params)
        if error:
            return {"ok": False, "saved": False, "error": error, "byok": self.status()}
        assert prepared is not None
        api_key = str(params.get("api_key") or "").strip()
        if prepared["requires_key"] and api_key:
            stored, store_error = store_managed_secret(LLM_API_KEY_ENV, api_key)
            if not stored:
                return {"ok": False, "saved": False, "error": store_error, "byok": self.status()}
        if prepared["requires_key"] and not (api_key or managed_secret(LLM_API_KEY_ENV) or _legacy_config_has_key(self.workspace)):
            return {"ok": False, "saved": False, "error": "api_key_required", "byok": self.status()}
        write_error = _write_connection_config(self.workspace, prepared)
        if write_error:
            return {"ok": False, "saved": False, "error": write_error, "byok": self.status()}
        self.reload_callback()
        test = self.test()
        return {"ok": bool(test.get("ok")), "saved": True, "error": test.get("error", ""), "test": test, "byok": self.status()}

    def test(self) -> dict[str, Any]:
        started = time.perf_counter()
        config = load_workspace_config(self.workspace)
        if config is None or config.llm.use_mock or not config.llm.is_configured:
            return self._remember_test(False, "model_unconfigured", started)
        try:
            from openai import OpenAI

            endpoint = config.llm.base_url.strip()
            client = OpenAI(
                api_key=config.llm.api_key or "ollama",
                base_url=endpoint,
                timeout=10.0,
                max_retries=0,
            )
            response = client.models.list()
            model_ids = _safe_model_ids(getattr(response, "data", []))
            if model_ids and config.llm.model not in model_ids:
                return self._remember_test(False, "model_not_found", started, models=model_ids)
            return self._remember_test(True, "", started, models=model_ids)
        except Exception as exc:
            return self._remember_test(False, _connection_error_code(exc), started)

    def discover_models(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        params = params if isinstance(params, dict) else {}
        provider = _normalized_provider(params.get("provider"))
        if provider != "ollama":
            return self._model_list_result(False, "unsupported_discovery", started)
        preset = next(row for row in _PRESETS if row["id"] == "ollama")
        base_url = str(params.get("base_url") or preset["base_url"]).strip().rstrip("/")
        if not _valid_endpoint(base_url, allow_local_http=True):
            return self._model_list_result(False, "invalid_endpoint", started)
        try:
            from openai import OpenAI

            client = OpenAI(api_key="ollama", base_url=base_url, timeout=5.0, max_retries=0)
            response = client.models.list()
            model_ids = _safe_model_ids(getattr(response, "data", []))
            if not model_ids:
                return self._model_list_result(False, "no_local_models", started)
            return self._model_list_result(True, "", started, models=model_ids)
        except Exception as exc:
            return self._model_list_result(False, _connection_error_code(exc), started)

    def disconnect(self) -> dict[str, Any]:
        secret_before = managed_secret_status(LLM_API_KEY_ENV)
        config_path = self.workspace / "config.yaml"
        if config_path.is_file():
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                return {"ok": False, "error": "config_invalid", "byok": self.status()}
            if not isinstance(raw, dict):
                return {"ok": False, "error": "config_invalid", "byok": self.status()}
            llm = raw.setdefault("llm", {})
            if not isinstance(llm, dict):
                llm = {}
                raw["llm"] = llm
            llm["use_mock"] = True
            if _normalized_provider(llm.get("provider")) != "ollama":
                llm["api_key"] = "${JOI_LLM_API_KEY}"
            error = _write_yaml(config_path, raw)
            if error:
                return {"ok": False, "error": error, "byok": self.status()}
        secret_removed = False
        if secret_before.get("source") == "system":
            secret_removed, delete_error = delete_managed_secret(LLM_API_KEY_ENV)
            if not secret_removed:
                self.reload_callback()
                return {
                    "ok": False,
                    "error": delete_error,
                    "disconnected": True,
                    "secret_removed": False,
                    "secret_source": "system",
                    "byok": self.status(),
                }
        self.last_test = None
        self.reload_callback()
        return {
            "ok": True,
            "disconnected": True,
            "secret_removed": secret_removed,
            "secret_source": secret_before.get("source", "missing"),
            "byok": self.status(),
        }

    def _remember_test(self, ok: bool, error: str, started: float, *, models: list[str] | None = None) -> dict[str, Any]:
        result = {
            "ok": ok,
            "error": error,
            "latency_ms": max(0, int(round((time.perf_counter() - started) * 1000))),
            "models": (models or [])[:60],
            "checked_without_generation": True,
        }
        self.last_test = result
        return result

    @staticmethod
    def _model_list_result(ok: bool, error: str, started: float, *, models: list[str] | None = None) -> dict[str, Any]:
        return {
            "ok": ok,
            "error": error,
            "latency_ms": max(0, int(round((time.perf_counter() - started) * 1000))),
            "models": (models or [])[:60],
            "checked_without_generation": True,
        }


def _prepare_connection(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    provider = _normalized_provider(params.get("provider"))
    preset = next((row for row in _PRESETS if row["id"] == provider), None)
    if preset is None or not _PROVIDER_RE.fullmatch(provider):
        return None, "unsupported_provider"
    base_url = str(params.get("base_url") or preset["base_url"] or "").strip().rstrip("/")
    if not _valid_endpoint(base_url, allow_local_http=provider == "ollama"):
        return None, "invalid_endpoint"
    model = _safe_model(params.get("model") or preset["model"])
    if not model or not _MODEL_RE.fullmatch(model):
        return None, "invalid_model"
    try:
        temperature = float(params.get("temperature", 0.7))
    except (TypeError, ValueError):
        return None, "invalid_temperature"
    if not 0.0 <= temperature <= 2.0:
        return None, "invalid_temperature"
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "requires_key": bool(preset["requires_key"]),
    }, ""


def _write_connection_config(workspace: Path, connection: dict[str, Any]) -> str:
    config_path = workspace / "config.yaml"
    if config_path.is_file():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return "config_invalid"
        if not isinstance(raw, dict):
            return "config_invalid"
    else:
        raw = _default_config(workspace)
    llm = raw.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = {}
        raw["llm"] = llm
    llm.update(
        {
            "provider": connection["provider"],
            "use_mock": False,
            "base_url": connection["base_url"],
            "model": connection["model"],
            "api_key": "ollama" if not connection["requires_key"] else "${JOI_LLM_API_KEY}",
            "temperature": connection["temperature"],
            "mock_when_unconfigured": False,
        }
    )
    return _write_yaml(config_path, raw)


def _default_config(workspace: Path) -> dict[str, Any]:
    example_path = workspace / "config.example.yaml"
    if example_path.is_file():
        try:
            example = yaml.safe_load(example_path.read_text(encoding="utf-8")) or {}
        except Exception:
            example = {}
        if isinstance(example, dict) and isinstance(example.get("characters"), list) and example["characters"]:
            return example
    return {
        "app": {"title": "Joi"},
        "llm": {},
        "tts": {"enabled": False, "provider": "gpt-sovits"},
        "asr": {"enabled": False, "provider": "openai_compatible"},
        "ocr": {"timeout_seconds": 5, "language": "chi_sim+eng+jpn"},
        "computer_use": {"post_action_settle_ms": 200},
        "characters": [
            {
                "name": "Joi",
                "color": "#d76f8f",
                "sprite_color": "#224e66",
                "setting": (
                    "Joi 是这个应用的默认角色。她熟悉代码、游戏和屏幕内容，"
                    "会用简短自然的方式说明计划、提醒风险和汇报结果。"
                ),
                "active_voice_profile": "zh",
                "prompt_lang": "zh",
                "speech_speed": 1.2,
                "speech_volume": 1.0,
                "voice_profiles": [
                    {"id": "zh", "label": "中文", "text_lang": "zh", "prompt_lang": "zh"}
                ],
                "sprites": [{"id": "1", "label": "默认", "image_path": ""}],
            }
        ],
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> str:
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        yaml.safe_load(text)
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        return "config_write_failed"
    return ""


def _valid_endpoint(value: str, *, allow_local_http: bool) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    if parsed.username or parsed.password or not parsed.hostname or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    return bool(allow_local_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})


def _normalized_provider(value: object) -> str:
    provider = str(value or "openai").strip().casefold().replace("-", "_")
    return provider if provider in {row["id"] for row in _PRESETS} else provider


def _unprobed_secret_status(config: Any) -> dict[str, Any]:
    """Return startup-safe BYOK state without opening the system keychain."""

    if os.environ.get(LLM_API_KEY_ENV, "").strip():
        return {"stored": True, "source": "environment", "secure_store_available": True}
    configured = bool(config and config.llm.is_configured and not config.llm.use_mock)
    return {
        "stored": configured,
        "source": "configured" if configured else "unchecked",
        "secure_store_available": True,
    }


def _safe_model(value: object) -> str:
    return " ".join(str(value or "").split()).strip()[:128]


def _safe_endpoint_for_display(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        port = f":{parsed.port}" if parsed.port else ""
    except Exception:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path.rstrip('/')}"[:300]


def _safe_model_ids(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    models: list[str] = []
    for row in rows:
        model = _safe_model(getattr(row, "id", ""))
        if model and _MODEL_RE.fullmatch(model) and model not in models:
            models.append(model)
    return sorted(models)[:100]


def _connection_error_code(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.casefold()
    if status in {401, 403} or "authentication" in name or "permissiondenied" in name:
        return "authentication_failed"
    if status == 404:
        return "endpoint_not_found"
    if status == 429 or "ratelimit" in name:
        return "rate_limited"
    if "timeout" in name:
        return "connection_timeout"
    if "connection" in name:
        return "endpoint_unreachable"
    return "connection_test_failed"


def _legacy_config_has_key(workspace: Path) -> bool:
    config = load_workspace_config(workspace)
    return bool(config and config.llm.api_key and not config.llm.api_key.startswith(("${", "%")))
