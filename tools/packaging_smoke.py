from __future__ import annotations

import argparse
import json
import plistlib
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

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
    handoff_report_path = root / "tools" / "windows_handoff_report.py"
    release_check_path = root / "tools" / "windows_release_check.py"
    setup_wizard_path = root / "tools" / "windows_setup_wizard.py"
    core_builder_path = root / "tools" / "build_core_sidecar.py"
    core_smoke_path = root / "tools" / "smoke_core_sidecar.py"
    build_requirements_path = root / "requirements-build.txt"
    release_assets_path = shell_dir / "release-assets.json"
    release_asset_verifier_path = shell_dir / "scripts" / "verify-release-assets.mjs"
    mac_info_plist_path = tauri_path.parent / "Info.plist"
    privacy_notice_path = root / "docs" / "PRIVACY.md"
    third_party_notices_path = root / "docs" / "THIRD_PARTY_NOTICES.md"

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
            "windows_handoff_report": handoff_report_path,
            "windows_release_check": release_check_path,
            "windows_setup_wizard": setup_wizard_path,
            "core_sidecar_builder": core_builder_path,
            "core_sidecar_smoke": core_smoke_path,
            "build_requirements": build_requirements_path,
            "release_asset_manifest": release_assets_path,
            "release_asset_verifier": release_asset_verifier_path,
            "mac_info_plist": mac_info_plist_path,
            "privacy_notice": privacy_notice_path,
            "third_party_notices": third_party_notices_path,
        },
        add,
    )
    _check_mac_info_plist(mac_info_plist_path, add)
    if package and tauri and cargo:
        _check_versions(package, tauri, cargo, add)
    if package:
        scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
        frontend_build = str(scripts.get("build") or "")
        _expect("vue-tsc --noEmit" in frontend_build and "vite build" in frontend_build, add, "frontend_build_script", "Frontend build script type-checks and builds Vite.", "Keep npm run build wired to vue-tsc plus Vite build.")
        _expect(scripts.get("tauri") == "tauri", add, "tauri_script", "Tauri CLI script is present.", "Expose Tauri through npm run tauri for local and CI reuse.")
        _expect(bool(scripts.get("core:bundle")), add, "core_sidecar_script", "Standalone Joi Core build script is present.", "Restore the PyInstaller sidecar build script.")
        dev_command = str(scripts.get("dev") or "")
        _expect(
            "core:bundle" not in dev_command and "vite" in dev_command,
            add,
            "fast_debug_start",
            "Debug startup skips the packaged Core build and starts Vite directly.",
            "Keep PyInstaller Core assembly in build:release; Debug runs the source Core.",
        )
        release_build = str(scripts.get("build:release") or "")
        _expect("core:bundle" in release_build and "assets:verify" in release_build, add, "release_build_gate", "Release build requires the Core sidecar and pinned assets.", "Require both the Core sidecar and release asset verification before Tauri packaging.")
    if tauri:
        _check_tauri_config(tauri, add)
    if capabilities:
        _check_capabilities(capabilities, add)
    if start_bat_path.is_file() and start_ps1_path.is_file():
        _check_launcher(start_bat_path, start_ps1_path, add)
    _check_release_privacy_policy(add)
    _check_shipped_notices(root, add)
    _check_game_adapter_bundle(root, add)
    _check_capability_dependencies(root, add)

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


