from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.speech_input import AsrRuntimeState, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from agent_companion.core.watch_transcript import probe_system_audio_readiness


PREFLIGHT_VERSION = "joi.provider_preflight.v1"
REQUIRED_DEMO_PROVIDERS = {"fast", "computer_use", "audit_verification"}
OPTIONAL_DEMO_PROVIDERS = {"reasoning", "vision", "code", "summarize", "voice_style", "asr", "tts", "ocr", "system_audio"}


def build_provider_preflight_report(workspace: Path | str | None = None) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    config_exists = (root / "config.yaml").is_file()
    asr_state = _safe_asr_state(root)
    tts_status = _safe_tts_status(root)
    runtime = build_runtime_status(root, asr_state, tts_status)
    rows = [_preflight_row(row) for row in runtime.get("providers", []) if isinstance(row, dict)]
    rows.append(_system_audio_preflight_row())
    checks = [_check_from_row(row, config_exists=config_exists) for row in rows]
    if not config_exists:
        checks.insert(
            0,
            {
                "status": "warn",
                "name": "config",
                "summary": "config.yaml is missing; Joi can run only in limited defaults.",
                "action": "Create config.yaml from config.example.yaml before real provider demos.",
            },
        )
    status = _overall_status(checks)
    counts = {name: sum(1 for item in checks if item["status"] == name) for name in ("ok", "warn", "fail")}
    return {
        "version": PREFLIGHT_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "counts": counts,
        "checks": checks,
        "providers": rows,
        "next_actions": _next_actions(checks),
    }


def provider_preflight_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi Provider Preflight: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    for item in report.get("checks", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(item.get("status"), "INFO")
        print(f"[{marker}] {item.get('name')}: {item.get('summary')}")
        if item.get("action"):
            print(f"       next: {item.get('action')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a safe offline provider readiness preflight for Joi.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_provider_preflight_report(Path(args.workspace))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return provider_preflight_exit_code(report)


def _safe_asr_state(root: Path) -> AsrRuntimeState:
    try:
        _, state = build_asr_provider(root)
        return state
    except Exception:
        return AsrRuntimeState(False, False, "none", error="asr_config_error")


def _safe_tts_status(root: Path) -> dict[str, Any]:
    try:
        bridge = TtsBridge(root)
        try:
            return bridge.status_payload()
        finally:
            bridge.shutdown()
    except Exception:
        return {"enabled": False, "configured": False, "provider": "none", "last_error": "tts_config_error"}


def _preflight_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _safe_text(row.get("name"), "unknown"),
        "label": _safe_text(row.get("label"), "Provider"),
        "state": _safe_state(row.get("state")),
        "enabled": bool(row.get("enabled")),
        "configured": bool(row.get("configured")),
        "provider": _safe_text(row.get("provider"), "none"),
        "model": _safe_text(row.get("model"), ""),
        "summary": _safe_text(row.get("summary"), ""),
        "last_error": _safe_text(row.get("last_error"), ""),
    }


def _system_audio_preflight_row() -> dict[str, Any]:
    diagnostics = probe_system_audio_readiness()
    ready = diagnostics.get("status") == "ready"
    return {
        "name": "system_audio",
        "label": "System Audio Transcript",
        "state": "ready" if ready else "unavailable",
        "enabled": True,
        "configured": ready,
        "provider": "loopback",
        "model": "",
        "summary": "Loopback audio source is available." if ready else "Loopback audio source is unavailable.",
        "last_error": "" if ready else _safe_text(diagnostics.get("status"), "system_audio_unavailable"),
    }


def _check_from_row(row: dict[str, Any], *, config_exists: bool) -> dict[str, str]:
    name = row["name"]
    state = row["state"]
    label = row["label"]
    if name in REQUIRED_DEMO_PROVIDERS:
        if not config_exists and name == "fast":
            return {"status": "warn", "name": name, "summary": f"{label} needs config.yaml for real demos.", "action": _action_for(name, config_exists)}
        if state in {"ready", "mock"}:
            return {"status": "ok", "name": name, "summary": f"{label} is available.", "action": ""}
        return {"status": "fail", "name": name, "summary": f"{label} is not ready.", "action": _action_for(name, config_exists)}
    if name in OPTIONAL_DEMO_PROVIDERS:
        if state in {"ready", "mock", "off"}:
            status = "ok" if state in {"ready", "mock"} else "warn"
            summary = f"{label} is {state}."
            return {"status": status, "name": name, "summary": summary, "action": "" if status == "ok" else _action_for(name, config_exists)}
        return {"status": "warn", "name": name, "summary": f"{label} is unavailable.", "action": _action_for(name, config_exists)}
    return {"status": "ok" if state in {"ready", "mock", "off"} else "warn", "name": name, "summary": f"{label} is {state}.", "action": ""}


def _action_for(name: str, config_exists: bool) -> str:
    if not config_exists:
        return "Create config.yaml from config.example.yaml before real provider demos."
    actions = {
        "fast": "Configure llm.use_mock=true for local demo or provide a real text model.",
        "reasoning": "Configure llm.routes.reasoning or let it fall back to the base text model.",
        "vision": "Configure the vision model before watch/video demos.",
        "code": "Configure llm.routes.code for coding demos when a separate code model is required.",
        "summarize": "Configure llm.routes.summarize or let it fall back to the base text model.",
        "voice_style": "Configure expression/voice_style model for richer emotion sync.",
        "asr": "Configure ASR provider credentials before voice-input demos.",
        "tts": "Configure GPT-SoVITS or disable voice output for text-only demos.",
        "ocr": "Install OCR dependencies and Tesseract before visual grounding demos.",
        "system_audio": "Install requirements-audio.txt and enable a Windows loopback-capable playback device before realtime watch demos.",
        "computer_use": "Run Computer Use on Windows with the desktop shell.",
        "audit_verification": "Restore local audit and verification modules.",
    }
    return actions.get(name, "")


def _overall_status(checks: list[dict[str, str]]) -> str:
    if any(item["status"] == "fail" for item in checks):
        return "fail"
    if any(item["status"] == "warn" for item in checks):
        return "warn"
    return "ok"


def _next_actions(checks: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    for status in ("fail", "warn"):
        for item in checks:
            action = item.get("action", "")
            if item.get("status") == status and action and action not in actions:
                actions.append(action)
    return actions[:8]


def _safe_state(value: Any) -> str:
    text = _safe_text(value, "unavailable")
    return text if text in {"ready", "mock", "off", "error", "unavailable"} else "unavailable"


def _safe_text(value: Any, default: str) -> str:
    text = str(value or default).strip()
    if any(fragment in text for fragment in ("sk-", "token", "secret", "api_key", "http://", "https://", "C:\\", "/Users/")):
        return "redacted"
    if any(char in text for char in ("\\", "/", "{", "}")):
        return "redacted"
    return text[:96]


if __name__ == "__main__":
    raise SystemExit(main())
