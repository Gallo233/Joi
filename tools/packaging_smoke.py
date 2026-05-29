from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib
from typing import Any

try:
    from tools.package_windows_release import build_release_privacy_report
except ModuleNotFoundError:
    from package_windows_release import build_release_privacy_report


SMOKE_VERSION = "joi.packaging_smoke.v1"


def build_packaging_smoke_report(workspace: Path | str | None = None) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    items: list[dict[str, str]] = []

    def add(status: str, name: str, summary: str, action: str = "") -> None:
        items.append({"status": status, "name": name, "summary": summary, "action": action})

    shell_dir = root / "agent_companion" / "shell"
    package_path = shell_dir / "package.json"
    package_lock_path = shell_dir / "package-lock.json"
    tauri_path = shell_dir / "src-tauri" / "tauri.conf.json"
    cargo_path = shell_dir / "src-tauri" / "Cargo.toml"
    capability_path = shell_dir / "src-tauri" / "capabilities" / "default.json"
    start_bat_path = root / "start_joi.bat"
    start_ps1_path = root / "tools" / "start_joi.ps1"
    doctor_path = root / "tools" / "joi_doctor.py"
    demo_check_path = root / "tools" / "mvp_demo_check.py"
    release_packager_path = root / "tools" / "package_windows_release.py"
    provider_preflight_path = root / "tools" / "provider_preflight.py"
    release_check_path = root / "tools" / "windows_release_check.py"
    setup_wizard_path = root / "tools" / "windows_setup_wizard.py"

    package = _read_json(package_path, add, "package_json")
    tauri = _read_json(tauri_path, add, "tauri_config")
    capabilities = _read_json(capability_path, add, "tauri_capabilities")
    cargo = _read_toml(cargo_path, add, "cargo_manifest")

    _check_required_files(
        {
            "package_lock": package_lock_path,
            "start_joi_bat": start_bat_path,
            "start_joi_ps1": start_ps1_path,
            "joi_doctor": doctor_path,
            "mvp_demo_check": demo_check_path,
            "windows_release_packager": release_packager_path,
            "provider_preflight": provider_preflight_path,
            "windows_release_check": release_check_path,
            "windows_setup_wizard": setup_wizard_path,
        },
        add,
    )
    if package and tauri and cargo:
        _check_versions(package, tauri, cargo, add)
    if package:
        scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
        _expect(scripts.get("build") == "vue-tsc --noEmit && vite build", add, "frontend_build_script", "Frontend build script type-checks and builds Vite.", "Keep npm run build as vue-tsc plus Vite build.")
        _expect(scripts.get("tauri") == "tauri", add, "tauri_script", "Tauri CLI script is present.", "Expose Tauri through npm run tauri for local and CI reuse.")
    if tauri:
        _check_tauri_config(tauri, add)
    if capabilities:
        _check_capabilities(capabilities, add)
    if start_bat_path.is_file() and start_ps1_path.is_file():
        _check_launcher(start_bat_path, start_ps1_path, add)
    _check_release_privacy_policy(add)

    counts = {status: sum(1 for item in items if item["status"] == status) for status in ("ok", "warn", "fail")}
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return {
        "version": SMOKE_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "counts": counts,
        "items": items,
        "next_actions": [item["action"] for item in items if item.get("action")][:8],
    }