def _check_mac_info_plist(path: Path, add: Any) -> None:
    if not path.is_file():
        return
    try:
        payload = plistlib.loads(path.read_bytes())
    except Exception:
        add("fail", "mac_privacy_descriptions", "Info.plist is invalid.", "Restore a valid macOS Info.plist.")
        return
    required = {
        "NSMicrophoneUsageDescription",
        "NSAudioCaptureUsageDescription",
        "NSAppleEventsUsageDescription",
    }
    available = {key for key in required if str(payload.get(key) or "").strip()}
    _expect(
        available == required,
        add,
        "mac_privacy_descriptions",
        "macOS microphone, audio capture, and Apple Events purpose strings are present.",
        "Add non-empty microphone, audio capture, and Apple Events purpose strings to src-tauri/Info.plist.",
    )


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
    _expect(build.get("beforeBuildCommand") == "npm run build:release", add, "tauri_before_build", "Tauri build runs the release-gated build first.", "Keep beforeBuildCommand as npm run build:release.")
    _expect(build.get("frontendDist") == "../dist", add, "tauri_frontend_dist", "Tauri uses the Vite dist directory.", "Keep frontendDist pointed at ../dist.")
    _expect(main_window.get("label") == "main", add, "main_window_label", "Main window label is stable.", "Keep the main window label as main for capability matching.")
    _expect(main_window.get("decorations") is True and main_window.get("titleBarStyle") == "Overlay", add, "native_window_chrome", "Main shell uses native window controls with an overlay title bar.", "Keep native decorations and the overlay title bar for correct macOS traffic lights.")
    _expect(int(main_window.get("width", 0) or 0) >= 1000 and int(main_window.get("height", 0) or 0) >= 700, add, "window_size", "Default desktop window size is release-ready.", "Keep the default shell window large enough for chat and stage panes.")
    bundle = tauri.get("bundle") if isinstance(tauri.get("bundle"), dict) else {}
    resources = bundle.get("resources") if isinstance(bundle.get("resources"), dict) else {}
    _expect(
        resources.get("binaries/joi-core-runtime/") == "joi-core-runtime/",
        add,
        "core_sidecar_bundle",
        "Tauri bundles the fast-start Joi Core runtime directory.",
        "Map binaries/joi-core-runtime/ to joi-core-runtime/ in bundle.resources.",
    )


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


def _check_game_adapter_bundle(root: Path, add: Any) -> None:
    """Whether a package built from this tree would carry a runnable game adapter.

    The Core sidecar copies `adapters/` verbatim, but the bridge's built bundle
    is gitignored -- so a fresh clone packages an adapter that can never start.
    Core fails closed and reports it unavailable rather than misbehaving, which
    is why this warns rather than fails: it is a capability the release would
    silently omit, not a broken one it would ship.
    """

    bridge = root / "agent_companion" / "adapters" / "minecraft-bridge"
    if not (bridge / "index.js").is_file():
        return
    bundle = bridge / "dist" / "index.js"
    vendor = bridge / "dist" / "vendor" / "minecraft-data" / "package.json"
    runtime = bridge / "dist" / "runtime"
    if bundle.is_file() and vendor.is_file() and runtime.is_dir():
        add("ok", "game_adapter_bundle", "Minecraft bridge bundle, pinned data and Node runtime are staged for packaging.")
        return
    add(
        "warn",
        "game_adapter_bundle",
        "Minecraft bridge has no built bundle, so a package from this tree would report the game adapter unavailable.",
        "Run `npm ci && npm run build` in agent_companion/adapters/minecraft-bridge, or state in the release notes that the package ships without game capability.",
    )


