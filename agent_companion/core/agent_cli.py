from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_PATH_RE = re.compile(r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/[^\s]+|\b[A-Za-z]:[\\/][^\s]+")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]+|\b(?:api[_-]?key|secret|token|bearer)\b", re.IGNORECASE)
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_REASONING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


@dataclass(frozen=True)
class AgentCliProfile:
    id: str
    name: str
    vendor: str
    command_names: tuple[str, ...]
    env_var: str = ""
    version_args: tuple[str, ...] = ("--version",)
    models: tuple[str, ...] = ("默认",)
    reasoning: tuple[str, ...] = ("默认",)
    run_strategy: str = "unsupported"
    prompt_args: tuple[str, ...] = ()
    installed_label: str = "未安装"
    supports_takeover: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


_PROFILES: tuple[AgentCliProfile, ...] = (
    AgentCliProfile(
        id="claude",
        name="Claude Code",
        vendor="Anthropic official CLI",
        command_names=("claude", "claude-code"),
        models=("默认", "Sonnet", "Opus"),
        reasoning=("默认",),
        run_strategy="prompt_arg",
        prompt_args=("-p",),
        installed_label="已安装",
        supports_takeover=True,
        notes=("external_agent_cli",),
    ),
    AgentCliProfile(
        id="codex",
        name="Codex CLI",
        vendor="OpenAI official CLI",
        command_names=("codex",),
        env_var="AGENT_COMPANION_CODEX_BIN",
        models=("默认",),
        reasoning=("默认", "Low", "Medium", "High", "XHigh"),
        run_strategy="codex_exec_json",
        installed_label="codex-cli",
        supports_takeover=True,
        notes=("joi_takeover_runner", "permission_bridge"),
    ),
    AgentCliProfile(
        id="gemini",
        name="Gemini CLI",
        vendor="Google agent CLI",
        command_names=("gemini",),
        models=("默认", "Gemini Pro", "Gemini Flash"),
        reasoning=("默认",),
        run_strategy="prompt_arg",
        prompt_args=("-p",),
        installed_label="已安装",
        supports_takeover=True,
        notes=("external_agent_cli",),
    ),
    AgentCliProfile(
        id="hermes",
        name="Hermes",
        vendor="ACP agent CLI",
        command_names=("hermes",),
        models=("默认",),
        reasoning=("默认",),
        installed_label="已安装",
        notes=("external_agent_cli",),
    ),
)


def scan_agent_clis() -> dict[str, Any]:
    rows = [_profile_state(profile) for profile in _PROFILES]
    selected = "codex" if any(row["id"] == "codex" and row["installed"] for row in rows) else _first_installed(rows)
    return {
        "ok": True,
        "mode": "local_cli",
        "selected": selected,
        "clis": rows,
    }


def test_agent_cli(cli_id: str) -> dict[str, Any]:
    profile = _profile_by_id(cli_id)
    if profile is None:
        return {"ok": False, "error": "unknown_cli", "summary": "未知 CLI。"}
    row = _profile_state(profile, probe=True)
    if not row["installed"]:
        return {"ok": False, "error": "cli_not_found", "summary": f"{profile.name} 未安装。", "cli": row}
    if row.get("probe_ok"):
        return {"ok": True, "summary": f"{profile.name} 测试通过。", "cli": row}
    return {"ok": False, "error": row.get("error") or "cli_probe_failed", "summary": f"{profile.name} 没有通过版本探测。", "cli": row}


def _profile_state(profile: AgentCliProfile, *, probe: bool = True) -> dict[str, Any]:
    executable = _resolve_executable(profile)
    installed = bool(executable)
    version = ""
    error = ""
    probe_ok = False
    if installed and probe:
        version, error = _probe_version(executable, profile.version_args)
        probe_ok = bool(version and not error)
    model_state = _model_state(profile, executable)
    return {
        "id": profile.id,
        "name": profile.name,
        "vendor": profile.vendor,
        "installed": installed,
        "version": version or (profile.installed_label if installed else ""),
        "status": "ready" if installed else "missing",
        "probe_ok": probe_ok,
        "error": error,
        **model_state,
        "reasoning": list(profile.reasoning),
        "run_strategy": profile.run_strategy,
        "supports_takeover": profile.supports_takeover,
        "notes": list(profile.notes),
    }


def _model_state(profile: AgentCliProfile, executable: str) -> dict[str, Any]:
    if profile.id == "codex":
        if not executable:
            return _fallback_model_state("cli_not_found")
        return _discover_codex_models(executable)
    models = list(profile.models) or ["默认"]
    return {
        "models": models,
        "model_options": [{"id": model, "label": model} for model in models],
        "models_source": "profile",
        "models_error": "",
    }


