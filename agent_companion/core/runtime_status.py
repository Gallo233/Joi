from __future__ import annotations

from dataclasses import asdict, dataclass, field
import importlib
import importlib.util
from pathlib import Path
import platform
import os
import shutil
import sys
from typing import Any

from agent_companion.core.config import AppConfig, ModelRouter, load_app_config
from agent_companion.core.speech_input import AsrRuntimeState


@dataclass(frozen=True)
class RuntimeProviderStatus:
    name: str
    label: str
    state: str
    enabled: bool = False
    configured: bool = False
    provider: str = "none"
    model: str = ""
    summary: str = ""
    timeout_seconds: int | None = None
    limit: str = ""
    last_error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_agent_state(self) -> dict[str, Any]:
        return asdict(self)


def build_runtime_status(workspace: Path, asr_state: AsrRuntimeState, tts_status: dict[str, Any]) -> dict[str, Any]:
    workspace = workspace.resolve()
    config = _load_config(workspace)
    providers = [
        _asr_status(asr_state),
        _tts_status(tts_status),
        _ocr_status(config),
        _model_status(config, "text"),
        _model_status(config, "vision"),
        _model_status(config, "expression"),
        _computer_use_status(config),
        _audit_verification_status(),
    ]
    return {
        "read_only": True,
        "safe_for_display": True,
        "providers": [provider.to_agent_state() for provider in providers],
    }


def _load_config(workspace: Path) -> AppConfig | None:
    config_path = workspace / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        return load_app_config(config_path)
    except Exception:
        return None


def _asr_status(state: AsrRuntimeState) -> RuntimeProviderStatus:
    configured = bool(state.configured)
    enabled = bool(state.enabled)
    status = "ready" if configured else "off" if not enabled else "error"
    return RuntimeProviderStatus(
        "asr",
        "ASR",
        status,
        enabled=enabled,
        configured=configured,
        provider=_safe_identifier(state.provider),
        summary="已配置" if configured else "未启用" if not enabled else "未配置",
        timeout_seconds=max(1, int(state.timeout_seconds or 1)),
        limit=_format_bytes(int(state.max_bytes or 0)),
        last_error=_safe_error(state.error),
        notes=[f"max {max(1, int(state.max_seconds or 1))}s"],
    )


def _tts_status(payload: dict[str, Any]) -> RuntimeProviderStatus:
    enabled = bool(payload.get("enabled"))
    configured = bool(payload.get("configured"))
    status = "ready" if configured else "off" if not enabled else "error"
    return RuntimeProviderStatus(
        "tts",
        "TTS",
        status,
        enabled=enabled,
        configured=configured,
        provider=_safe_identifier(payload.get("provider")),
        summary="已配置" if configured else "未启用" if not enabled else "未配置",
        last_error=_safe_error(payload.get("last_error")),
    )


def _ocr_status(config: AppConfig | None) -> RuntimeProviderStatus:
    has_pillow = importlib.util.find_spec("PIL") is not None
    has_pytesseract = importlib.util.find_spec("pytesseract") is not None
    tesseract_cmd = _resolve_tesseract_cmd(config)
    has_tesseract_executable = bool(tesseract_cmd)
    version_ok = False
    if has_pillow and has_pytesseract and has_tesseract_executable:
        try:
            version_ok = _probe_tesseract_version(tesseract_cmd)
        except Exception:
            version_ok = False
    configured = has_pillow and has_pytesseract and has_tesseract_executable and version_ok
    timeout = config.ocr.timeout_seconds if config else 5
    language = config.ocr.language if config else "chi_sim+eng"
    tessdata_dir = _resolve_tessdata_dir(config)
    missing = []
    if not has_pillow:
        missing.append("pillow_missing")
    if not has_pytesseract:
        missing.append("pytesseract_missing")
    if has_pytesseract and not has_tesseract_executable:
        missing.append("tesseract_missing")
    if has_pillow and has_pytesseract and has_tesseract_executable and not version_ok:
        missing.append("tesseract_unavailable")
    return RuntimeProviderStatus(
        "ocr",
        "OCR",
        "ready" if configured else "unavailable",
        enabled=True,
        configured=configured,
        provider="pytesseract" if has_pytesseract else "none",
        summary="可用" if configured else "依赖或本地运行时不可用",
        timeout_seconds=max(1, int(timeout or 5)),
        last_error=_safe_error(";".join(missing)),
        notes=[f"lang {language}", "version probe ok", "custom tessdata"] if configured and tessdata_dir else [f"lang {language}", "version probe ok"] if configured else [f"lang {language}"],
    )