def _check_capability_dependencies(root: Path, add: Any) -> None:
    """Whether a package built from this tree could see the screen at all.

    Pillow sat in the optional OCR list, which no release installed, while
    `vision/mac.py` imports it at module scope -- so a signed build reported its
    screen observer unavailable and meant it, while the development environment
    captured fine. The same shape hid the macOS display geometry package, whose
    absence makes Computer Use refuse to derive a click point.

    Both are declared dependencies now; this is what keeps them declared.
    """

    # This asks what a release would install, so it only speaks about a tree that
    # configures releases. Packaged and fixture trees carry stub requirements
    # files that say nothing about the dependency set, and judging those would
    # report a defect about a file nobody ships.
    workflow = root / ".github" / "workflows" / "release-macos.yml"
    requirements = root / "requirements.txt"
    if not workflow.is_file() or not requirements.is_file():
        return
    declared = requirements.read_text(encoding="utf-8").lower()
    _expect(
        "pillow" in declared,
        add,
        "capture_dependency",
        "Screen capture's imaging dependency ships with every build.",
        "Declare Pillow in requirements.txt: vision/mac.py imports it at module scope.",
    )

    # The install line, not any mention of it: a comment naming the file reads
    # the same to a substring search, which is how this check first passed a
    # workflow that had stopped installing it.
    installs = [line for line in workflow.read_text(encoding="utf-8").splitlines() if "pip install" in line]
    _expect(
        any("requirements-macos.txt" in line for line in installs),
        add,
        "display_geometry_dependency",
        "The macOS release installs the display geometry package.",
        "Install requirements-macos.txt in release-macos.yml, or multi-display coordinates stay untrusted.",
    )

    bundled = root / "agent_companion" / "shell" / "src-tauri" / "binaries" / "joi-core-runtime" / "_internal"
    if not bundled.is_dir():
        return
    missing = [name for name in ("PIL", "Quartz") if not (bundled / name).exists()]
    if missing:
        add(
            "warn",
            "capability_bundle",
            f"The built sidecar is missing {', '.join(missing)}, so a package from it could not capture the screen.",
            "Rebuild the sidecar after installing requirements.txt and requirements-macos.txt.",
        )
        return
    add("ok", "capability_bundle", "The built sidecar carries the capture and display geometry packages.")


def _check_shipped_notices(root: Path, add: Any) -> None:
    """The notices have to reach the product, not only the repository.

    Joi ships without a Live2D Expandable Application agreement, so the
    obligation that remains is the copyright notice -- and a notice that lives
    only in `docs/` reaches people who cloned the source, not people who
    downloaded the DMG. Read the build scripts and the panel itself: a check
    that only asked whether the notices file exists passed the whole time it
    was unreachable.
    """

    package_path = root / "agent_companion" / "shell" / "package.json"
    app_path = root / "agent_companion" / "shell" / "src" / "App.vue"
    scripts: dict[str, Any] = {}
    if package_path.is_file():
        loaded = json.loads(package_path.read_text(encoding="utf-8")).get("scripts")
        scripts = loaded if isinstance(loaded, dict) else {}
    synced = all("legal:sync" in str(scripts.get(name) or "") for name in ("build", "build:release"))
    _expect(
        synced,
        add,
        "legal_notice_sync",
        "Every shell build copies the licence and third-party notices into the bundle.",
        "Keep npm run legal:sync in both build and build:release.",
    )
    app_source = app_path.read_text(encoding="utf-8") if app_path.is_file() else ""
    shown = (
        "activeSettingsTab === 'about'" in app_source
        and "Live2D Inc." in app_source
        and "generated/THIRD_PARTY_NOTICES.md?raw" in app_source
    )
    _expect(
        shown,
        add,
        "about_panel_notices",
        "The About panel names the default character's copyright holder and carries the notices.",
        "Restore the About settings panel, its Live2D attribution, and the bundled notices.",
    )


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
        value = _loads_toml(path.read_text(encoding="utf-8"))
    except ValueError:
        add("fail", name, "Invalid TOML.", "Fix the TOML before packaging.")
        return {}
    add("ok", name, "Parsed.")
    return value


def _loads_toml(text: str) -> dict[str, Any]:
    if tomllib is not None:
        return tomllib.loads(text)
    result: dict[str, Any] = {}
    section: dict[str, Any] = result
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = result
            for part in line.strip("[]").split("."):
                section = section.setdefault(part.strip(), {})
            continue
        if "=" not in line:
            continue
        key, raw_value = (part.strip() for part in line.split("=", 1))
        section[key] = _loads_toml_scalar(raw_value)
    return result


def _loads_toml_scalar(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        return value


def _expect(condition: bool, add: Any, name: str, ok_summary: str, fail_action: str) -> None:
    add("ok" if condition else "fail", name, ok_summary if condition else "Check failed.", "" if condition else fail_action)


if __name__ == "__main__":
    raise SystemExit(main())