def packaging_smoke_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi Packaging Smoke: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    for item in report.get("items", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(item.get("status"), "INFO")
        print(f"[{marker}] {item.get('name')}: {item.get('summary')}")
        if item.get("action"):
            print(f"       next: {item.get('action')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Joi packaging metadata before a Windows release.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_packaging_smoke_report(Path(args.workspace))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return packaging_smoke_exit_code(report)


def _check_required_files(paths: dict[str, Path], add: Any) -> None:
    for name, path in paths.items():
        _expect(path.is_file(), add, name, "Found.", "Restore the packaging file before release.")


def _check_versions(package: dict[str, Any], tauri: dict[str, Any], cargo: dict[str, Any], add: Any) -> None:
    package_version = str(package.get("version", "") or "")
    tauri_version = str(tauri.get("version", "") or "")
    cargo_version = str(cargo.get("package", {}).get("version", "") or "")
    _expect(package_version == tauri_version == cargo_version and bool(package_version), add, "version_alignment", f"All package versions are {package_version}.", "Keep package.json, tauri.conf.json, and Cargo.toml versions aligned.")


def _check_tauri_config(tauri: dict[str, Any], add: Any) -> None:
    build = tauri.get("build") if isinstance(tauri.get("build"), dict) else {}
    app = tauri.get("app") if isinstance(tauri.get("app"), dict) else {}
    windows = app.get("windows") if isinstance(app.get("windows"), list) else []
    main_window = windows[0] if windows and isinstance(windows[0], dict) else {}
    _expect(tauri.get("productName") == "Joi", add, "product_name", "Product name is Joi.", "Set Tauri productName to Joi.")
    _expect(bool(str(tauri.get("identifier", "")).strip()), add, "app_identifier", "Application identifier is set.", "Set a stable Tauri identifier.")
    _expect(build.get("beforeBuildCommand") == "npm run build", add, "tauri_before_build", "Tauri build runs frontend build first.", "Keep beforeBuildCommand as npm run build.")
    _expect(build.get("frontendDist") == "../dist", add, "tauri_frontend_dist", "Tauri uses the Vite dist directory.", "Keep frontendDist pointed at ../dist.")
    _expect(main_window.get("label") == "main", add, "main_window_label", "Main window label is stable.", "Keep the main window label as main for capability matching.")
    _expect(main_window.get("decorations") is False and main_window.get("transparent") is True, add, "frameless_window", "Main shell is configured as a transparent frameless window.", "Keep decorations=false and transparent=true for Joi shell chrome.")
    _expect(int(main_window.get("width", 0) or 0) >= 1000 and int(main_window.get("height", 0) or 0) >= 700, add, "window_size", "Default desktop window size is release-ready.", "Keep the default shell window large enough for chat and stage panes.")


def _check_capabilities(capabilities: dict[str, Any], add: Any) -> None:
    permissions = capabilities.get("permissions") if isinstance(capabilities.get("permissions"), list) else []
    windows = capabilities.get("windows") if isinstance(capabilities.get("windows"), list) else []
    required = {
        "core:window:allow-close",
        "core:window:allow-minimize",
        "core:window:allow-start-dragging",
        "core:window:allow-set-always-on-top",
        "core:window:allow-set-skip-taskbar",
    }
    _expect("main" in windows, add, "capability_window", "Capabilities target the main window.", "Bind default capability to the main window.")
    _expect(required.issubset(set(str(item) for item in permissions)), add, "window_permissions", "Window controls and compact mode permissions are present.", "Keep close/minimize/drag/always-on-top/skip-taskbar permissions enabled.")


def _check_launcher(start_bat_path: Path, start_ps1_path: Path, add: Any) -> None:
    bat = start_bat_path.read_text(encoding="utf-8", errors="ignore")
    ps1 = start_ps1_path.read_text(encoding="utf-8", errors="ignore")
    _expect("%*" in bat, add, "bat_argument_forwarding", "start_joi.bat forwards command-line arguments.", "Forward batch arguments so -Doctor and future launch switches work.")
    _expect("-Doctor" in ps1 and "joi_doctor.py" in ps1, add, "doctor_launcher", "PowerShell launcher exposes doctor mode.", "Keep start_joi.ps1 wired to tools/joi_doctor.py.")
    _expect("-Setup" in ps1 and "windows_setup_wizard.py" in ps1, add, "setup_launcher", "PowerShell launcher exposes first-run setup mode.", "Keep start_joi.ps1 wired to tools/windows_setup_wizard.py.")
    _expect("joi_core.err.log" in ps1 and "joi_core.out.log" in ps1, add, "core_logs", "Core stdout/stderr logs are configured.", "Keep Core logs under logs/ for shortcut debugging.")


def _check_release_privacy_policy(add: Any) -> None:
    report = build_release_privacy_report()
    ok = report.get("status") == "ok" and not report.get("unprotected_samples")
    count = int(report.get("protected_sample_count", 0) or 0)
    _expect(ok, add, "release_privacy_policy", f"Release packager protects {count} local-only sample paths.", "Restore package_windows_release forbidden names, suffixes, and directory rules before release.")


def _read_json(path: Path, add: Any, name: str) -> dict[str, Any]:
    if not path.is_file():
        add("fail", name, "Missing.", "Restore the file before packaging.")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        add("fail", name, "Invalid JSON.", "Fix the JSON before packaging.")
        return {}
    if not isinstance(value, dict):
        add("fail", name, "JSON root is not an object.", "Restore the expected object shape.")
        return {}
    add("ok", name, "Parsed.")
    return value


def _read_toml(path: Path, add: Any, name: str) -> dict[str, Any]:
    if not path.is_file():
        add("fail", name, "Missing.", "Restore the file before packaging.")
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        add("fail", name, "Invalid TOML.", "Fix the TOML before packaging.")
        return {}
    add("ok", name, "Parsed.")
    return value


def _expect(condition: bool, add: Any, name: str, ok_summary: str, fail_action: str) -> None:
    add("ok" if condition else "fail", name, ok_summary if condition else "Check failed.", "" if condition else fail_action)


if __name__ == "__main__":
    raise SystemExit(main())