def _discover_codex_models(executable: str) -> dict[str, Any]:
    """Read Codex's public model catalog and return only display-safe fields."""
    try:
        result = subprocess.run(
            [executable, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except subprocess.TimeoutExpired:
        return _fallback_model_state("model_catalog_timeout")
    except OSError:
        return _fallback_model_state("model_catalog_launch_failed")
    if result.returncode != 0:
        return _fallback_model_state("model_catalog_nonzero_exit")
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _fallback_model_state("model_catalog_invalid")
    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        return _fallback_model_state("model_catalog_invalid")

    discovered: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, dict) or raw.get("visibility") not in (None, "list"):
            continue
        model_id = str(raw.get("slug") or "").strip()
        if not _MODEL_ID_RE.fullmatch(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        display_name = _safe_model_label(raw.get("display_name")) or model_id
        reasoning = _safe_reasoning_levels(raw.get("supported_reasoning_levels"))
        default_reasoning = str(raw.get("default_reasoning_level") or "").strip().casefold()
        if default_reasoning not in _REASONING_LEVELS:
            default_reasoning = ""
        try:
            priority = int(raw.get("priority", 10_000))
        except (TypeError, ValueError):
            priority = 10_000
        discovered.append(
            (
                priority,
                {
                    "id": model_id,
                    "label": display_name,
                    "reasoning": reasoning,
                    "default_reasoning": default_reasoning,
                },
            )
        )
    if not discovered:
        return _fallback_model_state("model_catalog_empty")
    discovered.sort(key=lambda item: (item[0], item[1]["label"].casefold()))
    options = [
        {"id": "默认", "label": "默认（使用 CLI 配置）", "reasoning": [], "default_reasoning": ""},
        *(option for _, option in discovered),
    ]
    return {
        "models": [option["id"] for option in options],
        "model_options": options,
        "models_source": "cli_live",
        "models_error": "",
    }


def _fallback_model_state(error: str) -> dict[str, Any]:
    return {
        "models": ["默认"],
        "model_options": [{"id": "默认", "label": "默认（使用 CLI 配置）", "reasoning": [], "default_reasoning": ""}],
        "models_source": "fallback",
        "models_error": error,
    }


def _safe_model_label(value: object) -> str:
    label = " ".join(str(value or "").split()).strip()
    if not label or _PATH_RE.search(label) or _SECRET_RE.search(label):
        return ""
    return label[:80]


def _safe_reasoning_levels(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    levels: list[str] = []
    for row in value:
        effort = str(row.get("effort") if isinstance(row, dict) else row).strip().casefold()
        if effort in _REASONING_LEVELS and effort not in levels:
            levels.append(effort)
    return levels


def agent_cli_profile(cli_id: str) -> AgentCliProfile | None:
    return _profile_by_id(cli_id)


def resolve_agent_cli_executable(cli_id: str) -> str:
    profile = _profile_by_id(cli_id)
    if profile is None:
        return ""
    return _resolve_executable(profile)


def _resolve_executable(profile: AgentCliProfile) -> str:
    env_names = [profile.env_var] if profile.env_var else []
    if profile.id == "codex":
        env_names.append("CODEX_CLI_PATH")
    for env_name in env_names:
        override = os.environ.get(env_name, "").strip()
        if override:
            return override if Path(override).is_file() else ""
    for command in profile.command_names:
        found = shutil.which(command)
        if found:
            return found
    for candidate in _candidate_executables(profile):
        if candidate.is_file():
            return str(candidate)
    return ""


def _candidate_executables(profile: AgentCliProfile) -> tuple[Path, ...]:
    if profile.id != "codex":
        return ()
    home = Path.home()
    return (
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
        home / ".codex" / "bin" / "codex",
        home / ".local" / "bin" / "codex",
        Path("/Applications/Codex.app/Contents/Resources/codex"),
    )


def _probe_version(executable: str, version_args: tuple[str, ...]) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [executable, *version_args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except OSError:
        return "", "launch_failed"
    output = " ".join((result.stdout or result.stderr or "").split())
    safe_output = _safe_display_text(output)
    if result.returncode != 0 and not safe_output:
        return "", "nonzero_exit"
    return safe_output[:96], "" if result.returncode == 0 else "nonzero_exit"


def _safe_display_text(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = _PATH_RE.sub("[path]", text)
    text = _SECRET_RE.sub("[secret]", text)
    if "[path]" in text or "[secret]" in text:
        return "版本已隐藏"
    return text[:120]


def _profile_by_id(cli_id: str) -> AgentCliProfile | None:
    normalized = re.sub(r"[^a-z0-9_-]+", "", str(cli_id or "").strip().casefold())
    for profile in _PROFILES:
        if profile.id == normalized:
            return profile
    return None


def _first_installed(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        if row.get("installed"):
            return str(row.get("id") or "")
    return "codex"
