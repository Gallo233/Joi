from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any, Callable


REQUIRED_MODULES = {
    "PySide6": "PySide6",
    "openai": "openai",
    "yaml": "PyYAML",
    "websockets": "websockets",
}

OPTIONAL_MODULES = {
    "PIL": "Pillow",
    "pytesseract": "pytesseract",
    "soundcard": "soundcard",
    "sounddevice": "sounddevice",
    "numpy": "numpy",
}

STATUS_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def build_doctor_report(
    workspace: Path | str | None = None,
    *,
    port: int = 8765,
    import_probe: Callable[[str], bool] | None = None,
    which_probe: Callable[[str], str | None] | None = None,
    port_probe: Callable[[int], bool] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    import_probe = import_probe or _module_available
    which_probe = which_probe or shutil.which
    port_probe = port_probe or _port_open
    env = env if env is not None else dict(os.environ)
    items: list[dict[str, str]] = []

    def add(status: str, category: str, name: str, summary: str, action: str = "") -> None:
        items.append(
            {
                "status": status,
                "category": category,
                "name": name,
                "summary": summary,
                "action": action,
            }
        )

    _check_project_files(root, add)
    _check_python(root, import_probe, add)
    _check_frontend(root, which_probe, add)
    _check_config(root, import_probe, which_probe, env, add)
    if port_probe(port):
        add("warn", "runtime", "core_port", f"127.0.0.1:{port} is already listening.", "Use -ReuseCore or stop the old Joi Core before a clean launch.")
    else:
        add("ok", "runtime", "core_port", f"127.0.0.1:{port} is available.")

    counts = {status: sum(1 for item in items if item["status"] == status) for status in STATUS_ORDER}
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return {
        "version": "joi.doctor.v1",
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "counts": counts,
        "items": items,
        "next_actions": _next_actions(items),
    }


def doctor_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi Doctor: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    for item in report.get("items", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(item.get("status"), "INFO")
        print(f"[{marker}] {item.get('category')}/{item.get('name')}: {item.get('summary')}")
        if item.get("action"):
            print(f"       next: {item.get('action')}")
    actions = report.get("next_actions") or []
    if actions:
        print("")
        print("Next actions:")
        for index, action in enumerate(actions, 1):
            print(f"{index}. {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local Joi Windows runtime readiness.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)
    report = build_doctor_report(Path(args.workspace), port=args.port)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return doctor_exit_code(report)


def _check_project_files(root: Path, add: Callable[[str, str, str, str, str], None]) -> None:
    required = [
        "README.md",
        "config.example.yaml",
        "requirements.txt",
        "agent_companion/shell/package.json",
        "agent_companion/shell/src-tauri/tauri.conf.json",
        "tools/start_joi.ps1",
    ]
    for relative in required:
        path = root / relative
        add("ok" if path.is_file() else "fail", "project", relative, "Found." if path.is_file() else "Missing.", "Restore the repository file before packaging." if not path.is_file() else "")


def _check_python(root: Path, import_probe: Callable[[str], bool], add: Callable[[str, str, str, str, str], None]) -> None:
    venv_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_python.is_file():
        add("ok", "python", "venv", "Local virtual environment exists.")
    else:
        add("fail", "python", "venv", "Local virtual environment is missing.", "Run: py -3 -m venv .venv")
    for module, package in REQUIRED_MODULES.items():
        if import_probe(module):
            add("ok", "python", package, "Installed.")
        else:
            add("fail", "python", package, "Missing required package.", "Run: .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    for module, package in OPTIONAL_MODULES.items():
        if import_probe(module):
            add("ok", "optional", package, "Installed.")
        else:
            add("warn", "optional", package, "Not installed.", _optional_install_action(package))


def _check_frontend(root: Path, which_probe: Callable[[str], str | None], add: Callable[[str, str, str, str, str], None]) -> None:
    shell_dir = root / "agent_companion" / "shell"
    npm = _first_tool(which_probe, "npm.cmd", "npm")
    cargo = _first_tool(which_probe, "cargo.exe", "cargo")
    release_shell = shell_dir / "src-tauri" / "target" / "release" / "joi-shell.exe"
    debug_shell = shell_dir / "src-tauri" / "target" / "debug" / "joi-shell.exe"
    node_modules = shell_dir / "node_modules"
    add("ok" if npm else "warn", "frontend", "npm", "Available." if npm else "Not found in PATH.", "Install Node or use tools/install_windows_toolchain.ps1." if not npm else "")
    add("ok" if cargo else "warn", "frontend", "cargo", "Available." if cargo else "Not found in PATH.", "Install Rust or use tools/install_windows_toolchain.ps1." if not cargo else "")
    add("ok" if node_modules.is_dir() else "warn", "frontend", "node_modules", "Installed." if node_modules.is_dir() else "Missing.", "Run: cd agent_companion\\shell; npm install" if not node_modules.is_dir() else "")
    if release_shell.is_file():
        add("ok", "frontend", "desktop_shell", "Release shell is built.")
    elif debug_shell.is_file():
        add("warn", "frontend", "desktop_shell", "Only debug shell is built.", "Build release before creating a stable desktop shortcut.")
    elif npm:
        add("warn", "frontend", "desktop_shell", "No built shell; launcher will use Tauri dev mode.", "Run: cd agent_companion\\shell; npm run tauri -- build --debug")
    else:
        add("fail", "frontend", "desktop_shell", "No built shell and no npm fallback.", "Install frontend toolchain, then build the shell.")


def _check_config(
    root: Path,
    import_probe: Callable[[str], bool],
    which_probe: Callable[[str], str | None],
    env: dict[str, str],
    add: Callable[[str, str, str, str, str], None],
) -> None:
    config_path = root / "config.yaml"
    if not config_path.is_file():
        add("warn", "config", "config.yaml", "Missing; Joi will run with limited defaults.", "Create config.yaml from config.example.yaml for real providers.")
        return
    add("ok", "config", "config.yaml", "Present.")
    if not import_probe("yaml"):
        add("warn", "config", "parse", "Cannot parse config until PyYAML is installed.", "Run: .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return
    try:
        import yaml

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        add("fail", "config", "parse", "config.yaml cannot be parsed.", "Fix YAML syntax before launching Joi.")
        return
    if not isinstance(raw, dict):
        add("fail", "config", "shape", "config.yaml root must be a mapping.", "Restore the config structure from config.example.yaml.")
        return
    characters = raw.get("characters")
    has_characters = isinstance(characters, list) and bool(characters)
    add("ok" if has_characters else "warn", "config", "characters", "At least one character configured." if has_characters else "No character rows configured.", "" if has_characters else "Add a characters list or copy config.example.yaml.")
    _check_llm_config(raw.get("llm") or {}, env, add)
    _check_asr_config(raw.get("asr") or {}, env, add)
    _check_tts_config(raw.get("tts") or {}, add)
    _check_ocr_config(raw.get("ocr") or {}, which_probe, add)


def _check_llm_config(llm: Any, env: dict[str, str], add: Callable[[str, str, str, str, str], None]) -> None:
    row = llm if isinstance(llm, dict) else {}
    use_mock = bool(row.get("use_mock", True))
    configured = _secret_configured(str(row.get("api_key", "") or ""), env) and bool(str(row.get("base_url", "") or "").strip()) and bool(str(row.get("model", "") or "").strip())
    if use_mock:
        add("warn", "config", "llm", "Text model is in mock mode.", "Set llm.use_mock=false and configure model credentials for real chat.")
    elif configured:
        add("ok", "config", "llm", "Text model appears configured.")
    else:
        add("warn", "config", "llm", "Text model is enabled but credentials or model fields are incomplete.", "Fill llm base_url, model, and api_key or env placeholder.")
    for route in ("vision", "expression"):
        if bool(row.get(f"{route}_enabled", False)):
            ready = bool(str(row.get(f"{route}_base_url", "") or "").strip()) and bool(str(row.get(f"{route}_model", "") or "").strip()) and _secret_configured(str(row.get(f"{route}_api_key", "") or ""), env)
            add("ok" if ready else "warn", "config", f"{route}_model", "Configured." if ready else "Enabled but incomplete.", f"Fill llm.{route}_base_url, llm.{route}_model, and llm.{route}_api_key." if not ready else "")


def _check_asr_config(asr: Any, env: dict[str, str], add: Callable[[str, str, str, str, str], None]) -> None:
    row = asr if isinstance(asr, dict) else {}
    if not bool(row.get("enabled", False)):
        add("warn", "config", "asr", "Voice input is disabled.", "Enable asr and set provider credentials to use microphone input.")
        return
    provider = str(row.get("provider", "") or "").strip()
    configured = bool(provider and str(row.get("base_url", "") or "").strip() and str(row.get("model", "") or "").strip() and _secret_configured(str(row.get("api_key", "") or ""), env))
    add("ok" if configured else "warn", "config", "asr", "ASR appears configured." if configured else "ASR is enabled but incomplete.", "Fill asr provider, base_url, model, and api_key." if not configured else "")


def _check_tts_config(tts: Any, add: Callable[[str, str, str, str, str], None]) -> None:
    row = tts if isinstance(tts, dict) else {}
    if not bool(row.get("enabled", False)):
        add("warn", "config", "tts", "TTS is disabled.", "Enable tts only after GPT-SoVITS or another provider is reachable.")
        return
    provider = str(row.get("provider", "") or "").strip()
    endpoint = str(row.get("server_url", "") or "").strip()
    add("ok" if provider and endpoint else "warn", "config", "tts", "TTS endpoint appears configured." if provider and endpoint else "TTS is enabled but provider endpoint is incomplete.", "Fill tts.provider and tts.server_url." if not (provider and endpoint) else "")


def _check_ocr_config(ocr: Any, which_probe: Callable[[str], str | None], add: Callable[[str, str, str, str, str], None]) -> None:
    row = ocr if isinstance(ocr, dict) else {}
    configured_path = str(row.get("tesseract_cmd", "") or "").strip()
    tesseract_available = bool(configured_path or _first_tool(which_probe, "tesseract.exe", "tesseract"))
    add("ok" if tesseract_available else "warn", "config", "tesseract", "Tesseract command is available." if tesseract_available else "Tesseract command not found.", "" if tesseract_available else "Install Tesseract or set ocr.tesseract_cmd.")


def _optional_install_action(package: str) -> str:
    if package in {"Pillow", "pytesseract"}:
        return "Run: .venv\\Scripts\\python.exe -m pip install -r requirements-ocr.txt"
    if package in {"soundcard", "sounddevice", "numpy"}:
        return "Run: .venv\\Scripts\\python.exe -m pip install -r requirements-audio.txt"
    return ""


def _secret_configured(value: str, env: dict[str, str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("${") and text.endswith("}"):
        key = text[2:-1].strip()
        return bool(key and env.get(key))
    if text.startswith("%") and text.endswith("%"):
        key = text[1:-1].strip()
        return bool(key and env.get(key))
    return text.lower() not in {"changeme", "your-api-key", "placeholder"}


def _next_actions(items: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    for status in ("fail", "warn"):
        for item in items:
            action = item.get("action", "")
            if item.get("status") == status and action and action not in actions:
                actions.append(action)
    return actions[:8]


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _first_tool(which_probe: Callable[[str], str | None], *names: str) -> str:
    for name in names:
        found = which_probe(name)
        if found:
            return found
    return ""


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.2):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
