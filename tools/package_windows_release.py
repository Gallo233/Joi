from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from typing import Any


PACKAGE_VERSION = "joi.windows_package.v1"
PRIVACY_POLICY_VERSION = "joi.windows_release_privacy.v1"
ROOT_FILES = [
    "README.md",
    "config.example.yaml",
    "secrets.example.yaml",
    "requirements.txt",
    "requirements-accessibility.txt",
    "requirements-audio.txt",
    "requirements-build.txt",
    "requirements-ocr.txt",
    "start_joi.bat",
]
ROOT_DIRS = [
    "agent_companion/config",
    "agent_companion/core",
    "agent_companion/docs",
    "docs",
]
SHELL_FILES = [
    "agent_companion/README.md",
    "agent_companion/shell/index.html",
    "agent_companion/shell/package.json",
    "agent_companion/shell/package-lock.json",
    "agent_companion/shell/release-assets.json",
    "agent_companion/shell/scripts/build-core-sidecar.mjs",
    "agent_companion/shell/scripts/verify-release-assets.mjs",
    "agent_companion/shell/tsconfig.json",
    "agent_companion/shell/vite.config.ts",
    "agent_companion/shell/src-tauri/build.rs",
    "agent_companion/shell/src-tauri/Cargo.lock",
    "agent_companion/shell/src-tauri/Cargo.toml",
    "agent_companion/shell/src-tauri/Info.plist",
    "agent_companion/shell/src-tauri/tauri.conf.json",
]
SHELL_DIRS = [
    "agent_companion/shell/src",
    "agent_companion/shell/src-tauri/capabilities",
    "agent_companion/shell/src-tauri/icons",
    "agent_companion/shell/src-tauri/src",
]
TOOLS_FILES = [
    "run_agent_companion_tests.py",
    "tools/build_core_sidecar.py",
    "tools/joi_doctor.py",
    "tools/mvp_demo_check.py",
    "tools/package_windows_release.py",
    "tools/packaging_smoke.py",
    "tools/provider_preflight.py",
    "tools/smoke_ws_bridge.py",
    "tools/smoke_core_sidecar.py",
    "tools/start_joi.ps1",
    "tools/windows_handoff_report.py",
    "tools/windows_release_check.py",
    "tools/windows_setup_wizard.py",
]
RELEASE_EXE = "agent_companion/shell/src-tauri/target/release/joi-shell.exe"
RELEASE_CORE_RUNTIME = "agent_companion/shell/src-tauri/target/release/joi-core-runtime"
FORBIDDEN_NAMES = {"config.yaml", "secrets.yaml", ".env"}
FORBIDDEN_SUFFIXES = (".local.yaml", ".pyc", ".log")
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", "data", "dist", "logs", "node_modules", "gen", "target"}
LOCAL_ONLY_SAMPLE_PATHS = [
    "config.yaml",
    "secrets.yaml",
    ".env",
    "config.local.yaml",
    "data/agent_companion/memory.sqlite3",
    "data/local_visual_eval/private_manifest.json",
    "logs/joi_core.err.log",
    "agent_companion/shell/node_modules/private.txt",
    "agent_companion/shell/dist/index.html",
    "agent_companion/shell/src-tauri/target/debug/joi-shell.exe",
    "agent_companion/shell/src-tauri/target/release/private.pdb",
    "agent_companion/core/__pycache__/app.pyc",
]