def _resolve_tesseract_cmd(config: AppConfig | None) -> str:
    configured = (config.ocr.tesseract_cmd if config else "").strip()
    if configured and Path(os.path.expandvars(configured)).is_file():
        return str(Path(os.path.expandvars(configured)))
    found = shutil.which("tesseract")
    return found or ""


def _resolve_tessdata_dir(config: AppConfig | None) -> str:
    configured = (config.ocr.tessdata_dir if config else "").strip()
    if configured and Path(os.path.expandvars(configured)).is_dir():
        return str(Path(os.path.expandvars(configured)))
    return ""


def _probe_tesseract_version(tesseract_cmd: str = "") -> bool:
    try:
        pytesseract = importlib.import_module("pytesseract")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        version_probe = getattr(pytesseract, "get_tesseract_version", None)
        if not callable(version_probe):
            return False
        return bool(str(version_probe()).strip())
    except Exception:
        return False


def _model_status(config: AppConfig | None, use: str) -> RuntimeProviderStatus:
    label = {"text": "Text Model", "vision": "Vision Model", "expression": "Expression Model"}.get(use, use.title())
    if config is None:
        return RuntimeProviderStatus(use, label, "off", provider="none", summary="未配置")
    llm = config.llm
    router = ModelRouter(llm)
    endpoint = router.resolve(use)
    if use == "vision":
        enabled = llm.vision_enabled
        configured = llm.is_vision_configured
    elif use == "expression":
        enabled = llm.expression_enabled
        configured = llm.is_expression_configured
    else:
        enabled = True
        configured = bool(llm.use_mock or llm.is_configured)
    if use == "text" and llm.use_mock:
        state = "mock"
        summary = "mock"
    elif configured:
        state = "ready"
        summary = "已配置"
    elif not enabled:
        state = "off"
        summary = "未启用"
    else:
        state = "error"
        summary = "未配置"
    return RuntimeProviderStatus(
        use,
        label,
        state,
        enabled=enabled,
        configured=configured,
        provider=_safe_identifier(llm.provider),
        model=_safe_model(endpoint.model if configured or state == "mock" else ""),
        summary=summary,
        last_error="" if configured or state == "mock" or not enabled else "model_unconfigured",
    )


def _computer_use_status(config: AppConfig | None) -> RuntimeProviderStatus:
    windows = sys.platform == "win32"
    settle_ms = config.computer_use.post_action_settle_ms if config else 200
    return RuntimeProviderStatus(
        "computer_use",
        "Computer Use",
        "ready" if windows else "unavailable",
        enabled=True,
        configured=windows,
        provider="windows" if windows else _safe_identifier(platform.system().lower() or "unknown"),
        summary="Windows 可用" if windows else "动作执行当前仅支持 Windows",
        limit=f"settle {max(0, int(settle_ms or 0))}ms",
        last_error="" if windows else "computer_use_windows_only",
        notes=["approval gated", "semi-automatic"],
    )


def _audit_verification_status() -> RuntimeProviderStatus:
    return RuntimeProviderStatus(
        "audit_verification",
        "Audit & Verification",
        "ready",
        enabled=True,
        configured=True,
        provider="local",
        summary="审计与本地图像验证可用",
        notes=["sanitized audit", "ocr/title/image signals", "no external model"],
    )


def _safe_identifier(value: Any) -> str:
    text = str(value or "none").strip().casefold()
    if not text:
        return "none"
    if any(token in text for token in ("sk-", "token", "secret", "key=")):
        return "redacted"
    if any(char in text for char in ("/", "\\", ":", "{", "}", "$", "%")):
        return "redacted"
    return text[:48]


def _safe_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered.startswith(("sk-", "${", "%")) or any(token in lowered for token in ("token", "secret", "api_key")):
        return "redacted"
    if any(char in text for char in ("/", "\\", "{", "}")):
        return "redacted"
    return text[:80]


def _safe_error(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    allowed = {
        "asr_unconfigured",
        "asr_disabled",
        "asr_config_error",
        "mock_asr_developer_only",
        "audio_too_large",
        "audio_decode_failed",
        "asr_timeout",
        "openai_package_missing",
        "empty_audio",
        "empty_transcript",
        "asr_failed",
        "tts_timeout",
        "tts_service_unavailable",
        "tts_config_error",
        "tts_failed",
        "ocr_dependency_missing",
        "pillow_missing",
        "pytesseract_missing",
        "tesseract_missing",
        "tesseract_unavailable",
        "computer_use_windows_only",
        "model_unconfigured",
    }
    parts = [part for part in text.replace(",", ";").split(";") if part]
    safe = [part for part in parts if part in allowed]
    return ";".join(safe[:3]) if safe else "runtime_status_unavailable"


def _format_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{round(value / 1024 / 1024)}MB"
    if value >= 1024:
        return f"{round(value / 1024)}KB"
    return f"{max(0, value)}B"
