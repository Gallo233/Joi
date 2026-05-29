from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

try:
    from tools.windows_release_check import build_windows_release_check_report
except ModuleNotFoundError:
    from windows_release_check import build_windows_release_check_report


HANDOFF_VERSION = "joi.windows_handoff.v1"


def build_windows_handoff_report(
    workspace: Path | str | None = None,
    *,
    include_doctor: bool = True,
    port: int = 8765,
    allow_missing_exe: bool = False,
    branch: str = "",
    commit: str = "",
) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    release_check = build_windows_release_check_report(
        root,
        include_doctor=include_doctor,
        port=port,
        allow_missing_exe=allow_missing_exe,
    )
    release_ready = bool(release_check.get("release_ready"))
    status = _handoff_status(release_check, release_ready, allow_missing_exe)
    phases = [_handoff_phase(phase) for phase in release_check.get("phases", []) if isinstance(phase, dict)]
    blocked_by = [
        phase["name"]
        for phase in phases
        if phase["required"] and phase["status"] == "fail"
    ]
    advisories = [
        phase["name"]
        for phase in phases
        if not phase["required"] and phase["status"] in {"warn", "fail"}
    ]
    metadata = _git_metadata(root, branch=branch, commit=commit)
    next_actions = _next_actions(release_check, release_ready=release_ready, allow_missing_exe=allow_missing_exe)
    return {
        "version": HANDOFF_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "release_ready": release_ready,
        "handoff_ready": status == "ok",
        "branch": metadata["branch"],
        "commit": metadata["commit"],
        "release_version": _release_version(root),
        "phases": phases,
        "blocked_by": blocked_by,
        "advisories": advisories,
        "commands": _commands(),
        "next_actions": next_actions,
    }


def windows_handoff_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    print(f"Joi Windows Handoff: {str(report.get('status', 'unknown')).upper()}")
    print(f"release ready: {'yes' if report.get('release_ready') else 'no'}")
    print(f"branch: {report.get('branch', 'unknown')}")
    print(f"commit: {report.get('commit', 'unknown')}")
    print(f"release version: {report.get('release_version', '0.0.0')}")
    for phase in report.get("phases", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(phase.get("status"), "INFO")
        required = "required" if phase.get("required") else "advisory"
        print(f"[{marker}] {phase.get('name')} ({required}): {phase.get('summary')}")
    actions = report.get("next_actions") or []
    if actions:
        print("")
        print("Next actions:")
        for index, action in enumerate(actions, 1):
            print(f"{index}. {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a safe Windows release handoff report for Joi.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-doctor", action="store_true", help="Skip local machine checks for CI or remote handoff.")
    parser.add_argument("--allow-missing-exe", action="store_true", help="Allow handoff before the release shell is built.")
    parser.add_argument("--branch", default="", help="Override branch name in environments without Git metadata.")
    parser.add_argument("--commit", default="", help="Override commit hash in environments without Git metadata.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_windows_handoff_report(
        Path(args.workspace),
        include_doctor=not args.skip_doctor,
        port=args.port,
        allow_missing_exe=args.allow_missing_exe,
        branch=args.branch,
        commit=args.commit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return windows_handoff_exit_code(report)


def _handoff_status(release_check: dict[str, Any], release_ready: bool, allow_missing_exe: bool) -> str:
    if release_check.get("status") == "fail":
        return "fail"
    if not release_ready:
        return "warn"
    if release_check.get("status") == "warn":
        return "warn"
    return "ok"


def _handoff_phase(phase: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(phase.get("name", "")),
        "status": str(phase.get("status", "fail")),
        "required": bool(phase.get("required")),
        "summary": str(phase.get("summary", ""))[:240],
    }


def _next_actions(release_check: dict[str, Any], *, release_ready: bool, allow_missing_exe: bool) -> list[str]:
    actions: list[str] = []
    if allow_missing_exe and not release_ready:
        actions.append("Build the Tauri release shell before publishing the Windows portable package.")
    for action in release_check.get("next_actions") or []:
        text = str(action)
        if text and text not in actions:
            actions.append(text)
    return actions[:10]


def _commands() -> list[str]:
    return [
        r"start_joi.bat -Setup",
        r"start_joi.bat -Doctor",
        r".venv\Scripts\python.exe tools\windows_release_check.py --allow-missing-exe",
        r"cd agent_companion\shell && npm run tauri -- build",
        r".venv\Scripts\python.exe tools\package_windows_release.py",
    ]


def _git_metadata(root: Path, *, branch: str, commit: str) -> dict[str, str]:
    safe_branch = _safe_metadata(branch) or _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    safe_commit = _safe_metadata(commit) or _run_git(root, "rev-parse", "--short", "HEAD")
    return {
        "branch": safe_branch or "unknown",
        "commit": safe_commit or "unknown",
    }


def _run_git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return _safe_metadata(result.stdout)


def _safe_metadata(value: str) -> str:
    text = str(value or "").strip().splitlines()[0][:80] if value else ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    return text if text and all(char in allowed for char in text) else ""


def _release_version(root: Path) -> str:
    tauri_path = root / "agent_companion" / "shell" / "src-tauri" / "tauri.conf.json"
    try:
        raw = json.loads(tauri_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "0.0.0"
    version = str(raw.get("version", "") or "").strip()
    return version or "0.0.0"


if __name__ == "__main__":
    raise SystemExit(main())