def build_windows_release_package(
    workspace: Path | str | None = None,
    *,
    output_dir: Path | str | None = None,
    dry_run: bool = False,
    require_exe: bool = True,
) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    version = _release_version(root)
    package_root = f"Joi-{version}-windows"
    output_root = Path(output_dir).resolve() if output_dir else root / "dist"
    zip_name = f"joi-windows-portable-{version}.zip"
    zip_path = output_root / zip_name
    errors: list[str] = []
    entries: list[tuple[Path, str]] = []

    for relative in ROOT_FILES + SHELL_FILES + TOOLS_FILES:
        _add_file(root, relative, entries, errors)
    for relative in ROOT_DIRS + SHELL_DIRS:
        _add_tree(root, relative, entries, errors)
    release_exe = root / RELEASE_EXE
    release_runtime = _release_runtime_source(root)
    if release_exe.is_file():
        _append_entry(root, release_exe, entries, errors, allow_release_exe=True)
    elif require_exe:
        errors.append("release_exe_missing")
    if _runtime_is_complete(release_runtime):
        _add_runtime_tree(root, release_runtime, entries, errors)
    elif require_exe:
        errors.append("release_sidecar_missing")

    deduped = _dedupe_entries(entries)
    forbidden_hits = _forbidden_hits([arc for _, arc in deduped])
    if forbidden_hits:
        errors.extend(f"forbidden_entry:{hit}" for hit in forbidden_hits[:8])
    privacy_report = build_release_privacy_report()
    if privacy_report["status"] != "ok":
        errors.append("release_privacy_policy_failed")

    manifest = {
        "version": PACKAGE_VERSION,
        "safe_for_display": True,
        "privacy_policy_version": privacy_report["version"],
        "release_version": version,
        "package_root": package_root,
        "entry_count": len(deduped) + 1,
        "includes_release_exe": release_exe.is_file(),
        "includes_core_sidecar": _runtime_is_complete(release_runtime),
        "forbidden_hits": forbidden_hits,
    }
    if not errors and not dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, arc in deduped:
                archive.write(source, f"{package_root}/{arc}")
            archive.writestr(f"{package_root}/RELEASE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        digest = _sha256(zip_path)
    else:
        digest = ""

    status = "fail" if errors else "ok"
    return {
        "version": PACKAGE_VERSION,
        "safe_for_display": True,
        "status": status,
        "release_version": version,
        "zip": "" if dry_run or errors else _safe_relative(root, zip_path),
        "sha256": digest,
        "entry_count": manifest["entry_count"],
        "includes_release_exe": manifest["includes_release_exe"],
        "includes_core_sidecar": manifest["includes_core_sidecar"],
        "privacy_policy": {
            "version": privacy_report["version"],
            "status": privacy_report["status"],
            "protected_sample_count": privacy_report["protected_sample_count"],
        },
        "dry_run": dry_run,
        "errors": errors,
        "next_actions": _next_actions(errors),
    }


def package_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def build_release_privacy_report() -> dict[str, Any]:
    unprotected = [relative for relative in LOCAL_ONLY_SAMPLE_PATHS if not _is_forbidden(relative)]
    status = "fail" if unprotected else "ok"
    return {
        "version": PRIVACY_POLICY_VERSION,
        "safe_for_display": True,
        "status": status,
        "protected_sample_count": len(LOCAL_ONLY_SAMPLE_PATHS) - len(unprotected),
        "unprotected_samples": unprotected,
        "forbidden_names": sorted(FORBIDDEN_NAMES),
        "forbidden_suffixes": list(FORBIDDEN_SUFFIXES),
        "forbidden_parts": sorted(FORBIDDEN_PARTS),
        "release_exe_exception": RELEASE_EXE,
        "release_sidecar_exception": RELEASE_CORE_RUNTIME,
    }


def print_text_report(report: dict[str, Any]) -> None:
    print(f"Joi Windows Package: {str(report.get('status', 'unknown')).upper()} ({report.get('entry_count', 0)} entries)")
    if report.get("zip"):
        print(f"zip: {report['zip']}")
        print(f"sha256: {report.get('sha256', '')}")
    for error in report.get("errors", []):
        print(f"error: {error}")
    actions = report.get("next_actions") or []
    if actions:
        print("")
        print("Next actions:")
        for index, action in enumerate(actions, 1):
            print(f"{index}. {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe Windows portable release zip for Joi.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-missing-exe", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_windows_release_package(
        Path(args.workspace),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        dry_run=args.dry_run,
        require_exe=not args.allow_missing_exe,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return package_exit_code(report)


def _release_version(root: Path) -> str:
    tauri_path = root / "agent_companion" / "shell" / "src-tauri" / "tauri.conf.json"
    try:
        raw = json.loads(tauri_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "0.0.0"
    version = str(raw.get("version", "") or "").strip()
    return version or "0.0.0"


def _add_file(root: Path, relative: str, entries: list[tuple[Path, str]], errors: list[str]) -> None:
    path = root / relative
    if not path.is_file():
        errors.append(f"missing:{relative}")
        return
    _append_entry(root, path, entries, errors)


def _release_runtime_source(root: Path) -> Path:
    bundled = root / RELEASE_CORE_RUNTIME
    if _runtime_is_complete(bundled):
        return bundled
    return (
        root
        / "agent_companion"
        / "shell"
        / "src-tauri"
        / "binaries"
        / "joi-core-runtime"
    )


def _runtime_is_complete(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "joi-core.exe").is_file()
        and (path / "_internal").is_dir()
    )


def _add_runtime_tree(
    root: Path,
    source: Path,
    entries: list[tuple[Path, str]],
    errors: list[str],
) -> None:
    try:
        source.resolve().relative_to(root)
    except ValueError:
        errors.append("path_outside_workspace")
        return
    for child in source.rglob("*"):
        if child.is_file():
            relative = child.relative_to(source).as_posix()
            entries.append((child, f"{RELEASE_CORE_RUNTIME}/{relative}"))


def _add_tree(root: Path, relative: str, entries: list[tuple[Path, str]], errors: list[str]) -> None:
    path = root / relative
    if not path.is_dir():
        errors.append(f"missing:{relative}")
        return
    for child in path.rglob("*"):
        if child.is_file():
            _append_entry(root, child, entries, errors)


def _append_entry(root: Path, path: Path, entries: list[tuple[Path, str]], errors: list[str], *, allow_release_exe: bool = False) -> None:
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        errors.append("path_outside_workspace")
        return
    if not allow_release_exe and _is_forbidden(relative):
        return
    entries.append((path, relative))


def _dedupe_entries(entries: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    rows: dict[str, Path] = {}
    for source, arc in entries:
        rows[arc] = source
    return [(rows[arc], arc) for arc in sorted(rows)]


def _is_forbidden(relative: str) -> bool:
    parts = set(Path(relative).parts)
    name = Path(relative).name
    return bool(parts & FORBIDDEN_PARTS) or name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES)


def _forbidden_hits(entries: list[str]) -> list[str]:
    hits: list[str] = []
    for entry in entries:
        if entry == RELEASE_EXE or entry.startswith(f"{RELEASE_CORE_RUNTIME}/"):
            continue
        if _is_forbidden(entry):
            hits.append(entry)
    return hits


def _next_actions(errors: list[str]) -> list[str]:
    actions: list[str] = []
    if any(error == "release_exe_missing" for error in errors):
        actions.append("Build the release shell first: cd agent_companion\\shell; npm run tauri -- build")
    if any(error == "release_sidecar_missing" for error in errors):
        actions.append("Build the standalone Joi Core sidecar before packaging the Windows release.")
    if any(error.startswith("missing:") for error in errors):
        actions.append("Restore missing release inputs before packaging.")
    if any(error.startswith("forbidden_entry:") for error in errors):
        actions.append("Remove local secrets/runtime data from the package input set.")
    if any(error == "release_privacy_policy_failed" for error in errors):
        actions.append("Restore release privacy forbidden-path rules before packaging.")
    return actions


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
